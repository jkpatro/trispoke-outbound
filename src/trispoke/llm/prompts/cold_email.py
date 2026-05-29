from jinja2 import Template
from trispoke.db.models import Lead
from trispoke.pain_analyzer import PainTriple

COLD_EMAIL_TEMPLATE = """You are writing a cold email to a busy executive. Your goal is to get a 15-minute conversation about their business challenges.

RULES:
- Subject line: lowercase, no marketing words, specific observation
- First sentence: industry-specific observation, NOT flattery
- Name pain in business terms ("steady second channel" not "scale outbound")
- Concrete, small ask ("15-minute look")
- Sign-off: just first name, no title
- Total body: 80-130 words
- NO: "leverage", "synergy", "circle back", "touch base", em-dashes

EXAMPLES:

Subject: healthcare staffing shortages hitting hospitals harder this winter

Dear Sarah,

I noticed hospital admissions are up 15% this flu season, and staffing agencies are struggling to keep up with emergency department demands. For healthcare execs like you, this creates constant pressure to find qualified nurses and doctors quickly.

We're helping similar organizations build reliable talent pipelines that reduce hiring time by 40%. Would you have 15 minutes next week to discuss your current challenges?

Best,
Alex

---

Subject: manufacturing firms losing market share to automation leaders

Dear Michael,

Manufacturing companies that haven't invested in automation are seeing their costs rise 25% faster than competitors. For operations leaders like you, this gap widens every quarter.

We work with manufacturers to implement practical automation that cuts operational costs by 30% within 6 months. Could we schedule a 15-minute call to explore your priorities?

Thanks,
Jordan

---

Now write an email to:

Name: {{ lead.first_name }} {{ lead.last_name }}
Title: {{ lead.title }}
Company: {{ lead.company_name }}
Industry: {{ lead.company_industry }}
Company Size: {{ lead.company_size }}

Pain Analysis:
Chronic: {{ pain.chronic }}
Acute: {{ pain.acute }}
Trigger: {{ pain.trigger }}

Subject:"""

def generate_prompt(lead: Lead, pain: PainTriple) -> str:
    """Generate the cold email prompt"""
    template = Template(COLD_EMAIL_TEMPLATE)
    return template.render(lead=lead, pain=pain)