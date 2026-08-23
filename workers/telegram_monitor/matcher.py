"""Decides whether a Telegram post reads as an order request - and, just
as importantly for noisy job-board channels, whether it's actually a
hiring/vacancy post in disguise.

Pure logic, no network/Telegram dependency, so it's trivial to unit test
with plain string fixtures - the one exception is a DEBUG-level structlog
call per verdict (see the module docstring on Matcher.match), which does
not affect testability (nothing asserts on captured logs here).
"""

import re
from dataclasses import dataclass, field

import structlog

from .config import KeywordsConfig

log = structlog.get_logger(__name__)

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
    # True exactly when matched is True: named explicitly (rather than
    # just reusing `matched`) because it's the field monitor.py and
    # channel_rater.py actually key their behavior off - "does this read
    # as a one-off order" is the whole point of this module.
    is_order: bool
    # None unless a post that would otherwise have matched got flipped to
    # matched=False by the anti-hiring filter - see Matcher.match().
    rejected_reason: str | None = None
    matched_keywords: dict[str, list[str]] = field(default_factory=dict)
    matched_intent: list[str] = field(default_factory=list)
    matched_order: list[str] = field(default_factory=list)
    matched_anti: list[str] = field(default_factory=list)


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
    message.

    Deliberately biased toward catching too much rather than too little:
    intent alone (with no anti-hiring signal) is enough to match, and a
    single order_signals hit is enough to save a post from anti-hiring
    rejection even when several hiring markers are present. A manager
    skimming leads can dismiss a stray vacancy in a second; a missed order
    is gone for good. See README.md "Баланс: лучше лишний лид" for the
    full reasoning.
    """

    def __init__(self, keywords: KeywordsConfig):
        self._intent = keywords.intent
        self._domain = keywords.domain
        self._budget = keywords.budget
        self._intent_patterns = _compile_keyword_patterns(keywords.intent.keywords)
        self._domain_patterns = _compile_keyword_patterns(keywords.domain.keywords)
        self._budget_patterns = _compile_keyword_patterns(keywords.budget.keywords)
        self._order_patterns = _compile_keyword_patterns(keywords.order_signals)
        self._anti_hiring_patterns = _compile_keyword_patterns(keywords.anti_hiring)

    def match(self, text: str) -> MatchResult:
        normalized = normalize_text(text)
        if len(normalized) < MIN_TEXT_LENGTH:
            return MatchResult(matched=False, category=None, weight=0, budget_flag=False, is_order=False)

        intent_hits = self._find_hits(normalized, self._intent_patterns)
        domain_hits = self._find_hits(normalized, self._domain_patterns)
        budget_hits = self._find_hits(normalized, self._budget_patterns)
        order_hits = self._find_hits(normalized, self._order_patterns)
        anti_hits = self._find_hits(normalized, self._anti_hiring_patterns)
        budget_flag = bool(budget_hits)

        # A post counts as an order request when it either states intent
        # directly, or mentions the topic together with an order signal -
        # topic words alone are just chatter ("люблю красивые сайты").
        matched = bool(intent_hits) or (bool(domain_hits) and bool(order_hits))

        rejected_reason = None
        if matched and anti_hits and not order_hits:
            # Reads like a vacancy/hiring post ("в штат", "оклад", ...)
            # with nothing that reads like an actual one-off order - reject.
            # If even one order_signals keyword is also present, order
            # signals win (see class docstring) and matched stays True.
            matched = False
            rejected_reason = "hiring"

        if not matched:
            log.debug(
                "matcher.verdict",
                verdict="rejected" if rejected_reason else "noise",
                reason=rejected_reason,
                intent=intent_hits,
                order=order_hits,
                anti=anti_hits,
            )
            return MatchResult(
                matched=False,
                category=None,
                weight=0,
                budget_flag=budget_flag,
                is_order=False,
                rejected_reason=rejected_reason,
                matched_intent=intent_hits,
                matched_order=order_hits,
                matched_anti=anti_hits,
            )

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

        log.debug(
            "matcher.verdict",
            verdict="order",
            category=category,
            weight=weight,
            intent=intent_hits,
            order=order_hits,
            anti=anti_hits,
        )

        return MatchResult(
            matched=True,
            category=category,
            weight=weight,
            budget_flag=budget_flag,
            is_order=True,
            rejected_reason=None,
            matched_keywords=matched_keywords,
            matched_intent=intent_hits,
            matched_order=order_hits,
            matched_anti=anti_hits,
        )

    @staticmethod
    def _find_hits(text: str, patterns: list[tuple[str, re.Pattern]]) -> list[str]:
        return [kw for kw, pattern in patterns if pattern.search(text)]
