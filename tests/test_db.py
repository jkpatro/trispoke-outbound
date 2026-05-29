import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import func
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from trispoke.db.models import (
    Base, Campaign, Lead, PainAnalysis, Email, Send, Reply, Event,
    CampaignMode, LeadStatus, SendStatus, ReplyClassification, EventType
)
from trispoke.db.event_log import log_event


def test_db():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        # Insert campaign
        campaign = Campaign(
            name="Test Campaign",
            mode=CampaignMode.hybrid.value,
            settings_json='{}'
        )
        session.add(campaign)
        session.commit()

        # Insert lead
        lead = Lead(
            campaign_id=campaign.id,
            email="test@example.com",
            source_row_json='{}',
            status=LeadStatus.new.value
        )
        session.add(lead)
        session.commit()

        # Insert pain analysis
        pain = PainAnalysis(
            lead_id=lead.id,
            chronic="Chronic pain description",
            confidence=0.8
        )
        session.add(pain)
        session.commit()

        # Insert email
        email = Email(
            lead_id=lead.id,
            campaign_id=campaign.id,
            subject="Test Subject",
            body="Test Body"
        )
        session.add(email)
        session.commit()

        # Insert send
        send = Send(
            email_id=email.id,
            sent_from_inbox="test@inbox.com",
            sent_at=func.now(),
            status=SendStatus.sent.value
        )
        session.add(send)
        session.commit()

        # Insert reply
        reply = Reply(
            lead_id=lead.id,
            email_id=email.id,
            received_at=func.now(),
            from_address="reply@example.com",
            subject="Re: Test Subject",
            body="Reply Body",
            classification=ReplyClassification.positive.value
        )
        session.add(reply)
        session.commit()

        # Test log_event
        log_event(session, lead.id, EventType.lead_imported.value, {"test": "data"})

        # Verify counts
        assert session.query(Campaign).count() == 1
        assert session.query(Lead).count() == 1
        assert session.query(PainAnalysis).count() == 1
        assert session.query(Email).count() == 1
        assert session.query(Send).count() == 1
        assert session.query(Reply).count() == 1
        assert session.query(Event).count() == 1

        # Verify log_event JSON
        event = session.query(Event).first()
        assert event.payload_json == '{"test": "data"}'

        # Test foreign key constraint
        try:
            invalid_lead = Lead(
                campaign_id=999,  # Non-existent campaign
                email="invalid@example.com",
                source_row_json='{}',
                status=LeadStatus.new.value
            )
            session.add(invalid_lead)
            session.commit()
            pytest.fail("Foreign key constraint should have failed")
        except Exception:
            session.rollback()

        # Test unique constraint on pain_analyses.lead_id
        try:
            duplicate_pain = PainAnalysis(
                lead_id=lead.id,  # Same lead_id
                chronic="Another pain"
            )
            session.add(duplicate_pain)
            session.commit()
            pytest.fail("Unique constraint should have failed")
        except Exception:
            session.rollback()

        # Verify indexes exist
        with engine.connect() as conn:
            # Check leads indexes
            result = conn.execute(text("PRAGMA index_list(leads)"))
            lead_indexes = [row[1] for row in result.fetchall()]
            assert "idx_leads_campaign_id" in lead_indexes
            assert "idx_leads_status" in lead_indexes

            # Check emails index
            result = conn.execute(text("PRAGMA index_list(emails)"))
            email_indexes = [row[1] for row in result.fetchall()]
            assert "idx_emails_lead_id" in email_indexes

            # Check sends index
            result = conn.execute(text("PRAGMA index_list(sends)"))
            send_indexes = [row[1] for row in result.fetchall()]
            assert "idx_sends_email_id" in send_indexes

            # Check replies index
            result = conn.execute(text("PRAGMA index_list(replies)"))
            reply_indexes = [row[1] for row in result.fetchall()]
            assert "idx_replies_lead_id" in reply_indexes

            # Check events index
            result = conn.execute(text("PRAGMA index_list(events)"))
            event_indexes = [row[1] for row in result.fetchall()]
            assert "idx_events_lead_id" in event_indexes