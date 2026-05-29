"""V1.5.2 pipeline runner — closes the auto-pipeline gap.

Apollo-search and manual-form intake paths insert leads with `status='new'`
and rely on someone manually triggering enrich → pain → generate → QC.
This daemon does that automatically: polls for `status='new'` leads and
runs the full pipeline per campaign mode.

Runs every PIPELINE_POLL_INTERVAL_SECONDS (default 120).

Stops cleanly on SIGINT.

Per-lead failures are isolated; one bad enrichment never blocks the batch.
"""

from __future__ import annotations

import json
import signal
import sys
import time
from datetime import datetime
from typing import Optional

from trispoke.apollo.client import ApolloClient
from trispoke.config import get_settings
from trispoke.db.event_log import log_event
from trispoke.db.models import (
    Campaign,
    Email,
    Lead,
    LeadStatus,
    PainAnalysis,
)
from trispoke.db.session import get_session
from trispoke.llm.qc_checker import check_email as qc_check
from trispoke.llm.qc_checker import flags_to_json
from trispoke.llm.router import LLMRouter
from trispoke.pain_analyzer import analyze


BATCH_SIZE = 20
POLL_INTERVAL_SECONDS = 120


class PipelineRunner:
    def __init__(self) -> None:
        self.running = True
        self.settings = get_settings()
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, sig, frame):
        print("\nPipeline runner shutting down…")
        self.running = False
        sys.exit(0)

    def run_once(self) -> dict[str, int]:
        """One pass through all status='new' leads. Returns counts by phase."""
        counts = {"enriched": 0, "generated": 0, "qc_flagged": 0, "qc_passed": 0,
                  "skipped": 0, "errored": 0}

        with get_session() as session:
            new_leads = (
                session.query(Lead)
                .filter(Lead.status == LeadStatus.new.value)
                .limit(BATCH_SIZE)
                .all()
            )
            if not new_leads:
                return counts

            # Group by campaign to share per-campaign clients
            by_campaign: dict[int, list[Lead]] = {}
            for lead in new_leads:
                by_campaign.setdefault(lead.campaign_id, []).append(lead)

            for campaign_id, leads in by_campaign.items():
                if not self.running:
                    break
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    continue

                # Cache clients within a campaign batch
                apollo: Optional[ApolloClient] = None
                router: Optional[LLMRouter] = None

                for lead in leads:
                    if not self.running:
                        break
                    try:
                        # Lazy init — only pay for clients if we have leads to process
                        if apollo is None:
                            apollo = ApolloClient()
                        if router is None:
                            router = LLMRouter()

                        ok = self._process_one(
                            session, campaign, lead, apollo, router, counts
                        )
                        if not ok:
                            counts["skipped"] += 1
                    except Exception as e:
                        print(f"[pipeline] lead {lead.id} ({lead.email}): {e}")
                        counts["errored"] += 1
                        # Don't leave lead in pending state — bounce back to 'new'
                        # so the next poll cycle retries.
                        try:
                            lead.status = LeadStatus.new.value
                            session.commit()
                        except Exception:
                            session.rollback()

        return counts

    def run(self) -> None:
        print(
            f"Pipeline runner started (every {POLL_INTERVAL_SECONDS}s). "
            "Ctrl+C to stop."
        )
        while self.running:
            try:
                counts = self.run_once()
                non_zero = {k: v for k, v in counts.items() if v}
                if non_zero:
                    print(f"[pipeline] {non_zero}")
                time.sleep(POLL_INTERVAL_SECONDS)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[pipeline] loop error: {e}")
                time.sleep(POLL_INTERVAL_SECONDS)

    # ---------- per-lead pipeline ----------

    def _process_one(
        self,
        session,
        campaign: Campaign,
        lead: Lead,
        apollo: ApolloClient,
        router: LLMRouter,
        counts: dict[str, int],
    ) -> bool:
        # 1. ENRICH — Apollo /organizations/enrich when we have a domain
        enrichment_data: dict[str, str] = {}
        if lead.company_domain:
            try:
                org = apollo.enrich_organization(lead.company_domain)
            except Exception as e:
                print(f"[pipeline] enrich failed for {lead.email}: {e}")
                org = None
            if org:
                if not lead.company_name and org.get("name"):
                    enrichment_data["company_name"] = org["name"]
                if not lead.company_size and org.get("estimated_num_employees"):
                    enrichment_data["company_size"] = str(org["estimated_num_employees"])
                if not lead.company_industry and org.get("industry"):
                    enrichment_data["company_industry"] = org["industry"]
                if not lead.company_location and org.get("city"):
                    loc = (
                        f"{org.get('city','')}, {org.get('state','')}"
                    ).strip(", ")
                    if loc:
                        enrichment_data["company_location"] = loc

        for k, v in enrichment_data.items():
            setattr(lead, k, v)
        lead.status = LeadStatus.enriched.value
        session.commit()
        counts["enriched"] += 1
        if enrichment_data:
            log_event(session, lead.id, "apollo_enriched", {
                "enrichment_data": enrichment_data,
                "campaign": campaign.name,
            })

        # 2. PAIN ANALYSIS
        pain = analyze(lead)
        existing_pain = (
            session.query(PainAnalysis).filter_by(lead_id=lead.id).first()
        )
        if existing_pain is None:
            session.add(PainAnalysis(
                lead_id=lead.id,
                chronic=pain.chronic, acute=pain.acute, trigger=pain.trigger,
                confidence=pain.confidence,
            ))
            session.commit()
        log_event(session, lead.id, "pain_analyzed", {
            "pain": {
                "chronic": pain.chronic, "acute": pain.acute,
                "trigger": pain.trigger, "confidence": pain.confidence,
            },
            "campaign": campaign.name,
        })

        # 3. GENERATE — uses campaign's mode (local_only / claude_only /
        # abacus_only / hybrid / hybrid_smart)
        mode = campaign.mode or "hybrid_smart"
        try:
            draft = router.generate_email(lead, pain, mode)  # type: ignore[arg-type]
        except Exception as e:
            print(f"[pipeline] generate failed for {lead.email} in mode={mode}: {e}")
            # Roll back to 'new' so we don't leave in 'enriched' limbo
            lead.status = LeadStatus.new.value
            session.commit()
            return False

        email = Email(
            lead_id=lead.id, campaign_id=campaign.id,
            subject=draft.subject, body=draft.body,
            model_used=draft.model_used,
            tokens_used=draft.tokens_used,
            generation_seconds=draft.generation_seconds,
        )
        session.add(email)
        session.commit()
        counts["generated"] += 1
        log_event(session, lead.id, "email_generated", {
            "email_id": email.id,
            "model_used": draft.model_used,
            "tokens_used": draft.tokens_used,
            "generation_seconds": draft.generation_seconds,
            "campaign": campaign.name,
        })

        # 4. QC PASS — Ollama runs both structural + semantic checks
        lead.status = LeadStatus.qc_pending.value
        session.commit()
        try:
            result = qc_check(lead, pain, email)
        except Exception as e:
            print(f"[pipeline] qc failed for {lead.email}: {e}")
            # On QC failure, drop to 'drafted' so it still reaches review
            lead.status = LeadStatus.drafted.value
            session.commit()
            return True

        email.qc_status = result.status
        email.qc_checked_at = datetime.utcnow()
        email.qc_flags_json = flags_to_json(result.flags)
        email.qc_model_used = result.model_used
        if result.status == "flagged":
            lead.status = LeadStatus.qc_flagged.value
            event_type = "qc_flagged"
            counts["qc_flagged"] += 1
        else:
            lead.status = LeadStatus.drafted.value
            event_type = "qc_passed"
            counts["qc_passed"] += 1
        session.commit()
        log_event(session, lead.id, event_type, {
            "email_id": email.id,
            "model_used": result.model_used,
            "elapsed_seconds": result.elapsed_seconds,
            "flags": json.loads(email.qc_flags_json or "[]"),
            "campaign": campaign.name,
        })
        return True


def main() -> None:
    PipelineRunner().run()


if __name__ == "__main__":
    main()
