import pytest
import tempfile
import csv
from pathlib import Path
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from trispoke.db.models import Base, Campaign, Lead, Email, PainAnalysis, LeadStatus
from trispoke.import_apollo import import_leads
from trispoke.enrich import enrich_leads
from trispoke.generate import generate_drafts
from trispoke.export import export_drafts

@pytest.fixture
def db_session():
    """Create in-memory database for testing"""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()

@patch('trispoke.export.get_session')
@patch('trispoke.generate.get_session')
@patch('trispoke.enrich.get_session')
@patch('trispoke.apollo.client.ApolloClient.match_person')
@patch('trispoke.apollo.client.ApolloClient.enrich_organization')
@patch('trispoke.llm.ollama_client.OllamaClient.generate')
def test_pipeline(
    mock_ollama,
    mock_enrich_org,
    mock_match_person,
    mock_enrich_session,
    mock_generate_session,
    mock_export_session,
    db_session,
):
    # Route every production-side `with get_session() as session:` to the
    # in-memory fixture session so enrich/generate/export see the same DB
    # the test populates.
    for m in (mock_enrich_session, mock_generate_session, mock_export_session):
        m.return_value.__enter__.return_value = db_session
        m.return_value.__exit__.return_value = False
    """Test the full import -> enrich -> generate -> export pipeline"""

    # Mock Apollo responses
    mock_match_person.return_value = {
        'email': 'john.doe@company.com'
    }
    mock_enrich_org.return_value = {
        'name': 'Test Company',
        'estimated_num_employees': 50,
        'industry': 'Staffing & Recruiting',
        'city': 'Toronto',
        'state': 'ON'
    }

    # Mock Ollama response
    mock_ollama.return_value = (
        """Subject: staffing firms struggling with candidate quality

Dear John,

Staffing companies are seeing 30% more applications but 40% fewer qualified candidates. For owners like you, this gap means longer time-to-hire and higher costs.

We're helping staffing firms build better talent pipelines that improve quality by 50%. Would you have 15 minutes to discuss your current challenges?

Best,
Alex""",
        150,
        2.5
    )

    # Create test Excel data (simulate the import)
    # Since we can't easily create Excel in test, we'll manually insert a lead
    campaign = Campaign(
        name="test_campaign",
        mode="local_only",
        settings_json='{}'
    )
    db_session.add(campaign)
    db_session.commit()

    lead = Lead(
        campaign_id=campaign.id,
        email="john.doe@company.com",
        first_name="John",
        last_name="Doe",
        title="Owner",
        company_name="Test Company",
        company_domain="company.com",
        company_size="25",
        company_industry="Staffing & Recruiting",
        source_row_json='{}',
        status=LeadStatus.new.value
    )
    db_session.add(lead)
    db_session.commit()

    # Test enrich
    enrich_leads("test_campaign")

    # Verify enrichment
    db_session.refresh(lead)
    assert lead.status == LeadStatus.enriched.value

    # Test generate
    generate_drafts("test_campaign", "local_only")

    # Verify generation. Post-V1.5 generation runs an LLM QC pass that
    # ends in 'drafted' (passed) or 'qc_flagged' — both are valid here.
    db_session.refresh(lead)
    assert lead.status in (LeadStatus.drafted.value, LeadStatus.qc_flagged.value)

    email = db_session.query(Email).filter_by(lead_id=lead.id).first()
    assert email is not None
    assert "staffing firms struggling" in email.subject.lower()
    # Router stores model_used as "provider:model_name" (e.g. local:qwen3:8b).
    assert email.model_used == "local:qwen3:8b"

    pain = db_session.query(PainAnalysis).filter_by(lead_id=lead.id).first()
    assert pain is not None
    assert pain.confidence == 0.85

    # Test export
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        export_drafts("test_campaign", tmp_path)

        # Verify CSV
        with open(tmp_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            rows = list(reader)

        assert len(rows) == 2  # Header + 1 data row
        assert rows[0] == ['lead_email', 'lead_name', 'lead_title', 'company', 'model_used', 'pain_chronic', 'pain_acute', 'subject', 'body']
        assert rows[1][0] == 'john.doe@company.com'
        assert 'staffing firms struggling' in rows[1][7].lower()

    finally:
        Path(tmp_path).unlink()