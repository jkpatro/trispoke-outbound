from datetime import datetime
from trispoke.db.session import get_session
from trispoke.db.models import Campaign, Lead
from trispoke.config import get_settings

def current_daily_cap(campaign_name: str) -> int:
    """
    Calculate the current daily cap based on days since campaign start.
    - Week 1 (days 0-6): 10
    - Week 2 (days 7-13): 15
    - Week 3+: 20
    """
    settings = get_settings()
    
    # Check if override is set
    if settings.daily_cap_per_inbox and settings.daily_cap_per_inbox > 0:
        return settings.daily_cap_per_inbox
    
    with get_session() as session:
        campaign = session.query(Campaign).filter_by(name=campaign_name).first()
        
        if not campaign or not campaign.started_at:
            # Default to week 1 cap if campaign not started
            return 10
        
        days_elapsed = (datetime.utcnow() - campaign.started_at).days
        
        if days_elapsed < 7:
            return 10
        elif days_elapsed < 14:
            return 15
        else:
            return 20