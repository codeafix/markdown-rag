from __future__ import annotations

import re
from typing import List

# Quoted multi-word names like "John Doe" or 'Mary Jane'
NAME_QUOTED = re.compile(r'"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)"|\'([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\'')
# Multi-word capitalized sequences
NAME_MULTI = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
# Single capitalized tokens (light heuristic)
NAME_SINGLE = re.compile(r"\b([A-Z][a-z]{2,})\b")
STOP = {
    # question words / articles / prepositions / conjunctions
    "What","When","Where","Which","Who","Why","How","The","In","On","For","And","Or","Of","Last","Past","Previous","Most","Recent","Latest",
    # pronouns / possessives
    "I","We","Us","Me","My","Our","Ours","You","Your","Yours","He","She","They","Them","His","Her","Their",
    # common non-name tokens seen in titles
    "Notes","Note","Quick","Catch","Up","Catch-up","Todo","To","Do","Tasks","Task","Meeting","Meet","Journal","Daily","Weights","Ideas","Agent","Talk","Talked","About","From","With","At",
}


def extract_people_from_text(text: str) -> List[str]:
    people: List[str] = []
    for m in NAME_QUOTED.finditer(text):
        g = m.group(1) or m.group(2)
        if g:
            people.append(g.strip())
    for m in NAME_MULTI.finditer(text):
        people.append(m.group(1).strip())
    for m in NAME_SINGLE.finditer(text):
        tok = m.group(1)
        if tok not in STOP:
            people.append(tok)
    # De-dup preserving order (case-insensitive unique)
    seen = set()
    out: List[str] = []
    for p in people:
        pl = p.strip()
        if not pl:
            continue
        key = pl.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(pl)
    return out


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
