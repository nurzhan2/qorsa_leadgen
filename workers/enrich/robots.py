"""robots.txt, per RFC 9309 (https://www.rfc-editor.org/rfc/rfc9309).

Not urllib.robotparser, deliberately: the stdlib parser does plain prefix
matching with no `*` / `$` support, and first-match-wins. RFC 9309 - and
every major crawler - use wildcards and longest-match-wins. Rules that are
common on real sites, like `Disallow: /*?sort=` or `Disallow: /*.pdf$`,
would be silently ignored by the stdlib parser, i.e. we would crawl exactly
what the site asked us not to.

What this implements:
  - groups: consecutive `User-agent:` lines share the rules that follow;
    the group(s) naming our product token win, else the `*` group(s), else
    everything is allowed. Groups for the same agent are merged.
  - matching: `*` = any sequence, trailing `$` = end of URL; path+query are
    compared percent-decoded; the LONGEST matching rule decides, and Allow
    wins a tie (RFC 9309 section 2.2.2).
  - `/robots.txt` itself is always allowed.
  - `Crawl-delay` is not in the RFC but is widely used on RU sites (Yandex
    honours it); it's read and respected, with a cap (fetcher.py).
  - status handling is in rules_for_status() below.

Pure: no network. fetcher.py does the HTTP.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import unquote

#: RFC 9309: crawlers must parse at least 500 KiB. Anything past this is ignored.
MAX_ROBOTS_BYTES = 512 * 1024


@dataclass(frozen=True)
class RobotsRules:
    allows: tuple[str, ...] = ()
    disallows: tuple[str, ...] = ()
    crawl_delay: float | None = None
    # Set when the rules were not parsed from a file but decided from the
    # response status ("robots.txt ответил 503 ..."); used in enrichNotes.
    detail: str = ""

    def blocking_rule(self, path: str) -> str | None:
        """The Disallow rule that forbids `path` (path plus "?query"), or None
        if it may be fetched."""
        target = _normalize(path or "/")
        if target == "/robots.txt":
            return None
        disallow_len, disallow_rule = _longest_match(self.disallows, target)
        if disallow_rule is None:
            return None
        allow_len, _ = _longest_match(self.allows, target)
        return None if allow_len >= disallow_len else disallow_rule

    def can_fetch(self, path: str) -> bool:
        return self.blocking_rule(path) is None


def allow_all(detail: str = "") -> RobotsRules:
    return RobotsRules(detail=detail)


def disallow_all(detail: str) -> RobotsRules:
    return RobotsRules(disallows=("/",), detail=detail)


def rules_for_status(status: int, body: bytes, product_token: str) -> RobotsRules:
    """What a /robots.txt response means, by status (after redirects):

      2xx          -> parse it.
      404, 410,
      other 4xx    -> "unavailable": no rules, everything allowed (RFC 9309 2.3.1.3).
      401/403/429  -> treated as "keep out". The RFC would allow these like any
                      4xx; we are stricter on purpose - a site that refuses to
                      show a bot its robots.txt will not welcome the bot either.
      5xx, other   -> "unreachable": assume complete disallow (RFC 9309 2.3.1.4).
    """
    if 200 <= status < 300:
        return parse_robots(body[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace"), product_token)
    if status in (401, 403, 429):
        return disallow_all(f"robots.txt ответил {status} — сайт не пускает роботов")
    if 400 <= status < 500:
        return allow_all(f"robots.txt отсутствует ({status})")
    return disallow_all(f"robots.txt недоступен ({status}) — по RFC 9309 считаем обход запрещённым")


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    allows: list[str] = field(default_factory=list)
    disallows: list[str] = field(default_factory=list)
    crawl_delay: float | None = None


def parse_robots(text: str, product_token: str) -> RobotsRules:
    token = _agent_token(product_token)
    groups: list[_Group] = []
    current: _Group | None = None
    in_agent_run = False

    for raw_line in text.lstrip("﻿").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if key == "user-agent":
            if current is None or not in_agent_run:
                current = _Group()
                groups.append(current)
            current.agents.append(_agent_token(value))
            in_agent_run = True
            continue

        if key not in ("allow", "disallow", "crawl-delay"):
            continue  # sitemap, host, clean-param, ...: not group members
        in_agent_run = False
        if current is None:
            continue  # rules before any User-agent line belong to no group
        if key == "crawl-delay":
            try:
                delay = float(value.replace(",", "."))
            except ValueError:
                continue
            if delay >= 0:
                current.crawl_delay = delay
        elif value:  # an empty "Disallow:" means nothing is disallowed
            rule = value if value.startswith(("/", "*")) else "/" + value
            (current.allows if key == "allow" else current.disallows).append(rule)

    chosen = [g for g in groups if token in g.agents] or [g for g in groups if "*" in g.agents]
    delays = [g.crawl_delay for g in chosen if g.crawl_delay is not None]
    return RobotsRules(
        allows=tuple(rule for g in chosen for rule in g.allows),
        disallows=tuple(rule for g in chosen for rule in g.disallows),
        crawl_delay=max(delays) if delays else None,
    )


def _agent_token(value: str) -> str:
    """"Qorsa-Leadgen-Enrich/1.0 (...)" -> "qorsa-leadgen-enrich"; "*" -> "*"."""
    return re.split(r"[/\s]", value.strip().lower(), maxsplit=1)[0]


def _normalize(value: str) -> str:
    return unquote(value)


@lru_cache(maxsize=4096)
def _compile(rule: str) -> re.Pattern:
    anchored = rule.endswith("$")
    body = rule[:-1] if anchored else rule
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(regex + ("$" if anchored else ""), re.DOTALL)


def _longest_match(rules: tuple[str, ...], target: str) -> tuple[int, str | None]:
    best_len, best_rule = -1, None
    for rule in rules:
        normalized = _normalize(rule)
        if len(normalized) > best_len and _compile(normalized).match(target):
            best_len, best_rule = len(normalized), rule
    return best_len, best_rule
