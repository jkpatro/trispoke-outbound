"""LLM router — picks the engine for a draft based on the campaign's mode.

Five modes:
  local_only    Always use Ollama.
  claude_only   Always use Claude direct.
  abacus_only   Always use Abacus.AI RouteLLM (requires ABACUS_API_KEY).
  hybrid        LEGACY. Claude → Local. Preserved so campaigns saved before
                Abacus support keep working without migration.
  hybrid_smart  RECOMMENDED. Claude → Abacus → Local. Each step falls through
                on transient failure (auth, rate-limit, network).

`EmailDraft.model_used` is stored as `provider:model_name` (e.g.
`local:qwen3:8b`, `claude:claude-sonnet-4-6`, `abacus:route-llm`) so that
analytics and the UI badge can disambiguate provider without parsing model
strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from trispoke.db.models import Lead
from trispoke.llm.claude_client import ClaudeClient
from trispoke.llm.ollama_client import OllamaClient
from trispoke.pain_analyzer import PainTriple

PROMPTS_DIR = Path(__file__).parent / "prompts"
_jinja_env = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
    keep_trailing_newline=True,
)

Mode = Literal[
    "local_only", "claude_only", "abacus_only", "hybrid", "hybrid_smart"
]
EnhanceMode = Literal["local_only", "claude_only", "abacus_only"]


@dataclass
class EmailDraft:
    subject: str
    body: str
    model_used: str  # "provider:model_name"
    tokens_used: int
    generation_seconds: float


class LLMRouter:
    def __init__(self) -> None:
        self.ollama = OllamaClient()
        self._claude: Optional[ClaudeClient] = None
        self._abacus = None  # type: ignore[var-annotated]

    # ---------- lazy engine accessors ----------

    def _get_claude(self) -> Optional[ClaudeClient]:
        if self._claude is None:
            try:
                self._claude = ClaudeClient()
            except Exception:
                self._claude = None
        return self._claude

    def _get_abacus(self):
        if self._abacus is None:
            try:
                from trispoke.llm.abacus_client import AbacusClient

                self._abacus = AbacusClient()
            except Exception:
                self._abacus = None
        return self._abacus

    # ---------- generate ----------

    def generate_email(
        self,
        lead: Lead,
        pain: PainTriple,
        mode: Mode,
        *,
        is_follow_up: bool = False,
        sender_first_name: str = "Alex",
    ) -> EmailDraft:
        if is_follow_up:
            return self._render_follow_up(lead, sender_first_name)

        from trispoke.llm.prompts.cold_email import generate_prompt

        prompt = generate_prompt(lead, pain)

        if mode == "local_only":
            return self._local(prompt)
        if mode == "claude_only":
            return self._claude_or_raise(prompt)
        if mode == "abacus_only":
            return self._abacus_or_raise(prompt)
        if mode == "hybrid":
            # LEGACY: Claude → Local
            draft = self._try_claude(prompt)
            return draft if draft is not None else self._local(prompt)
        if mode == "hybrid_smart":
            return self._hybrid_smart(prompt)
        raise ValueError(f"Unknown mode: {mode}")

    # ---------- engine helpers (return None on failure, raise only when forced) ----------

    def _local(self, prompt: str, temperature: float = 0.7) -> EmailDraft:
        text, tokens, elapsed = self.ollama.generate(prompt, temperature=temperature)
        return EmailDraft(
            subject=self._extract_subject(text),
            body=self._extract_body(text),
            model_used=f"local:{self.ollama.default_model}",
            tokens_used=tokens,
            generation_seconds=elapsed,
        )

    def _try_claude(
        self, prompt: str, temperature: float = 0.7
    ) -> Optional[EmailDraft]:
        claude = self._get_claude()
        if claude is None:
            return None
        try:
            text, tokens, elapsed = claude.generate(prompt, temperature=temperature)
        except Exception:
            return None
        return EmailDraft(
            subject=self._extract_subject(text),
            body=self._extract_body(text),
            model_used="claude:claude-sonnet-4-6",
            tokens_used=tokens or 0,
            generation_seconds=elapsed,
        )

    def _claude_or_raise(self, prompt: str, temperature: float = 0.7) -> EmailDraft:
        claude = self._get_claude()
        if claude is None:
            raise Exception("Claude client not available")
        text, tokens, elapsed = claude.generate(prompt, temperature=temperature)
        return EmailDraft(
            subject=self._extract_subject(text),
            body=self._extract_body(text),
            model_used="claude:claude-sonnet-4-6",
            tokens_used=tokens or 0,
            generation_seconds=elapsed,
        )

    def _try_abacus(
        self, prompt: str, temperature: float = 0.7
    ) -> Optional[EmailDraft]:
        abacus = self._get_abacus()
        if abacus is None:
            return None
        try:
            text, tokens, elapsed = abacus.generate(prompt, temperature=temperature)
        except Exception:
            return None
        return EmailDraft(
            subject=self._extract_subject(text),
            body=self._extract_body(text),
            model_used=f"abacus:{abacus.default_model}",
            tokens_used=tokens,
            generation_seconds=elapsed,
        )

    def _abacus_or_raise(self, prompt: str, temperature: float = 0.7) -> EmailDraft:
        abacus = self._get_abacus()
        if abacus is None:
            raise Exception(
                "Abacus client not available — set ABACUS_API_KEY in .env "
                "(ChatLLM Teams subscription required)"
            )
        text, tokens, elapsed = abacus.generate(prompt, temperature=temperature)
        return EmailDraft(
            subject=self._extract_subject(text),
            body=self._extract_body(text),
            model_used=f"abacus:{abacus.default_model}",
            tokens_used=tokens,
            generation_seconds=elapsed,
        )

    def _hybrid_smart(self, prompt: str) -> EmailDraft:
        draft = self._try_claude(prompt)
        if draft is not None:
            return draft
        draft = self._try_abacus(prompt)
        if draft is not None:
            return draft
        return self._local(prompt)

    # ---------- enhance ----------

    def enhance_email(
        self,
        current_subject: str,
        current_body: str,
        instruction: str,
        mode: EnhanceMode,
        *,
        style: Literal["few_shot", "thinking"] = "few_shot",
    ) -> EmailDraft:
        """Rewrite an existing draft per a reviewer's instruction.

        style="few_shot"  → short examples of good edits, low-temperature
        style="thinking"  → asks the model to reason briefly before rewriting
        """
        prompt = self._build_enhance_prompt(
            current_subject, current_body, instruction, style
        )

        if mode == "local_only":
            text, tokens, elapsed = self.ollama.generate(prompt, temperature=0.6)
            return EmailDraft(
                subject=self._extract_subject(text) or current_subject,
                body=self._extract_body(text) or current_body,
                model_used=f"local:{self.ollama.default_model}+enhance:{style}",
                tokens_used=tokens,
                generation_seconds=elapsed,
            )

        if mode == "claude_only":
            claude = self._get_claude()
            if not claude:
                raise Exception("Claude client not available")
            text, tokens, elapsed = claude.generate(prompt, temperature=0.6)
            return EmailDraft(
                subject=self._extract_subject(text) or current_subject,
                body=self._extract_body(text) or current_body,
                model_used=f"claude:claude-sonnet-4-6+enhance:{style}",
                tokens_used=tokens or 0,
                generation_seconds=elapsed,
            )

        if mode == "abacus_only":
            abacus = self._get_abacus()
            if not abacus:
                raise Exception(
                    "Abacus client not available — set ABACUS_API_KEY in .env"
                )
            text, tokens, elapsed = abacus.generate(prompt, temperature=0.6)
            return EmailDraft(
                subject=self._extract_subject(text) or current_subject,
                body=self._extract_body(text) or current_body,
                model_used=f"abacus:{abacus.default_model}+enhance:{style}",
                tokens_used=tokens,
                generation_seconds=elapsed,
            )

        raise ValueError(f"Unsupported enhance mode: {mode}")

    # ---------- follow-up / prompt building / extraction (unchanged behaviour) ----------

    def _render_follow_up(self, lead: Lead, sender_first_name: str) -> EmailDraft:
        template = _jinja_env.get_template("follow_up.j2")
        rendered = template.render(
            lead=lead, sender_first_name=sender_first_name
        ).strip()
        first_line, _, rest = rendered.partition("\n")
        subject = (
            first_line.split(":", 1)[1].strip()
            if first_line.lower().startswith("subject:")
            else first_line
        )
        return EmailDraft(
            subject=subject,
            body=rest.strip(),
            model_used="template:follow_up.j2",
            tokens_used=0,
            generation_seconds=0.0,
        )

    def _build_enhance_prompt(
        self, subject: str, body: str, instruction: str, style: str
    ) -> str:
        examples = (
            "EXAMPLES OF GOOD EDITS:\n\n"
            "Instruction: shorter and more concrete\n"
            "Before body: We help organizations leverage automation to streamline operations and drive efficiency at scale.\n"
            "After body:  We've cut three logistics firms' dispatch time by 30% in eight weeks.\n\n"
            "Instruction: drop the sales pitch, lead with curiosity\n"
            "Before body: Our platform solves these problems and saves you money.\n"
            "After body:  Curious how you're handling this today — are you tracking it weekly, or is it more reactive?\n\n"
        )
        thinking_preamble = (
            "First, read the reviewer's instruction carefully. In one short paragraph "
            "(no more than three sentences) decide what concrete change the instruction "
            "implies. Then rewrite the email. Output ONLY the final email in the format "
            "below — no preamble, no explanation, no markdown headers.\n\n"
        )
        rules = (
            "RULES:\n"
            "- Keep the email plain text (no HTML, no markdown).\n"
            "- Subject line: lowercase, specific, no marketing words.\n"
            "- Body: 80-130 words, end with first-name sign-off.\n"
            "- NO: 'leverage', 'synergy', 'circle back', 'touch base', em-dashes.\n\n"
        )
        body_block = (
            f"CURRENT EMAIL:\n"
            f"Subject: {subject}\n\n"
            f"{body}\n\n"
            f"REVIEWER INSTRUCTION:\n{instruction.strip()}\n\n"
        )
        output_block = (
            "OUTPUT THE REVISED EMAIL EXACTLY IN THIS FORMAT:\n"
            "Subject: ...\n\nDear ...,\n\n...body...\n\nBest,\nFirst\n\nSubject:"
        )
        if style == "thinking":
            return thinking_preamble + rules + body_block + output_block
        return examples + rules + body_block + output_block

    def _extract_subject(self, text: str) -> str:
        lines = text.strip().split("\n")
        for line in lines:
            if line.lower().startswith("subject:"):
                return line.split(":", 1)[1].strip()
        return lines[0].strip() if lines else ""

    def _extract_body(self, text: str) -> str:
        lines = text.strip().split("\n")
        body_lines: list[str] = []
        in_body = False
        for line in lines:
            if line.lower().startswith("subject:"):
                continue
            if line.lower().startswith("body:") or line.lower().startswith("dear"):
                in_body = True
                if line.lower().startswith("body:"):
                    continue
            if in_body:
                body_lines.append(line)
        return "\n".join(body_lines).strip()
