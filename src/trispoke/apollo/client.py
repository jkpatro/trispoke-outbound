import httpx
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from trispoke.config import get_settings

settings = get_settings()

class ApolloClient:
    def __init__(self):
        self.base_url = "https://api.apollo.io/api/v1"
        self.api_key = settings.apollo_api_key
        self.client = httpx.Client(
            headers={"x-api-key": self.api_key},
            timeout=30.0
        )

    def _retry_request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Retry with exponential backoff on 429"""
        for attempt in range(4):  # 0,1,2,3
            try:
                response = self.client.request(method, url, **kwargs)
                if response.status_code == 429:
                    if attempt < 3:
                        wait_time = 2 ** attempt  # 1,2,4
                        time.sleep(wait_time)
                        continue
                    else:
                        raise Exception("Rate limit exceeded after retries")
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < 3:
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
                    continue
                raise

    def match_person(
        self,
        linkedin_url: Optional[str] = None,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        organization_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Match a person using Apollo API"""
        data = {
            "linkedin_url": linkedin_url,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "organization_name": organization_name
        }
        # Remove None values
        data = {k: v for k, v in data.items() if v is not None}

        if not data:
            return None

        response = self._retry_request(
            "POST",
            f"{self.base_url}/people/match",
            json=data
        )
        return response.json().get("person")

    def enrich_organization(self, domain: str) -> Optional[Dict[str, Any]]:
        """Enrich organization data"""
        data = {"domain": domain}
        response = self._retry_request(
            "POST",
            f"{self.base_url}/organizations/enrich",
            json=data
        )
        return response.json().get("organization")

    def bulk_match(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Bulk match people, batching in groups of 10"""
        results = []
        for i in range(0, len(records), 10):
            batch = records[i:i+10]
            response = self._retry_request(
                "POST",
                f"{self.base_url}/people/bulk_match",
                json={"people": batch}
            )
            batch_results = response.json().get("people", [])
            results.extend(batch_results)
        return results

    # ------------------------------------------------------------------
    # V1.5 — sequence + contact + events API
    # ------------------------------------------------------------------
    #
    # These methods drive the "Apollo as sink" half of the architecture:
    # contacts that V1 enriches get pushed into Apollo sequences, and the
    # events poller reads send/open/reply events back through here.
    #
    # All methods reuse `_retry_request` so they inherit the 429
    # exponential-backoff behaviour already in this client.

    def create_or_update_contact(self, lead: Any) -> Dict[str, Any]:
        """Upsert a Lead into Apollo as a Contact.

        Apollo's /contacts endpoint will create-or-merge by email when the
        contact already exists in the workspace. Returns the full contact
        dict from Apollo (with `id`, `email`, etc.).

        Raises if Apollo returns no contact payload.
        """
        payload = {
            "first_name": _attr(lead, "first_name"),
            "last_name": _attr(lead, "last_name"),
            "email": _attr(lead, "email"),
            "title": _attr(lead, "title"),
            "organization_name": _attr(lead, "company_name"),
            "website_url": _attr(lead, "company_domain"),
            "linkedin_url": _attr(lead, "linkedin_url"),
        }
        payload = {k: v for k, v in payload.items() if v}

        response = self._retry_request(
            "POST", f"{self.base_url}/contacts", json=payload
        )
        body = response.json()
        contact = body.get("contact") or body.get("contacts", [None])[0]
        if not contact or not contact.get("id"):
            raise RuntimeError(
                f"Apollo did not return a contact payload: {body!r}"
            )
        return contact

    def search_contacts(
        self, query: Dict[str, Any], page: int = 1, page_size: int = 25
    ) -> Dict[str, Any]:
        """People-search by criteria.

        Uses /mixed_people/api_search — the legacy /mixed_people/search is
        deprecated for API callers (returns 422 with a deprecation notice).

        `query` keys Apollo accepts:
          person_titles                       list[str]
          person_locations                    list[str]
          organization_num_employees_ranges   list[str] — each as "min,max"
          q_keywords                          str
          q_organization_name                 str
        """
        payload = {**query, "page": page, "per_page": page_size}
        response = self._retry_request(
            "POST",
            f"{self.base_url}/mixed_people/api_search",
            json=payload,
        )
        return response.json()

    def list_mailboxes(self) -> List[Dict[str, Any]]:
        """Return the mailboxes the Apollo workspace has connected.

        Each mailbox dict carries `id`, `email`, `provider`, `active`. The
        push worker uses these IDs as `mailbox_id` when creating sequences.
        """
        response = self._retry_request(
            "GET", f"{self.base_url}/email_accounts"
        )
        body = response.json()
        return body.get("email_accounts") or body.get("mailboxes") or []

    def create_sequence(
        self, name: str, step_template: Dict[str, Any],
        follow_up_template: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a fully-wired Apollo sequence with optional follow-up step.

        Apollo data model: `sequence → step → touch → template`. A sequence
        won't fire until each touch has a populated template, every touch is
        approved, and a schedule is attached. This method does all of that.

        It does NOT activate the sequence (Apollo silently ignores
        `active: true` on PUT for this API tier — activation must be done
        manually in the Apollo UI, one time).

        `step_template` provides initial step: subject, body_html, body_text,
        wait_days_after, mailbox_id, schedule_id.

        `follow_up_template` (optional) provides a second step: subject,
        body_html, body_text, wait_days_after (default 4).

        Returns:
          {
            "sequence_id":  str, "step_id": str, "touch_id": str,
            "template_id":  str, "schedule_id": str, "mailbox_id": str | None,
            # follow_up_* keys present only when follow_up_template was provided
            "followup_step_id":     str | None,
            "followup_touch_id":    str | None,
            "followup_template_id": str | None,
          }
        """
        # 1. sequence shell
        seq_resp = self._retry_request(
            "POST",
            f"{self.base_url}/emailer_campaigns",
            json={"name": name, "permissions": "team_can_use"},
        )
        seq = seq_resp.json().get("emailer_campaign") or seq_resp.json()
        sequence_id = seq.get("id")
        if not sequence_id:
            raise RuntimeError(
                f"Apollo did not return a sequence id: {seq_resp.json()!r}"
            )

        # 2. step — Apollo auto-creates an empty touch+template alongside
        step_payload = {
            "emailer_campaign_id": sequence_id,
            "position": 1,
            "type": "auto_email",
            "wait_time": int(step_template.get("wait_days_after", 0)),
            "wait_mode": "day",
        }
        step_resp = self._retry_request(
            "POST", f"{self.base_url}/emailer_steps", json=step_payload
        )
        step = step_resp.json().get("emailer_step") or step_resp.json()
        step_id = step.get("id")
        if not step_id:
            raise RuntimeError(
                f"Apollo did not return a step id: {step_resp.json()!r}"
            )

        # 3. discover the auto-created touch + template for this step
        touch_id, template_id = self._find_touch_and_template_for_step(
            sequence_id, step_id
        )

        # 4. populate the template
        self.update_template(
            template_id,
            subject=step_template.get("subject", ""),
            body_html=step_template.get("body_html", ""),
            body_text=step_template.get("body_text", ""),
        )

        # 5. approve the touch — required before activation will succeed
        self._retry_request(
            "POST", f"{self.base_url}/emailer_touches/{touch_id}/approve"
        )

        # 6. attach schedule (default = workspace default if not supplied)
        schedule_id = step_template.get("schedule_id") or self._default_schedule_id()
        update_body: Dict[str, Any] = {}
        if schedule_id:
            update_body["emailer_schedule_id"] = schedule_id
        if update_body:
            self._retry_request(
                "PUT",
                f"{self.base_url}/emailer_campaigns/{sequence_id}",
                json=update_body,
            )

        out: Dict[str, Any] = {
            "sequence_id": sequence_id,
            "step_id": step_id,
            "touch_id": touch_id,
            "template_id": template_id,
            "schedule_id": schedule_id,
            "mailbox_id": step_template.get("mailbox_id"),
            "followup_step_id": None,
            "followup_touch_id": None,
            "followup_template_id": None,
        }

        # 7. (optional) Add a follow-up step.
        if follow_up_template:
            fu = self.add_followup_step(sequence_id, follow_up_template)
            out["followup_step_id"] = fu["step_id"]
            out["followup_touch_id"] = fu["touch_id"]
            out["followup_template_id"] = fu["template_id"]

        return out

    def add_followup_step(
        self,
        sequence_id: str,
        template: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Append a follow-up step to an existing sequence.

        Used both at sequence creation time (via create_sequence) and to
        retroactively upgrade single-step slots. Apollo accepts new steps
        on active sequences — no need to deactivate first.

        `template` provides: subject, body_html, body_text, wait_days_after.

        Returns:
          {"step_id": str, "touch_id": str, "template_id": str}
        """
        step_payload = {
            "emailer_campaign_id": sequence_id,
            "position": 2,
            "type": "auto_email",
            "wait_time": int(template.get("wait_days_after", 4)),
            "wait_mode": "day",
        }
        step_resp = self._retry_request(
            "POST", f"{self.base_url}/emailer_steps", json=step_payload
        )
        step = step_resp.json().get("emailer_step") or step_resp.json()
        step_id = step.get("id")
        if not step_id:
            raise RuntimeError(
                f"Apollo did not return a follow-up step id: {step_resp.json()!r}"
            )

        touch_id, template_id = self._find_touch_and_template_for_step(
            sequence_id, step_id
        )
        self.update_template(
            template_id,
            subject=template.get("subject", ""),
            body_html=template.get("body_html", ""),
            body_text=template.get("body_text", ""),
        )
        self._retry_request(
            "POST", f"{self.base_url}/emailer_touches/{touch_id}/approve"
        )
        return {
            "step_id": step_id,
            "touch_id": touch_id,
            "template_id": template_id,
        }

    def update_template(
        self,
        template_id: str,
        subject: str = "",
        body_html: str = "",
        body_text: str = "",
    ) -> Dict[str, Any]:
        """PUT subject/body on an emailer_template. Apollo reads this LIVE
        at send time, so callers must coordinate timing (slot recycling)."""
        payload = {
            "subject": subject,
            "body_html": body_html,
            "body_text": body_text,
        }
        response = self._retry_request(
            "PUT", f"{self.base_url}/emailer_templates/{template_id}", json=payload
        )
        body = response.json()
        return body.get("emailer_template", body)

    def send_message_now(self, message_id: str) -> Dict[str, Any]:
        """Bypass the sequence's schedule and fire this queued message now."""
        response = self._retry_request(
            "POST", f"{self.base_url}/emailer_messages/{message_id}/send_now"
        )
        body = response.json()
        return body.get("emailer_message", body) if isinstance(body, dict) else {}

    def find_message_for_enrollment(
        self, sequence_id: str, contact_id: str
    ) -> Optional[str]:
        """Locate the emailer_message Apollo created for a fresh enrollment.

        Used by the push worker immediately after add_contact_ids returns.
        Falls back to scanning the sequence's queue if no direct filter
        exists.
        """
        # Search supports filtering by sequence; we filter contact in code
        # because Apollo's filter params vary by API tier.
        body = self._search_messages(sequence_id)
        messages = body.get("emailer_messages", [])
        for m in messages:
            if m.get("contact_id") == contact_id:
                return m.get("id")
        return None

    def list_emailer_schedules(self) -> List[Dict[str, Any]]:
        response = self._retry_request("GET", f"{self.base_url}/emailer_schedules")
        body = response.json()
        return body.get("emailer_schedules", body.get("schedules", []))

    # ---------- private helpers ----------

    def _default_schedule_id(self) -> Optional[str]:
        try:
            scheds = self.list_emailer_schedules()
        except Exception:
            return None
        for s in scheds:
            if s.get("default"):
                return s.get("id")
        return scheds[0].get("id") if scheds else None

    def _find_touch_and_template_for_step(
        self, sequence_id: str, step_id: str
    ) -> tuple[str, str]:
        """When you POST /emailer_steps, Apollo auto-creates a touch +
        template for it. The IDs aren't in the step response — you have to
        fetch the sequence and look them up."""
        # The sequence GET returns the full graph (steps → touches).
        response = self._retry_request(
            "GET", f"{self.base_url}/emailer_campaigns/{sequence_id}"
        )
        body = response.json()
        # Apollo exposes touches at top level of the campaign response.
        touches = body.get("emailer_touches", [])
        for t in touches:
            if t.get("emailer_step_id") == step_id:
                tid = t.get("id")
                tmpl_id = t.get("emailer_template_id")
                if tid and tmpl_id:
                    return tid, tmpl_id
        raise RuntimeError(
            f"Apollo did not auto-create a touch+template for step {step_id}"
        )

    def _search_messages(self, sequence_id: str) -> Dict[str, Any]:
        """The working endpoint for listing sequence messages is /search
        — the plain /emailer_messages?emailer_campaign_id=... 404s."""
        response = self._retry_request(
            "GET",
            f"{self.base_url}/emailer_messages/search",
            params={"emailer_campaign_id": sequence_id},
        )
        return response.json()

    def enroll_contact_in_sequence(
        self,
        sequence_id: str,
        contact_id: str,
        send_at: Optional[datetime] = None,
        mailbox_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a contact to a sequence. Optional `send_at` delays enrollment.

        Apollo's endpoint requires `send_email_from_email_account_id` to
        identify which connected mailbox should send — it 422s without it.
        When `mailbox_id` is omitted, falls back to the first active mailbox.
        """
        if mailbox_id is None:
            for mb in self.list_mailboxes():
                if mb.get("active") is not False and mb.get("id"):
                    mailbox_id = mb["id"]
                    break
        if not mailbox_id:
            raise RuntimeError(
                "No mailbox available for enrollment — connect at least one in Apollo."
            )

        payload: Dict[str, Any] = {
            "contact_ids": [contact_id],
            "send_email_from_email_account_id": mailbox_id,
            "emailer_campaign_id": sequence_id,
        }
        if send_at is not None:
            # Apollo expects ISO-8601 with timezone.
            payload["send_at"] = (
                send_at.isoformat()
                if send_at.tzinfo
                else send_at.isoformat() + "Z"
            )
        response = self._retry_request(
            "POST",
            f"{self.base_url}/emailer_campaigns/{sequence_id}/add_contact_ids",
            json=payload,
        )
        return response.json()

    def get_sequence_events(
        self, sequence_id: str, since: datetime
    ) -> List[Dict[str, Any]]:
        """Fetch all message events for a sequence since `since`.

        Uses /emailer_messages/search — the plain /emailer_messages
        endpoint 404s on this Apollo plan. Apollo's search endpoint does
        not natively filter by `updated_after`; we paginate and filter in
        code so the events poller still gets a useful diff each cycle.
        """
        since_naive = since.replace(tzinfo=None) if since.tzinfo else since
        body = self._search_messages(sequence_id)
        messages = body.get("emailer_messages", [])
        # Filter to records touched since `since`. Apollo records carry
        # several timestamps — use the freshest non-null one.
        out: List[Dict[str, Any]] = []
        for m in messages:
            ts = _newest_timestamp(m)
            if ts is None or ts >= since_naive:
                out.append(m)
        return out

    def get_message_body(self, message_id: str) -> Dict[str, Any]:
        """Fetch a single emailer_message by id (used to retrieve reply bodies)."""
        response = self._retry_request(
            "GET", f"{self.base_url}/emailer_messages/{message_id}"
        )
        body = response.json()
        return body.get("emailer_message", body)


def _attr(obj: Any, name: str) -> Any:
    """Read an attribute that may be on a SQLAlchemy model or a dict."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _newest_timestamp(m: Dict[str, Any]) -> Optional[datetime]:
    """Pick the most recent timestamp on an emailer_message — used by the
    events poller to cursor forward."""
    for key in (
        "unsubscribed_at",
        "bounced_at",
        "replied_at",
        "opened_at",
        "completed_at",
        "sent_at",
        "due_at",
        "created_at",
    ):
        v = m.get(key)
        if not v:
            continue
        try:
            s = v.replace("Z", "+00:00") if isinstance(v, str) else v
            dt = datetime.fromisoformat(s) if isinstance(s, str) else s
            return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt
        except (TypeError, ValueError):
            continue
    return None