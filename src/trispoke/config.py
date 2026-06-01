from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Make `.env` values visible via os.environ for any caller (push worker, etc.),
# not only via the Settings model. Pydantic-settings reads `.env` into the
# model but doesn't pollute os.environ on its own.
load_dotenv(override=False)


class Settings(BaseSettings):
    apollo_api_key: str | None = None
    # V1.5: Apollo-out controls
    apollo_poll_interval_seconds: int = 900
    apollo_max_enrollments_per_minute: int = 25
    apollo_default_sequence_mailbox_ids: list[str] = []
    # Full-automation controls
    bounce_pause_threshold: float = 0.03  # pause when bounce rate over last 100 sends > 3%
    bounce_pause_min_sends: int = 20  # need at least this many recent sends to evaluate
    reply_webhook_url: str | None = None  # POSTed JSON on positive replies (Slack/Zapier/etc)
    anthropic_api_key: str | None = None
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"

    # Abacus.AI RouteLLM (OpenAI-compatible). All optional — when api_key is
    # None, abacus-using modes raise a clear config error at invocation time.
    abacus_api_key: str | None = None
    abacus_base_url: str = "https://routellm.abacus.ai/v1"
    abacus_model: str = "route-llm"
    # V1.5: SMTP/IMAP are now OPTIONAL — only required when a campaign
    # opts into the legacy mode='smtp_direct' sending path. Apollo-mode
    # campaigns ignore these entirely.
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_username: str | None = None
    imap_password: str | None = None
    daily_cap_per_inbox: int = 20

    # Supabase Auth (GoTrue) — email + password sign-in. Required for the
    # Streamlit login screen. supabase_anon_key accepts BOTH the legacy JWT
    # (eyJ...) and modern publishable key (sb_publishable_...) formats. Leave
    # both blank to keep auth disabled and run the UI with no login screen
    # (dev-only open access).
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    # Service-role secret (Supabase → Project Settings → API → service_role).
    # Server-side only — never exposed to the browser. Required for admin
    # password resets and for self-signup (admin.create_user). Leave blank to
    # disable those privileged operations.
    supabase_service_role_key: str | None = None

    # DB: prefer DATABASE_URL when set (Postgres in Docker prod);
    # fall back to a SQLite file at database_path for bare-metal dev.
    database_url: str | None = None
    database_path: str = "./data/trispoke.db"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 3600

    class Config:
        env_file = ".env"
        # Allow Docker/orchestration vars (POSTGRES_*, etc.) in .env
        # without declaring them on the app's Settings class.
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()