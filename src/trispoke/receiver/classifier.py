import re
from typing import Optional

def classify(subject: str, body: str) -> str:
    """Classify a reply message - regex only, no LLM"""
    
    combined = f"{subject}\n{body}".lower()
    
    # Check for unsubscribe first (highest priority)
    unsubscribe_patterns = [
        r'\bunsubscribe\b',
        r'\bremove me\b',
        r'\bstop\b',
        r'\bdo not\s+contact',
    ]
    for pattern in unsubscribe_patterns:
        if re.search(pattern, combined):
            return 'unsubscribe'
    
    # Check for auto-reply
    auto_reply_patterns = [
        r'\bout of office\b',
        r'\bautomatic reply\b',
        r'\bvacation\b',
        r'\bauto-reply\b',
        r'\bi am out\b',
        r'\bi will be\b',
    ]
    for pattern in auto_reply_patterns:
        if re.search(pattern, combined):
            return 'auto_reply'
    
    # Check for negative
    negative_patterns = [
        r'\bnot interested\b',
        r'\bno thanks\b',
        r'\bnot a fit\b',
        r'\bno thank you\b',
        r'\bdecline\b',
        r'\bpass\b',
    ]
    for pattern in negative_patterns:
        if re.search(pattern, combined):
            return 'negative'
    
    # Check for positive
    positive_patterns = [
        r'\byes\b',
        r'\binterested\b',
        r'\btell me more\b',
        r'\blet[\'s ]+chat\b',
        r'\bsure\b',
        r'\bhappy to\b',
        r'\blove to\b',
        r'\bgreat\b',
        r'\bwould like to\b',
    ]
    for pattern in positive_patterns:
        if re.search(pattern, combined):
            return 'positive'
    
    return 'unknown'

def is_bounce(subject: str, body: str) -> bool:
    """Detect delivery failure/bounce"""
    combined = f"{subject}\n{body}".lower()
    
    bounce_patterns = [
        r'\bdelivery failure\b',
        r'\bundeliverable\b',
        r'\bbounce',
        r'\bfailed to deliver\b',
        r'\bmailer-daemon\b',
        r'\bmail delivery failed\b',
        r'\bpermanent failure\b',
        r'\b550\b',  # SMTP 550 error
        r'\b554\b',  # SMTP 554 error
    ]
    
    for pattern in bounce_patterns:
        if re.search(pattern, combined):
            return True
    
    return False