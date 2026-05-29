import argparse
import sys
from pathlib import Path
from typing import Dict, Any
import openpyxl
from trispoke.db.session import get_session
from trispoke.db.models import Lead, Campaign, LeadStatus
from trispoke.db.event_log import log_event
from trispoke.db.unsubscribe import is_unsubscribed
from trispoke.config import get_settings

settings = get_settings()

def normalize_columns(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Apollo export column names to our schema"""
    # Common Apollo column mappings
    mappings = {
        'Email': 'email',
        'First Name': 'first_name',
        'Last Name': 'last_name',
        'Title': 'title',
        'Company': 'company_name',
        'Company Domain': 'company_domain',
        'Company Size': 'company_size',
        'Industry': 'company_industry',
        'Location': 'company_location',
        'LinkedIn URL': 'linkedin_url',
        'Apollo ID': 'apollo_id',
    }

    normalized = {}
    for apollo_col, our_col in mappings.items():
        if apollo_col in row:
            normalized[our_col] = row[apollo_col]

    # Convert company_size to string if it's a number
    if 'company_size' in normalized and isinstance(normalized['company_size'], (int, float)):
        normalized['company_size'] = str(int(normalized['company_size']))

    return normalized

def import_leads(file_path: str, campaign_name: str):
    """Import leads from Apollo Excel export"""
    if not Path(file_path).exists():
        print(f"Error: File {file_path} does not exist")
        sys.exit(1)

    # Load workbook
    wb = openpyxl.load_workbook(file_path)
    sheet = wb.active

    # Read headers
    headers = [cell.value for cell in sheet[1]]

    leads_data = []
    seen_emails = set()

    # Read data rows
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if not row[0]:  # Skip empty rows
            continue

        row_dict = dict(zip(headers, row))
        normalized = normalize_columns(row_dict)

        email = normalized.get('email')
        if not email or email in seen_emails:
            continue  # Skip duplicates or missing email

        seen_emails.add(email)
        leads_data.append(normalized)

    print(f"Found {len(leads_data)} unique leads to import")

    with get_session() as session:
        # Get or create campaign
        campaign = session.query(Campaign).filter_by(name=campaign_name).first()
        if not campaign:
            campaign = Campaign(
                name=campaign_name,
                mode="hybrid",  # Default mode
                settings_json='{}'
            )
            session.add(campaign)
            session.commit()

        imported_count = 0
        skipped_unsubscribed = 0
        for lead_data in leads_data:
            # V1.5.2: skip globally-unsubscribed addresses
            if is_unsubscribed(session, lead_data["email"]):
                skipped_unsubscribed += 1
                continue

            # Check if lead already exists
            existing = session.query(Lead).filter_by(
                email=lead_data['email'],
                campaign_id=campaign.id
            ).first()

            if existing:
                continue

            lead = Lead(
                campaign_id=campaign.id,
                **lead_data,
                source_row_json=str(row_dict),  # Store original data
                status=LeadStatus.new.value,
                intake_source="apollo_csv",
            )
            session.add(lead)
            session.flush()  # populate lead.id before logging the event
            imported_count += 1

            # Log event
            log_event(session, lead.id, "lead_imported", {
                "source": "apollo_export",
                "campaign": campaign_name,
                "original_data": row_dict
            })

        session.commit()
        msg = f"Imported {imported_count} new leads into campaign '{campaign_name}'"
        if skipped_unsubscribed:
            msg += f" (skipped {skipped_unsubscribed} unsubscribed)"
        print(msg)

def main():
    parser = argparse.ArgumentParser(description="Import Apollo contacts export")
    parser.add_argument("--file", required=True, help="Path to Apollo Excel export")
    parser.add_argument("--campaign", required=True, help="Campaign name")

    args = parser.parse_args()
    import_leads(args.file, args.campaign)

if __name__ == "__main__":
    main()