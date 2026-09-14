"""Host/domain helpers: turning a domain from the core into something
fetchable, deciding which hosts belong to one organisation, and refusing
hosts this worker must never fetch.

Pure string logic, except resolve_guard(), which does one DNS lookup.
"""

import asyncio
import ipaddress
import re
import socket

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")
_HOSTNAME_RE = re.compile(
    r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})")

# Second-level labels under which the registrable name sits one level deeper:
# romashka.com.kz, romashka.msk.ru, romashka.co.uk.
_SECOND_LEVEL_LABELS = frozenset({"com", "net", "org", "co", "edu", "gov", "ac", "biz", "msk", "spb", "pp"})

# Platforms, not a company's own website. HH's site_url in particular is
# often a VK/Instagram/taplink profile; after domain normalization that
# becomes plain "vk.com", and crawling it would "find" VK's own contacts.
PLATFORM_DOMAINS = frozenset({
    # social networks, messengers, video
    "vk.com", "vk.ru", "ok.ru", "facebook.com", "fb.com", "instagram.com", "t.me", "telegram.me",
    "wa.me", "whatsapp.com", "youtube.com", "youtu.be", "tiktok.com", "twitter.com", "x.com",
    "linkedin.com", "pinterest.com", "dzen.ru",
    # link-in-bio pages
    "taplink.cc", "taplink.ru", "linktr.ee", "hipolink.me", "mssg.me",
    # search, maps, directories, job boards, marketplaces
    "google.com", "goo.gl", "yandex.ru", "yandex.kz", "2gis.ru", "2gis.kz", "hh.ru", "hh.kz",
    "headhunter.ru", "superjob.ru", "avito.ru", "ozon.ru", "wildberries.ru", "kaspi.kz", "satu.kz",
    "olx.kz", "krisha.kz", "kolesa.kz", "flamp.ru", "zoon.ru", "yell.ru", "rusprofile.ru",
    "list-org.com", "checko.ru", "sbis.ru", "profi.ru", "youdo.com", "prodoctorov.ru", "booking.com",
})

_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet",
                      ".test", ".example", ".invalid")


def clean_domain(raw: str | None) -> str | None:
    """The core's `domain` -> a bare, lowercase, ASCII (punycode) host, or
    None when it isn't a usable public hostname. Tolerates a scheme, path,
    port, "user@" and trailing dot; IP literals and single-label names
    ("localhost") are rejected here - a company website has a domain."""
    if not raw or not raw.strip():
        return None
    value = raw.strip().lower()
    value = _SCHEME_RE.sub("", value)
    value = re.split(r"[/?#\s]", value, maxsplit=1)[0]
    value = value.rsplit("@", 1)[-1]
    value = value.split(":", 1)[0].strip(".")
    if not value:
        return None
    try:
        value = value.encode("idna").decode("ascii")  # ромашка.рф -> xn--80aa...xn--p1ai
    except UnicodeError:
        return None
    return value if _HOSTNAME_RE.fullmatch(value) else None


def base_domain(host: str | None) -> str:
    """Approximate registrable domain: shop.romashka.kz -> romashka.kz,
    romashka.com.kz -> romashka.com.kz. Good enough to tell "their own
    address" from "someone else's" without shipping the Public Suffix List."""
    host = (host or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL_LABELS and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def platform_of(host: str) -> str | None:
    """The platform a host belongs to (vk.com, taplink.cc, ...), or None for
    what looks like a company's own site."""
    host = host.lower()
    if host in PLATFORM_DOMAINS:
        return host
    base = base_domain(host)
    return base if base in PLATFORM_DOMAINS else None


def static_block_reason(host: str | None) -> str | None:
    """Why a host must not be fetched at all, judged from the name alone:
    IP literals, localhost, internal-only suffixes. Applied to every hop of
    a redirect chain, not just the starting domain - an external site
    redirecting us to 127.0.0.1 is exactly the case this exists for."""
    host = (host or "").lower().strip(".").strip("[]")
    if not host:
        return "пустой хост"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        return f"IP-адрес вместо домена ({ip})"
    if host == "localhost" or "." not in host or host.endswith(_INTERNAL_SUFFIXES):
        return f"внутренний хост ({host})"
    return None


async def resolve_guard(host: str) -> str | None:
    """static_block_reason(), plus a DNS check: a public-looking name that
    resolves to a private/loopback/link-local address is refused too.
    Domains here come from third parties (an HH employer types its own
    site_url), so the worker must not be steerable into the local network.

    A name that doesn't resolve at all is NOT refused here - the request
    itself then fails and is reported as "сайт недоступен", which is what it
    is."""
    reason = static_block_reason(host)
    if reason:
        return reason
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return None
    for *_, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        except ValueError:
            continue
        if not ip.is_global:
            return f"{host} указывает на внутренний адрес {ip}"
    return None
