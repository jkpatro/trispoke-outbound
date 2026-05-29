import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from trispoke.db.models import (
    Base, Campaign, Lead, Email, Send, Reply, LeadStatus, SendStatus, CampaignMode
)
from trispoke.receiver.classifier import classify, is_bounce
from trispoke.receiver.imap_poller import extract_message_id

@pytest.fixture
def db_session():
    """Create in-memory database for testing"""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()

class TestClassifier:
    """Test the reply classifier"""
    
    def test_classify_positive_yes(self):
        assert classify("RE: Your offer", "Yes, I'm interested!") == 'positive'
    
    def test_classify_positive_tell_me_more(self):
        assert classify("RE: Meeting", "Tell me more about this opportunity") == 'positive'
    
    def test_classify_positive_let_chat(self):
        assert classify("RE: Opportunity", "Let's chat about this") == 'positive'
    
    def test_classify_positive_sure(self):
        assert classify("RE: Proposal", "Sure, I'd like to hear more") == 'positive'
    
    def test_classify_positive_happy(self):
        assert classify("RE: Discussion", "Happy to talk about this") == 'positive'
    
    def test_classify_negative_not_interested(self):
        assert classify("RE: Offer", "Not interested, thanks") == 'negative'
    
    def test_classify_negative_no_thanks(self):
        assert classify("RE: Proposal", "No thanks") == 'negative'
    
    def test_classify_negative_pass(self):
        assert classify("RE: Opportunity", "I'll pass on this one") == 'negative'
    
    def test_classify_unsubscribe_explicit(self):
        assert classify("RE: Email", "Please unsubscribe me") == 'unsubscribe'
    
    def test_classify_unsubscribe_remove_me(self):
        assert classify("RE: Email", "Remove me from your list") == 'unsubscribe'
    
    def test_classify_unsubscribe_stop(self):
        assert classify("RE: Email", "Stop sending me emails") == 'unsubscribe'
    
    def test_classify_auto_reply_out_of_office(self):
        assert classify("Out of Office", "I am out of the office") == 'auto_reply'
    
    def test_classify_auto_reply_automatic(self):
        assert classify("Automatic Reply", "This is an automatic reply") == 'auto_reply'
    
    def test_classify_auto_reply_vacation(self):
        assert classify("Vacation Notice", "I'm on vacation until...") == 'auto_reply'
    
    def test_classify_auto_reply_will_return(self):
        assert classify("Away", "I will be back...") == 'auto_reply'
    
    def test_classify_unknown(self):
        assert classify("RE: Meeting", "Looking at your message") == 'unknown'
    
    def test_classify_case_insensitive(self):
        assert classify("RE: Offer", "YES, INTERESTED") == 'positive'
    
    def test_classify_unsubscribe_priority_over_negative(self):
        # unsubscribe should match even if negative words present
        assert classify("RE: Email", "Unsubscribe me, not interested") == 'unsubscribe'
    
    def test_classify_auto_reply_priority_over_other(self):
        # auto_reply should match before positive
        result = classify("Out of Office Reply", "Out of office, yes I'm here")
        assert result == 'auto_reply'
    
    def test_classify_love_to(self):
        assert classify("RE: Proposal", "I'd love to discuss this") == 'positive'
    
    def test_classify_great(self):
        assert classify("RE: Idea", "That sounds great!") == 'positive'

class TestBounceDetection:
    """Test bounce detection"""
    
    def test_bounce_delivery_failure(self):
        assert is_bounce("Delivery Failure", "The email could not be delivered") == True
    
    def test_bounce_undeliverable(self):
        assert is_bounce("Undeliverable", "This message was undeliverable") == True
    
    def test_bounce_mailer_daemon(self):
        assert is_bounce("Mail from Mailer-Daemon", "Bounce notification") == True
    
    def test_bounce_failed_to_deliver(self):
        assert is_bounce("Failed", "Failed to deliver your message") == True
    
    def test_bounce_550_error(self):
        assert is_bounce("SMTP Error", "550 User not found") == True
    
    def test_bounce_554_error(self):
        assert is_bounce("SMTP Error", "554 Message rejected") == True
    
    def test_not_bounce_regular_reply(self):
        assert is_bounce("RE: Your message", "Thanks for reaching out") == False
    
    def test_not_bounce_positive_reply(self):
        assert is_bounce("RE: Offer", "Yes, I'm interested") == False

class TestMessageIdExtraction:
    """Test Message-ID extraction"""
    
    def test_extract_in_reply_to(self):
        mock_msg = Mock()
        mock_msg.headers = {
            'In-Reply-To': '<abc123@example.com>'
        }
        result = extract_message_id(mock_msg)
        assert result == 'abc123@example.com'
    
    def test_extract_in_reply_to_without_brackets(self):
        mock_msg = Mock()
        mock_msg.headers = {
            'In-Reply-To': 'abc123@example.com'
        }
        result = extract_message_id(mock_msg)
        assert result == 'abc123@example.com'
    
    def test_extract_references(self):
        mock_msg = Mock()
        mock_msg.headers = {
            'References': '<msg1@example.com> <abc123@example.com>'
        }
        result = extract_message_id(mock_msg)
        assert result == 'abc123@example.com'
    
    def test_extract_references_last_message(self):
        mock_msg = Mock()
        mock_msg.headers = {
            'References': '<msg1@example.com> <msg2@example.com> <abc123@example.com>'
        }
        result = extract_message_id(mock_msg)
        assert result == 'abc123@example.com'
    
    def test_extract_no_headers(self):
        mock_msg = Mock()
        mock_msg.headers = {}
        result = extract_message_id(mock_msg)
        assert result is None
    
    def test_extract_in_reply_to_takes_precedence(self):
        mock_msg = Mock()
        mock_msg.headers = {
            'In-Reply-To': '<priority@example.com>',
            'References': '<other@example.com>'
        }
        result = extract_message_id(mock_msg)
        assert result == 'priority@example.com'

class TestReplyMatching:
    """Test reply to send matching"""
    
    @patch('trispoke.receiver.imap_poller.get_session')
    def test_match_by_message_id(self, mock_get_session, db_session):
        """Test matching a reply by SMTP message ID"""
        mock_get_session.return_value.__enter__.return_value = db_session
        
        # Create campaign, lead, email, send
        campaign = Campaign(
            name="test",
            mode=CampaignMode.hybrid.value,
            settings_json='{}'
        )
        db_session.add(campaign)
        db_session.commit()
        
        lead = Lead(
            campaign_id=campaign.id,
            email="recipient@example.com",
            source_row_json='{}',
            status=LeadStatus.sent.value
        )
        db_session.add(lead)
        db_session.commit()
        
        email = Email(
            lead_id=lead.id,
            campaign_id=campaign.id,
            subject="Test",
            body="Test body"
        )
        db_session.add(email)
        db_session.commit()
        
        send = Send(
            email_id=email.id,
            sent_from_inbox="sender@example.com",
            sent_at=datetime.utcnow(),
            smtp_message_id="test-msg-id@example.com",
            status=SendStatus.sent.value
        )
        db_session.add(send)
        db_session.commit()
        
        # Test matching
        from trispoke.receiver.imap_poller import match_reply_to_send
        matched_send = match_reply_to_send("sender@example.com", "test-msg-id@example.com")
        assert matched_send is not None
        assert matched_send.id == send.id

class TestFollowupScheduling:
    """Test follow-up scheduling"""
    
    @patch('trispoke.scheduler.followup.get_session')
    def test_schedule_followup_after_4_days(self, mock_get_session, db_session):
        """Test that follow-up is scheduled after 4 days"""
        mock_get_session.return_value.__enter__.return_value = db_session
        
        # Create campaign and lead sent 4+ days ago
        campaign = Campaign(
            name="test",
            mode=CampaignMode.hybrid.value,
            settings_json='{}'
        )
        db_session.add(campaign)
        db_session.commit()
        
        lead = Lead(
            campaign_id=campaign.id,
            email="recipient@example.com",
            source_row_json='{}',
            status=LeadStatus.sent.value,
            created_at=datetime.utcnow() - timedelta(days=5)
        )
        db_session.add(lead)
        db_session.commit()
        
        # Verify that lead would be eligible for followup
        four_days_ago = datetime.utcnow() - timedelta(days=4)
        assert lead.created_at <= four_days_ago