"""Employer filtering: who is NOT a lead.

Two name-based stop-lists (recruiting agencies, IT giants) plus two signals
that come from HH itself and are more reliable than any name list:

  - employer.type == "agency" / "private_recruiter" - HH's own
    classification (verified live against /dictionaries: `agency` =
    "Кадровое агентство", `private_recruiter` = "Частный рекрутер"). Only
    available when employer details are fetched.
  - employer.accredited_it_employer - HH's IT-accreditation flag, present on
    the search result itself. An accredited IT company has in-house
    developers by definition and will not outsource a website to us.

Name matching is WHOLE-WORD, not raw substring, exactly as in
workers/osm/chains.py and for the same reason: a substring match on "кадры"
also kills «Кадры Плюс Дизайн», and one on "вк" kills half the alphabet.
Over-filtering is a *silent* loss - the lead simply never appears.

Pure - no I/O, no network.
"""

import re

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(name: str | None) -> str:
    """Lowercase, collapse whitespace, unify ё/е and dash variants - Russian
    company names are written inconsistently across all three."""
    if not name:
        return ""
    value = _WHITESPACE_RE.sub(" ", name.strip().lower())
    value = value.replace("ё", "е")
    value = re.sub(r"[‐-―−]", "-", value)
    return value


def _compile_needle(needle: str) -> re.Pattern | None:
    """One stop-list entry -> a whole-word pattern.

    `\\b` is Unicode-aware in Python, so Cyrillic counts as word characters
    and "кадры" correctly fails to match inside "кадрырезерв". Internal
    spaces become `\\s+`. A needle starting/ending with a non-word character
    (e.g. "mail.ru") only gets a boundary on the side where one is
    meaningful.
    """
    normalized = normalize_name(needle)
    if not normalized:
        return None
    body = r"\s+".join(re.escape(word) for word in normalized.split(" "))
    prefix = r"\b" if re.match(r"\w", normalized[0]) else ""
    suffix = r"\b" if re.match(r"\w", normalized[-1]) else ""
    return re.compile(prefix + body + suffix)


class NameFilter:
    """Compiled once per run; called for every vacancy."""

    def __init__(self, needles: list[str], label: str):
        self.label = label
        self._patterns: list[tuple[str, re.Pattern]] = []
        for needle in needles or ():
            pattern = _compile_needle(needle)
            if pattern is not None:
                self._patterns.append((needle, pattern))

    def matched(self, name: str | None) -> str | None:
        """The stop-list entry this name hit, or None. Returning the entry
        rather than a bool makes the "why was this dropped" log actionable
        when tuning the list."""
        normalized = normalize_name(name)
        if not normalized:
            return None
        for needle, pattern in self._patterns:
            if pattern.search(normalized):
                return needle
        return None

    def __len__(self) -> int:
        return len(self._patterns)


class EmployerFilter:
    """All the reasons to drop an employer, in one place.

    `skip_reason(...)` returns a short machine-readable reason or None, so
    the runner can both count and explain every exclusion.
    """

    def __init__(
        self,
        agencies: list[str],
        giants: list[str],
        skip_types: list[str] | None = None,
        skip_accredited_it: bool = True,
    ):
        self.agency_names = NameFilter(agencies, "agency")
        self.giant_names = NameFilter(giants, "giant")
        self._skip_types = {t.strip().lower() for t in (skip_types or ()) if t.strip()}
        self._skip_accredited_it = skip_accredited_it

    def skip_reason(
        self,
        name: str | None,
        employer_type: str | None = None,
        accredited_it: bool | None = None,
    ) -> tuple[str, str] | None:
        """Returns (reason, detail) or None to keep.

        HH's own signals are checked FIRST: they're facts from the source,
        whereas a name match is our inference.
        """
        if employer_type and employer_type.strip().lower() in self._skip_types:
            return ("employer_type", employer_type.strip().lower())
        if self._skip_accredited_it and accredited_it:
            return ("accredited_it", "hh_it_accreditation")
        hit = self.agency_names.matched(name)
        if hit:
            return ("agency_name", hit)
        hit = self.giant_names.matched(name)
        if hit:
            return ("giant_name", hit)
        return None
