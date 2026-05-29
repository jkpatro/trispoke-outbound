from dataclasses import dataclass
from typing import Optional
from trispoke.db.models import Lead

@dataclass
class PainTriple:
    chronic: str
    acute: str
    trigger: str
    confidence: float

# Lookup table for pain analysis
PAIN_LOOKUP = {
    # Staffing & Recruiting + Owner/Founder/President/CEO at sub-30 employees
    ("staffing", None, "owner", None): PainTriple(
        chronic="Building a consistent pipeline of qualified candidates",
        acute="Current hiring needs aren't being met quickly enough",
        trigger="Recent growth spurt requiring 3-5 new hires this quarter",
        confidence=0.85
    ),
    ("staffing", None, "founder", None): PainTriple(
        chronic="Building a consistent pipeline of qualified candidates",
        acute="Current hiring needs aren't being met quickly enough",
        trigger="Recent growth spurt requiring 3-5 new hires this quarter",
        confidence=0.85
    ),
    ("staffing", None, "president", None): PainTriple(
        chronic="Building a consistent pipeline of qualified candidates",
        acute="Current hiring needs aren't being met quickly enough",
        trigger="Recent growth spurt requiring 3-5 new hires this quarter",
        confidence=0.85
    ),
    ("staffing", None, "ceo", None): PainTriple(
        chronic="Building a consistent pipeline of qualified candidates",
        acute="Current hiring needs aren't being met quickly enough",
        trigger="Recent growth spurt requiring 3-5 new hires this quarter",
        confidence=0.85
    ),

    # Staffing & Recruiting + VP/Director/Partner at any size
    ("staffing", None, "vp", None): PainTriple(
        chronic="Scaling recruitment operations to handle volume",
        acute="Time-to-hire is impacting business growth",
        trigger="Expanding team by 20% this year",
        confidence=0.85
    ),
    ("staffing", None, "director", None): PainTriple(
        chronic="Scaling recruitment operations to handle volume",
        acute="Time-to-hire is impacting business growth",
        trigger="Expanding team by 20% this year",
        confidence=0.85
    ),
    ("staffing", None, "partner", None): PainTriple(
        chronic="Scaling recruitment operations to handle volume",
        acute="Time-to-hire is impacting business growth",
        trigger="Expanding team by 20% this year",
        confidence=0.85
    ),

    # Executive search + Partner/Principal
    ("executive search", None, "partner", None): PainTriple(
        chronic="Finding senior leadership talent in competitive markets",
        acute="Critical C-suite positions remain unfilled",
        trigger="Board meeting next month requiring executive hires",
        confidence=0.85
    ),
    ("executive search", None, "principal", None): PainTriple(
        chronic="Finding senior leadership talent in competitive markets",
        acute="Critical C-suite positions remain unfilled",
        trigger="Board meeting next month requiring executive hires",
        confidence=0.85
    ),

    # IT staffing + VP/Director
    ("it staffing", None, "vp", None): PainTriple(
        chronic="Securing technical talent for digital transformation",
        acute="IT projects delayed due to talent shortages",
        trigger="New product launch requiring specialized developers",
        confidence=0.85
    ),
    ("it staffing", None, "director", None): PainTriple(
        chronic="Securing technical talent for digital transformation",
        acute="IT projects delayed due to talent shortages",
        trigger="New product launch requiring specialized developers",
        confidence=0.85
    ),
}

def _normalize_industry(industry: Optional[str]) -> Optional[str]:
    """Normalize industry names"""
    if not industry:
        return None
    industry_lower = industry.lower()
    if "staffing" in industry_lower or "recruiting" in industry_lower:
        return "staffing"
    if "executive search" in industry_lower:
        return "executive search"
    if "it" in industry_lower and "staffing" in industry_lower:
        return "it staffing"
    return industry_lower

def _normalize_title(title: Optional[str]) -> Optional[str]:
    """Normalize job titles"""
    if not title:
        return None
    title_lower = title.lower()
    if any(word in title_lower for word in ["owner", "founder", "president", "ceo"]):
        return "owner" if "owner" in title_lower or "founder" in title_lower else "ceo"
    if "vp" in title_lower:
        return "vp"
    if "director" in title_lower:
        return "director"
    if "partner" in title_lower:
        return "partner"
    if "principal" in title_lower:
        return "principal"
    return title_lower

def _is_small_company(size: Optional[str]) -> bool:
    """Check if company size is sub-30"""
    if not size:
        return False
    try:
        return int(size) < 30
    except ValueError:
        return False

def analyze(lead: Lead) -> PainTriple:
    """Analyze lead for pain points"""
    industry = _normalize_industry(lead.company_industry)
    title = _normalize_title(lead.title)
    size = lead.company_size

    # Check for specific matches
    keys_to_check = [
        (industry, None, title, size),
        (industry, None, title, None),
    ]

    for key in keys_to_check:
        if key in PAIN_LOOKUP:
            pain = PAIN_LOOKUP[key]
            # For owner roles, check company size
            if title in ["owner", "ceo"] and industry in ["staffing", "recruiting"]:
                if _is_small_company(size):
                    return pain
            else:
                return pain

    # Generic fallback
    return PainTriple(
        chronic="Managing business growth and operational efficiency",
        acute="Current processes aren't scaling with business needs",
        trigger="Recent business expansion requiring process improvements",
        confidence=0.40
    )