from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

try:
    import dateparser  # type: ignore[import]
except ImportError:  # pragma: no cover
    dateparser = None  # type: ignore[assignment]

ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
DMY_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
YMD_SLASH = re.compile(r"\b(\d{4})/(\d{1,2})/(\d{1,2})\b")
MON_D_COMMA_Y = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})\b")
D_MON_Y = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")

MONTHS = {
    'jan':1,'january':1,'feb':2,'february':2,'mar':3,'march':3,'apr':4,'april':4,
    'may':5,'jun':6,'june':6,'jul':7,'july':7,'aug':8,'august':8,'sep':9,'sept':9,'september':9,
    'oct':10,'october':10,'nov':11,'november':11,'dec':12,'december':12
}

RELATIVE_RE = re.compile(
    r"\b(today|yesterday|this week|last week|this month|last month|this year|last year|recent|recently|lately|just)\b",
    re.IGNORECASE,
)

LAST_N_RE = re.compile(
    r"\b(?:last|past|previous)\s+(?P<n>\d{1,3})\s+(?P<u>day|days|week|weeks|month|months|year|years)\b",
    re.IGNORECASE,
)
IN_THE_LAST_N_RE = re.compile(
    r"\bin\s+the\s+last\s+(?P<n>\d{1,3})\s+(?P<u>day|days|week|weeks|month|months|year|years)\b",
    re.IGNORECASE,
)
NUMBER_WORDS = {
    'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10,
    'eleven':11,'twelve':12,'thirteen':13,'fourteen':14,'fifteen':15,'sixteen':16,'seventeen':17,
    'eighteen':18,'nineteen':19,'twenty':20
}
LAST_WORD_N_RE = re.compile(
    r"\b(?:last|past|previous)\s+(?P<nw>" + '|'.join(NUMBER_WORDS.keys()) + r")\s+(?P<u>day|days|week|weeks|month|months|year|years)\b",
    re.IGNORECASE,
)
IN_THE_LAST_WORD_N_RE = re.compile(
    r"\bin\s+the\s+last\s+(?P<nw>" + '|'.join(NUMBER_WORDS.keys()) + r")\s+(?P<u>day|days|week|weeks|month|months|year|years)\b",
    re.IGNORECASE,
)
FORTNIGHT_RE = re.compile(r"\b(?:last|past|previous)?\s*fortnight\b", re.IGNORECASE)

_MON = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
# Matches "in April", "during March 2025", "April 2025" (year required when no preposition,
# to avoid treating the auxiliary verb "may" as a month name).
MONTH_ONLY_RE = re.compile(
    r"(?:(?:in|during|for|of)\s+(?P<mp>" + _MON + r")(?:\s+(?P<yp>\d{4}))?(?!\s*\d)"
    r"|(?P<mn>" + _MON + r")\s+(?P<yn>\d{4}))",
    re.IGNORECASE,
)

RANGE_RE = re.compile(
    r"\b(?:between\s+(?P<between_a>.+?)\s+and\s+(?P<between_b>.+?)|from\s+(?P<from_a>.+?)\s+(?:to|until)\s+(?P<from_b>.+?)|since\s+(?P<since>.+?)|after\s+(?P<after>.+?)|before\s+(?P<before>.+?))\b",
    re.IGNORECASE,
)

class DateParser:
    def _parse_month(self, name: str) -> Optional[int]:
        return MONTHS.get(name.strip().lower())

    def _to_iso_date(self, y: int, m: int, d: int) -> Optional[str]:
        try:
            return datetime(y, m, d).date().isoformat()
        except Exception:
            return None

    def _norm_date_token(self, token: str) -> Optional[str]:
        token = token.strip()
        m = ISO_DATE.search(token)
        if m:
            return m.group(0)
        m = DMY_SLASH.search(token)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return self._to_iso_date(y, mo, d)
        m = YMD_SLASH.search(token)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return self._to_iso_date(y, mo, d)
        m = MON_D_COMMA_Y.search(token)
        if m:
            mon = self._parse_month(m.group(1))
            d = int(m.group(2)); y = int(m.group(3))
            if mon:
                return self._to_iso_date(y, mon, d)
        m = D_MON_Y.search(token)
        if m:
            d = int(m.group(1)); mon = self._parse_month(m.group(2)); y = int(m.group(3))
            if mon:
                return self._to_iso_date(y, mon, d)
        # Simple "Month YYYY" like "Oct 2025"
        parts = token.split()
        if len(parts) == 2 and parts[1].isdigit():
            mon = self._parse_month(parts[0])
            if mon:
                y = int(parts[1])
                return self._to_iso_date(y, mon, 1)
        return None

    def _week_bounds(self, day: datetime) -> tuple[str, str]:
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=6)
        return start.date().isoformat(), end.date().isoformat()

    def _month_bounds(self, day: datetime) -> tuple[str, str]:
        start = day.replace(day=1)
        if start.month == 12:
            next_first = start.replace(year=start.year+1, month=1, day=1)
        else:
            next_first = start.replace(month=start.month+1, day=1)
        end = next_first - timedelta(days=1)
        return start.date().isoformat(), end.date().isoformat()

    def parse(self, q: str, tz_name: str) -> tuple[Optional[str], Optional[str]]:
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
        start: Optional[str] = None
        end: Optional[str] = None

        # Relative phrases
        rel = RELATIVE_RE.findall(q)
        if rel:
            for phrase in rel:
                p = phrase.lower()
                if p in ['today', 'just']:
                    d = now.date().isoformat()
                    start = start or d; end = end or d
                elif p == 'yesterday':
                    d = (now - timedelta(days=1)).date().isoformat()
                    start = start or d; end = end or d
                elif p in ['recent', 'recently', 'lately']:
                    s = (now - timedelta(days=30)).date().isoformat()
                    e = now.date().isoformat()
                    start = start or s; end = end or e
                elif p == 'this week':
                    s, e = self._week_bounds(now)
                    start = start or s; end = end or e
                elif p == 'last week':
                    last = now - timedelta(days=7)
                    s, e = self._week_bounds(last)
                    start = start or s; end = end or e
                elif p == 'this month':
                    s, e = self._month_bounds(now)
                    start = start or s; end = end or e
                elif p == 'last month':
                    this_start = now.replace(day=1)
                    prev_last = this_start - timedelta(days=1)
                    s, e = self._month_bounds(prev_last)
                    start = start or s; end = end or e
                elif p == 'this year':
                    s = datetime(now.year, 1, 1, tzinfo=tz).date().isoformat()
                    e = datetime(now.year, 12, 31, tzinfo=tz).date().isoformat()
                    start = start or s; end = end or e
                elif p == 'last year':
                    y = now.year - 1
                    s = datetime(y, 1, 1, tzinfo=tz).date().isoformat()
                    e = datetime(y, 12, 31, tzinfo=tz).date().isoformat()
                    start = start or s; end = end or e

        # Quantified relative windows
        for rex in (LAST_N_RE, IN_THE_LAST_N_RE):
            for m in rex.finditer(q):
                n = int(m.group('n'))
                u = m.group('u').lower()
                days = n
                if u.startswith('week'):
                    days = n * 7
                elif u.startswith('month'):
                    days = n * 30
                elif u.startswith('year'):
                    days = n * 365
                s = (now - timedelta(days=days)).date().isoformat()
                e = now.date().isoformat()
                start = start or s; end = end or e

        # Word-number windows
        for m in list(LAST_WORD_N_RE.finditer(q)) + list(IN_THE_LAST_WORD_N_RE.finditer(q)):
            n = NUMBER_WORDS.get(m.group('nw').lower(), 0)
            if n <= 0:
                continue
            u = m.group('u').lower()
            days = n
            if u.startswith('week'):
                days = n * 7
            elif u.startswith('month'):
                days = n * 30
            elif u.startswith('year'):
                days = n * 365
            s = (now - timedelta(days=days)).date().isoformat()
            e = now.date().isoformat()
            start = start or s; end = end or e

        # Fortnight (~14 days)
        if FORTNIGHT_RE.search(q):
            s = (now - timedelta(days=14)).date().isoformat()
            e = now.date().isoformat()
            start = start or s; end = end or e

        # Explicit ranges
        m = RANGE_RE.search(q)
        if m:
            if m.group('between_a') and m.group('between_b'):
                a = self._norm_date_token(m.group('between_a'))
                b = self._norm_date_token(m.group('between_b'))
                if a and b:
                    start = a; end = b
            if m.group('from_a') and m.group('from_b'):
                a = self._norm_date_token(m.group('from_a'))
                b = self._norm_date_token(m.group('from_b'))
                if a and b:
                    start = a; end = b
            if m.group('since'):
                a = self._norm_date_token(m.group('since'))
                if a:
                    start = a; end = end or now.date().isoformat();
            if m.group('after'):
                a = self._norm_date_token(m.group('after'))
                if a:
                    start = a;
            if m.group('before'):
                b = self._norm_date_token(m.group('before'))
                if b:
                    end = b;

        # Standalone explicit dates in text
        candidates = set()
        for rex in (ISO_DATE, DMY_SLASH, YMD_SLASH, MON_D_COMMA_Y, D_MON_Y):
            for mm in rex.finditer(q):
                candidates.add(mm.group(0))
        for tok in candidates:
            iso = self._norm_date_token(tok)
            if iso:
                if not start and not end:
                    start = end = iso

        # Bare month name: "in April", "during March 2025", "April 2025"
        if not start and not end:
            mm = MONTH_ONLY_RE.search(q)
            if mm:
                mon_str = mm.group('mp') or mm.group('mn')
                yr_str = mm.group('yp') or mm.group('yn')
                mon = self._parse_month(mon_str)
                yr = int(yr_str) if yr_str else now.year
                if mon:
                    start, end = self._month_bounds(datetime(yr, mon, 1, tzinfo=tz))

        # Normalize order
        if start and end and start > end:
            start, end = end, start

        if start or end:
            return start, end

        # dateparser fallback for phrases not covered by the regex rules above
        # (e.g. "a few weeks ago", "early March", "Q1 2025", "last Tuesday").
        # dateparser is pure-Python — no network call, no model load.
        try:
            parsed = dateparser.parse(
                q,
                settings={
                    "PREFER_DATES_FROM": "past",
                    "PREFER_DAY_OF_MONTH": "first",
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "TIMEZONE": tz_name,
                    "TO_TIMEZONE": tz_name,
                },
            )
            if parsed:
                d = parsed.date().isoformat()
                return d, d
        except Exception:
            pass
        return None, None
