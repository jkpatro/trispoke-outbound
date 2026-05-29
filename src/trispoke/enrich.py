import argparse
import sys
from sqlalchemy.orm import Session
from trispoke.db.session import get_session
from trispoke.db.models import Lead, Campaign, LeadStatus
from trispoke.db.event_log import log_event
from trispoke.apollo.client import ApolloClient
from trispoke.config import get_settings

settings = get_settings()

def enrich_leads(campaign_name: str):
    """Enrich leads in a campaign"""
    client = ApolloClient()

    with get_session() as session:
        # Get campaign
        campaign = session.query(Campaign).filter_by(name=campaign_name).first()
        if not campaign:
            print(f"Error: Campaign '{campaign_name}' not found")
            sys.exit(1)

        # Get leads with status 'new'
        leads = session.query(Lead).filter_by(
            campaign_id=campaign.id,
            status=LeadStatus.new.value
        ).all()

        if not leads:
            print(f"No leads to enrich in campaign '{campaign_name}'")
            return

        enriched_count = 0

        for lead in leads:
            enrichment_data = {}

            # Try to find email if missing
            if not lead.email:
                person = client.match_person(
                    linkedin_url=lead.linkedin_url,
                    first_name=lead.first_name,
                    last_name=lead.last_name,
                    organization_name=lead.company_name
                )
                if person and person.get('email'):
                    enrichment_data['email'] = person['email']

            # Always try to enrich organization
            if lead.company_domain:
                org = client.enrich_organization(lead.company_domain)
                if org:
                    # Update company fields if missing
                    if not lead.company_name and org.get('name'):
                        enrichment_data['company_name'] = org['name']
                    if not lead.company_size and org.get('estimated_num_employees'):
                        enrichment_data['company_size'] = str(org['estimated_num_employees'])
                    if not lead.company_industry and org.get('industry'):
                        enrichment_data['company_industry'] = org['industry']
                    if not lead.company_location and org.get('city'):
                        enrichment_data['company_location'] = f"{org.get('city', '')}, {org.get('state', '')}".strip(', ')

            # Update lead if we have new data
            if enrichment_data:
                for key, value in enrichment_data.items():
                    setattr(lead, key, value)
                lead.status = LeadStatus.enriched.value
                enriched_count += 1

                # Log event with full Apollo response
                log_event(session, lead.id, "apollo_enriched", {
                    "enrichment_data": enrichment_data,
                    "campaign": campaign_name
                })

        session.commit()
        print(f"Enriched {enriched_count} leads in campaign '{campaign_name}'")

def main():
    parser = argparse.ArgumentParser(description="Enrich leads with Apollo data")
    parser.add_argument("--campaign", required=True, help="Campaign name")

    args = parser.parse_args()
    enrich_leads(args.campaign)

if __name__ == "__main__":
    main()