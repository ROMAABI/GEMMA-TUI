from __future__ import annotations

import re
from datetime import datetime

from core.client import LlamaClient

_CURRENT_YEAR = datetime.now().year

_REALTIME_PATTERNS = [
    "latest",
    "current",
    "recent",
    "newest",
    "breaking",
    "news",
    "updated",
    "announced",
    "release date",
    "release",
    "launch",
    "launched",
    "price",
    "pricing",
    "election",
    "winner",
    "score",
    "result",
    "upsc",
    "syllabus",
    "candidate",
    "ceo of",
    "president of",
    "prime minister of",
    "chief minister",
    "cm of",
    "minister of",
    "what happened",
]

_YEAR_RE = re.compile(r"\b(2\d{3})\b")

_OFFICE_RE = re.compile(
    r"\b(?:current|incumbent|new|latest|now)?\s*"
    r"(?:who (?:is|was) (?:the )?|what is the current|name (?:the|me) )?"
    r"(chief minister|prime minister|president|governor|mayor|chancellor|"
    r"speaker|minister|head of government|voivode)\s+of\s+(.+)$",
    re.IGNORECASE,
)

_OFFICE_PLURALS = {
    "chief minister": "chief ministers",
    "prime minister": "prime ministers",
    "president": "presidents",
    "governor": "governors",
    "mayor": "mayors",
    "chancellor": "chancellors",
    "speaker": "speakers",
    "minister": "ministers",
    "head of government": "heads of government",
}


def _office_query(question: str) -> str | None:
    """Rewrite office-holder questions to target Wikipedia 'List of ...' pages."""
    match = _OFFICE_RE.search(question.strip())
    if not match:
        return None
    office, place = match.group(1).strip().lower(), match.group(2).strip().rstrip("?!. ")
    office = _OFFICE_PLURALS.get(office, office)
    if not place:
        return None
    return f"List of {office} of {place}"


def _has_realtime_signal(question: str) -> bool:
    lowered = question.lower()
    if any(pattern in lowered for pattern in _REALTIME_PATTERNS):
        return True
    for year in _YEAR_RE.findall(question):
        if int(year) >= _CURRENT_YEAR - 2:
            return True
    return False


def _judge_prompt(question: str) -> str:
    today = datetime.now().strftime("%B %d, %Y")
    return (
        "<start_of_turn>system\n"
        f"Decide whether answering this question requires up-to-date information "
        f"that may fall after a model's training cutoff (current events, recent news, "
        f"new product releases, fresh stats, election results, prices, current office "
        f"holders, etc.). Today's date is {today}.\n"
        f"Reply with exactly YES or NO on the first line. If YES, also output a concise "
        f"search query (under 8 words) on the second line that would surface the current "
        f"facts. For questions about a current office holder, best results often come from "
        f"queries like 'List of <office> of <place>', e.g. 'List of chief ministers of "
        f"Tamil Nadu'.\n"
        "<end_of_turn>\n"
        f"<start_of_turn>user\n{question}<end_of_turn>\n"
        "<start_of_turn>model\n"
    )


async def decide_search(question: str, client: LlamaClient) -> tuple[bool, list[str]]:
    """Return (needs_web_search, candidate_queries_in_priority_order)."""
    heuristic = _has_realtime_signal(question)
    office_q = _office_query(question)
    try:
        raw = await client.complete(
            _judge_prompt(question), n_predict=16, temperature=0.0
        )
    except Exception:
        if office_q:
            return True, [office_q, question]
        return heuristic, [question] if heuristic else []
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    first = lines[0].upper() if lines else ""
    if first and first.split()[0].startswith("Y"):
        wants = True
    elif first and first.split()[0].startswith("N"):
        wants = False
    else:
        wants = heuristic
    if office_q:
        wants = True
    if not wants:
        return False, []
    query = ""
    if len(lines) >= 2:
        second = " ".join(lines[1:]).strip()
        second = second.split("<end")[0].strip()
        if second and not second.upper().startswith(("YES", "NO")):
            query = second[:80]
    candidates: list[str] = []
    for candidate in (office_q, query, question):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return True, candidates[:3]