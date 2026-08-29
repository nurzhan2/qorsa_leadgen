"""Chain/franchise filter: drops national retail chains from the results.

Why: a Пятёрочка or a Сбербанк branch already has a website and an in-house
IT department. It is never going to buy a website from us, so it's not a
lead - it's noise that dilutes the list and costs the core a dedup pass.

Matching is WHOLE-WORD, not raw substring, and that distinction is
load-bearing: a substring match on "Магнит" also kills "Магнитогорская
аптека", and one on "Метро" kills "Кафе у метро". Both are exactly the small
independent businesses this pipeline exists to find, and losing them is
silent - they'd just never appear.

Pure - no I/O, no network.
"""

import re

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(name: str | None) -> str:
    """Lowercase, collapse whitespace, unify the ё/е and dash variants that
    Russian business names are written with inconsistently."""
    if not name:
        return ""
    value = _WHITESPACE_RE.sub(" ", name.strip().lower())
    value = value.replace("ё", "е")
    # Any dash variant (hyphen, en/em dash, minus) reads as a plain hyphen.
    value = re.sub(r"[‐-―−]", "-", value)
    return value


def _compile_needle(needle: str) -> re.Pattern | None:
    """One stop-list entry -> a whole-word pattern.

    Word boundaries are Unicode-aware in Python (unlike Java's ASCII-only
    \\b), so Cyrillic letters count as word characters and "магнит" correctly
    fails to match inside "магнитогорская".

    Internal whitespace becomes \\s+ so "красное и белое" matches however many
    spaces the mapper used. A needle that starts or ends with a non-word
    character (e.g. "h&m") can't take a \\b on that side - the boundary is
    only added where it's meaningful.
    """
    normalized = normalize_name(needle)
    if not normalized:
        return None
    words = [re.escape(word) for word in normalized.split(" ")]
    body = r"\s+".join(words)
    prefix = r"\b" if re.match(r"\w", normalized[0]) else ""
    suffix = r"\b" if re.match(r"\w", normalized[-1]) else ""
    return re.compile(prefix + body + suffix)


class ChainFilter:
    """Compiled once per run - is_chain() is called for every element."""

    def __init__(self, stoplist: list[str]):
        self._patterns: list[tuple[str, re.Pattern]] = []
        for needle in stoplist:
            pattern = _compile_needle(needle)
            if pattern is not None:
                self._patterns.append((needle, pattern))

    def matched_chain(self, name: str | None) -> str | None:
        """The stop-list entry this name matched, or None. Returning the
        entry rather than a bool makes the "why was this dropped?" log line
        useful when tuning the list."""
        normalized = normalize_name(name)
        if not normalized:
            return None
        for needle, pattern in self._patterns:
            if pattern.search(normalized):
                return needle
        return None

    def is_chain(self, name: str | None) -> bool:
        return self.matched_chain(name) is not None

    def __len__(self) -> int:
        return len(self._patterns)
