from __future__ import annotations

import re
from typing import List, Optional

_nlp = None  # lazy-loaded spaCy model


def _get_nlp():
    """
    Lazy-load the spaCy English model the first time we need it.
    If loading fails for any reason, return None so callers can
    gracefully fall back to empty results.
    """
    global _nlp
    if _nlp is not None:
        return _nlp
    try:
        import spacy  # type: ignore[import]

        _nlp = spacy.load("en_core_web_sm")
    except Exception:
        _nlp = None
    return _nlp


_LABEL_PREFIX = {
    "PERSON": "person",
    "ORG": "org",
    "GPE": "place",
    "WORK_OF_ART": "work",
}


def extract_entities_from_text(text: str) -> List[str]:
    """
    Run spaCy NER over the provided text and extract entities for
    a small, focused label set. Results are returned as prefixed
    strings, e.g. person:Michael, org:GitHub, place:Barcelona, work:Alien Clay.

    - Uses labels PERSON, ORG, GPE, WORK_OF_ART
    - Deduplicates entities case-insensitively while preserving order
    - Falls back to [] if spaCy or the model cannot be loaded
    """
    nlp = _get_nlp()
    if nlp is None:
        return []

    try:
        doc = nlp(text)
    except Exception:
        return []

    out: List[str] = []
    seen_lower: set[str] = set()

    for ent in getattr(doc, "ents", []):
        prefix = _LABEL_PREFIX.get(ent.label_)
        if not prefix:
            continue
        val = ent.text.strip()
        if not val:
            continue
        key = val.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        out.append(f"{prefix}:{val}")

    return out


# ---------------------------------------------------------------------------
# Existing name-term extraction used at query time.
# This heuristic remains unchanged and does NOT depend on spaCy.
# ---------------------------------------------------------------------------

NAME_QUOTED = re.compile(
    r'"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)"|\'([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\''
)
NAME_MULTI = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
NAME_SINGLE = re.compile(r"\b([A-Z][a-z]{2,})\b")
STOP = {
    # question words / articles / prepositions / conjunctions
    "What",
    "When",
    "Where",
    "Which",
    "Who",
    "Why",
    "How",
    "The",
    "In",
    "On",
    "For",
    "And",
    "Or",
    "Of",
    "Last",
    "Past",
    "Previous",
    "Most",
    "Recent",
    "Latest",
    # pronouns / possessives
    "I",
    "We",
    "Us",
    "Me",
    "My",
    "Our",
    "Ours",
    "You",
    "Your",
    "Yours",
    "He",
    "She",
    "They",
    "Them",
    "His",
    "Her",
    "Their",
    # common non-name tokens seen in titles
    "Notes",
    "Note",
    "Quick",
    "Catch",
    "Up",
    "Catch-up",
    "Todo",
    "To",
    "Do",
    "Tasks",
    "Task",
    "Meeting",
    "Meet",
    "Journal",
    "Daily",
    "Weights",
    "Ideas",
    "Agent",
    "Talk",
    "Talked",
    "About",
    "From",
    "With",
    "At",
}


def extract_name_terms(q: str) -> List[str]:
    # Prefer quoted multi-word names
    terms: List[str] = []
    for m in NAME_QUOTED.finditer(q):
        g = m.group(1) or m.group(2)
        if g:
            terms.append(g.strip())
    if terms:
        return list(dict.fromkeys(terms))
    # Fallback: capitalized tokens heuristic
    tokens = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", q)
    for t in tokens:
        if t not in STOP:
            terms.append(t)
    return list(dict.fromkeys(terms))
