"""Decides whether a Telegram post reads as an order request.

Pure logic, no I/O: takes post text and a KeywordsConfig, returns a
MatchResult. Intentionally has no notion of "Telegram" at all so it's
trivial to unit test with plain string fixtures.
"""

import re
from dataclasses import dataclass, field

from .config import KeywordsConfig

_WHITESPACE_RE = re.compile(r"\s+")

# Below this length a post can't plausibly be a real order request even if a
# keyword happens to appear in it - guards against noise.
MIN_TEXT_LENGTH = 5


def normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "").strip().lower())


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    category: str | None
    weight: int
    budget_flag: bool
    matched_keywords: dict[str, list[str]] = field(default_factory=dict)


def _compile_keyword_patterns(keywords: list[str]) -> list[tuple[str, re.Pattern]]:
    """Whole-word/phrase boundary matching, NOT raw substring containment.

    Python's `\\b` is Unicode-aware by default (unlike Java's, which is
    ASCII-only), so this correctly treats Cyrillic letters as word
    characters. That matters here: a naive `kw in text` check would make a
    short keyword like "ии" match inside completely unrelated words such as
    "интеграции" or "информации" (both end in "-ии"), or "бот" match inside
    "разработчика" - `\\b` prevents both.
    """
    return [(kw, re.compile(r"\b" + re.escape(kw.lower()) + r"\b")) for kw in keywords]


class Matcher:
    """Stateless matcher bound to one loaded keyword dictionary. Patterns
    are compiled once up front since match() runs on every incoming
    message."""

    def __init__(self, keywords: KeywordsConfig):
        self._intent = keywords.intent
        self._domain = keywords.domain
        self._budget = keywords.budget
        self._intent_patterns = _compile_keyword_patterns(keywords.intent.keywords)
        self._domain_patterns = _compile_keyword_patterns(keywords.domain.keywords)
        self._budget_patterns = _compile_keyword_patterns(keywords.budget.keywords)

    def match(self, text: str) -> MatchResult:
        normalized = normalize_text(text)
        if len(normalized) < MIN_TEXT_LENGTH:
            return MatchResult(matched=False, category=None, weight=0, budget_flag=False)

        intent_hits = self._find_hits(normalized, self._intent_patterns)
        domain_hits = self._find_hits(normalized, self._domain_patterns)
        budget_hits = self._find_hits(normalized, self._budget_patterns)
        budget_flag = bool(budget_hits)

        # A post counts as an order request when it either states intent
        # directly, or mentions the topic together with a budget signal -
        # topic words alone are just chatter ("люблю красивые сайты").
        matched = bool(intent_hits) or (bool(domain_hits) and budget_flag)
        if not matched:
            return MatchResult(matched=False, category=None, weight=0, budget_flag=budget_flag)

        matched_keywords: dict[str, list[str]] = {}
        weight = 0
        if intent_hits:
            matched_keywords["intent"] = intent_hits
            weight += self._intent.weight * len(intent_hits)
        if domain_hits:
            matched_keywords["domain"] = domain_hits
            weight += self._domain.weight * len(domain_hits)
        if budget_hits:
            matched_keywords["budget"] = budget_hits
            weight += self._budget.weight * len(budget_hits)

        category = "intent" if intent_hits else "domain"
        return MatchResult(
            matched=True,
            category=category,
            weight=weight,
            budget_flag=budget_flag,
            matched_keywords=matched_keywords,
        )

    @staticmethod
    def _find_hits(text: str, patterns: list[tuple[str, re.Pattern]]) -> list[str]:
        return [kw for kw, pattern in patterns if pattern.search(text)]
