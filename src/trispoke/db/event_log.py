from sqlalchemy.orm import Session
import json
from trispoke.db.models import Event, EventType

def log_event(session: Session, lead_id: int, event_type: str, payload: dict):
    event = Event(
        lead_id=lead_id,
        event_type=event_type,
        payload_json=json.dumps(payload)
    )
    session.add(event)
    session.commit()