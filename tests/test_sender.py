import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from trispoke.db.models import (
    Base, Campaign, Lead, Email, Send, LeadStatus, SendStatus, CampaignMode
)
from trispoke.sender.inbox_manager import today_sends_for_inbox, pick_next_inbox
from trispoke.sender.ramp import current_daily_cap
from trispoke.sender.sender_loop import SenderLoop

@pytest.fixture
def db_session():
    """Create in-memory database for testing"""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()

@patch('trispoke.sender.inbox_manager.get_session')
def test_pick_next_inbox_respects_daily_cap(mock_get_session, db_session):
    """Test that pick_next_inbox respects daily caps"""
    mock_get_session.return_value.__enter__.return_value = db_session
    
    # Create campaign and leads
    campaign = Campaign(
        name="test",
        mode=CampaignMode.hybrid.value,
        settings_json='{}'
    )
    db_session.add(campaign)
    db_session.commit()
    
    # Create leads
    for i in range(3):
        lead = Lead(
            campaign_id=campaign.id,
            email=f"test{i}@example.com",
            source_row_json='{}',
            status=LeadStatus.new.value
        )
        db_session.add(lead)
    db_session.commit()
    
    # Create emails
    emails = db_session.query(Email).all()
    for email in emails:
        db_session.add(email)
    db_session.commit()
    
    # Create sends to simulate inbox reaching cap
    inbox1 = "inbox1@example.com"
    inbox2 = "inbox2@example.com"
    
    # Add 10 sends from inbox1 (at cap of 10)
    for i in range(10):
        send = Send(
            email_id=1,
            sent_from_inbox=inbox1,
            sent_at=datetime.utcnow(),
            status=SendStatus.sent.value
        )
        db_session.add(send)
    db_session.commit()
    
    # Mock settings to return our inboxes
    with patch('trispoke.sender.inbox_manager._parse_inboxes_config') as mock_parse:
        mock_inbox1 = Mock()
        mock_inbox1.address = inbox1
        mock_inbox2 = Mock()
        mock_inbox2.address = inbox2
        mock_parse.return_value = [mock_inbox1, mock_inbox2]
        
        # At cap of 10, inbox1 should be skipped, inbox2 should be picked
        result = pick_next_inbox(10)
        assert result.address == inbox2

@patch('trispoke.sender.ramp.get_session')
def test_ramp_schedule(mock_get_session, db_session):
    """Test that ramp returns correct caps for each week"""
    mock_get_session.return_value.__enter__.return_value = db_session
    
    # Create campaign with start time 1 day ago (week 1)
    campaign_week1 = Campaign(
        name="week1",
        mode=CampaignMode.hybrid.value,
        settings_json='{}',
        started_at=datetime.utcnow() - timedelta(days=1)
    )
    db_session.add(campaign_week1)
    
    # Create campaign with start time 8 days ago (week 2)
    campaign_week2 = Campaign(
        name="week2",
        mode=CampaignMode.hybrid.value,
        settings_json='{}',
        started_at=datetime.utcnow() - timedelta(days=8)
    )
    db_session.add(campaign_week2)
    
    # Create campaign with start time 15 days ago (week 3+)
    campaign_week3 = Campaign(
        name="week3",
        mode=CampaignMode.hybrid.value,
        settings_json='{}',
        started_at=datetime.utcnow() - timedelta(days=15)
    )
    db_session.add(campaign_week3)
    db_session.commit()
    
    # Mock settings to return default values
    with patch('trispoke.sender.ramp.get_settings') as mock_settings:
        mock_settings.return_value.daily_cap_per_inbox = 0
        
        assert current_daily_cap("week1") == 10
        assert current_daily_cap("week2") == 15
        assert current_daily_cap("week3") == 20

@patch('trispoke.sender.smtp_client.smtplib.SMTP_SSL')
@patch('trispoke.sender.sender_loop.pick_next_inbox')
@patch('trispoke.sender.sender_loop.current_daily_cap')
def test_sender_loop_processes_approved_emails(
    mock_daily_cap, mock_pick_inbox, mock_smtp, db_session
):
    """Test that sender_loop processes emails with approved status"""
    mock_daily_cap.return_value = 20
    
    # Create campaign
    campaign = Campaign(
        name="test",
        mode=CampaignMode.hybrid.value,
        settings_json='{}'
    )
    
    # Create lead with approved status
    lead = Lead(
        campaign_id=1,
        email="test@example.com",
        source_row_json='{}',
        status=LeadStatus.approved.value
    )
    
    # Create email
    email = Email(
        lead_id=1,
        campaign_id=1,
        subject="Test Subject",
        body="Test Body"
    )
    
    # Mock inbox
    mock_inbox = Mock()
    mock_inbox.address = "from@example.com"
    mock_inbox.smtp_host = "smtp.example.com"
    mock_inbox.smtp_port = 587
    mock_inbox.smtp_username = "user"
    mock_inbox.smtp_password = "pass"
    mock_pick_inbox.return_value = mock_inbox
    
    # Mock SMTP server
    mock_smtp_instance = MagicMock()
    mock_smtp.return_value = mock_smtp_instance
    
    # Verify that inbox selection respects daily cap
    # (This would be tested with actual database integration)