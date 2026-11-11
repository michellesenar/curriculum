"""
A plain check on free-text fields before they are saved.

Descriptions and reasons should describe the subject, not the person. This
catches the obvious slips (first-person and "the user" wording) without any
model call. It cannot catch everything, so tool prompts also tell the client
not to include health, family, work, or personal circumstances.
"""

import re

_PERSON_PATTERNS = [
    r"\bmy\b",
    r"\bmine\b",
    r"\bmyself\b",
    r"\bme\b",
    r"\bI'm\b",
    r"\bI've\b",
    r"\bI'd\b",
    r"\bI'll\b",
    r"\bthe user\b",
    r"\buser's\b",
    # Bare "I" is also a Roman numeral (World War I, Type I error), so only
    # match it before a verb people use when talking about themselves.
    (
        r"\bI\s+(?:am|was|had|have|need|want|feel|felt|think|struggle|struggled|"
        r"lost|can't|cannot|don't|do|did|will|would|should|keep|used|got|get|"
        r"like|love|hate)\b"
    ),
]


def personal_references(text: str) -> list[str]:
    """Return the first-person or 'the user' phrases found in text, lowercased."""
    found = {
        m.group(0).lower()
        for pattern in _PERSON_PATTERNS
        for m in re.finditer(pattern, text, re.IGNORECASE)
    }
    return sorted(found)
