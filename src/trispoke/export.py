import argparse
import sys
import csv
from pathlib import Path
from sqlalchemy.orm import Session
from trispoke.db.session import get_session
from trispoke.db.models import Lead, Email, PainAnalysis, Campaign

def export_drafts(campaign_name: str, output_file: str):
    """Export email drafts to CSV"""
    with get_session() as session:
        # Get campaign
        campaign = session.query(Campaign).filter_by(name=campaign_name).first()
        if not campaign:
            print(f"Error: Campaign '{campaign_name}' not found")
            sys.exit(1)

        # Get leads with drafts
        leads = session.query(Lead).filter_by(campaign_id=campaign.id).all()

        if not leads:
            print(f"No leads found in campaign '{campaign_name}'")
            return

        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                'lead_email', 'lead_name', 'lead_title', 'company',
                'model_used', 'pain_chronic', 'pain_acute', 'subject', 'body'
            ])

            exported_count = 0
            for lead in leads:
                # Get latest email draft
                email = session.query(Email).filter_by(lead_id=lead.id).order_by(Email.created_at.desc()).first()
                if not email:
                    continue

                # Get pain analysis
                pain = session.query(PainAnalysis).filter_by(lead_id=lead.id).first()

                writer.writerow([
                    lead.email or '',
                    f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
                    lead.title or '',
                    lead.company_name or '',
                    email.model_used or '',
                    pain.chronic if pain else '',
                    pain.acute if pain else '',
                    email.subject or '',
                    email.body or ''
                ])
                exported_count += 1

        print(f"Exported {exported_count} drafts to {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Export email drafts to CSV")
    parser.add_argument("--campaign", required=True, help="Campaign name")
    parser.add_argument("--out", default="drafts.csv", help="Output CSV file")

    args = parser.parse_args()
    export_drafts(args.campaign, args.out)

if __name__ == "__main__":
    main()