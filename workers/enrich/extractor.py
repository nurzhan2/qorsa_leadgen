"""Pure HTML -> contact candidates. No network, no I/O - every rule here is
testable on a string.

Per page it produces:
  - emails        - from mailto: links, schema.org JSON-LD, and the VISIBLE text;
  - phones        - from tel: links, JSON-LD, and the visible text (RU/KZ, +7 / 8);
  - messengers    - t.me / wa.me / api.whatsapp.com links;
  - contact_links - up to N same-site links that look like a contacts page.

Deliberately NOT done (README "Вежливость"): no JavaScript rendering, and no
decoding of obfuscated addresses (Cloudflare `data-cfemail`, "info [at]
site.ru"). A site that hides its email from bots has made a choice, and we
take it at its word. <script>/<style> contents and tag attributes are never
scanned either - that is where analytics ids, Sentry DSNs, bundler junk and
form placeholders ("example@mail.ru") live.
"""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .domains import base_domain

# ---------------------------------------------------------------- decoding

_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
# Server defaults that are routinely wrong for Cyrillic pages (Apache's
# ISO-8859-1 in particular) - the page's own <meta> is trusted over these.
_UNTRUSTED_HEADER_CHARSETS = frozenset({"iso-8859-1", "latin-1", "latin1", "windows-1252", "cp1252",
                                        "us-ascii", "ascii"})


def decode_html(body: bytes, declared_charset: str | None = None) -> str:
    """Bytes -> text: the HTTP header's charset, then the page's own <meta
    charset>, then UTF-8. An undeclared page that isn't valid UTF-8 is decoded
    as windows-1251 - on RU/KZ sites that's what an undeclared legacy page
    almost always is. Emails and phones are ASCII and survive any of these;
    the encoding matters for Cyrillic link text ("Контакты")."""
    if not body:
        return ""
    header = (declared_charset or "").strip().lower() or None
    meta_match = _META_CHARSET_RE.search(body[:4096])
    meta = meta_match.group(1).decode("ascii", errors="ignore").lower() if meta_match else None

    order = [meta, header] if header in _UNTRUSTED_HEADER_CHARSETS else [header, meta]
    for encoding in (*order, "utf-8"):
        if not encoding:
            continue
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("windows-1251", errors="replace")


# ---------------------------------------------------------------- emails

_EMAIL_CORE = (r"[a-z0-9][a-z0-9._%+-]{0,63}@"
               r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,24}|xn--[a-z0-9-]{2,59})")
_EMAIL_IN_TEXT = re.compile(r"(?<![a-z0-9._%+-])(" + _EMAIL_CORE + r")(?![a-z0-9-])", re.IGNORECASE)
_EMAIL_EXACT = re.compile(_EMAIL_CORE, re.IGNORECASE)

# "logo@2x.png": a retina asset name that happens to be email-shaped.
_ASSET_EXTENSIONS = frozenset({
    "png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "ico", "bmp", "tif", "tiff",
    "css", "js", "mjs", "map", "json", "woff", "woff2", "ttf", "eot", "otf",
    "mp4", "webm", "mp3", "pdf", "php", "html", "htm", "xml", "txt",
})
_NOREPLY_LOCALS = frozenset({
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "do_not_reply",
    "mailer-daemon", "bounce", "bounces", "postmaster", "hostmaster", "abuse",
})
_NOREPLY_PREFIXES = ("noreply", "no-reply", "no_reply", "donotreply", "do-not-reply")
_PLACEHOLDER_LOCALS = frozenset({
    "example", "test", "testing", "user", "username", "yourname", "your", "youremail",
    "your.email", "yourmail", "name", "sample", "xxx", "xxxx",
})
_PLACEHOLDER_DOMAINS = frozenset({
    "example.com", "example.org", "example.net", "example.ru", "example.kz", "test.com", "test.ru",
    "domain.com", "domain.ru", "domain.kz", "yourdomain.com", "yoursite.com", "mysite.ru",
    "site.ru", "site.com", "sait.ru", "vashsait.ru", "company.com", "email.com",
})
# Hosting, CMS, site builders, widgets, error trackers: their addresses turn
# up in footers and scripts of sites built on them, and are never the
# company's contact.
_SERVICE_DOMAINS = frozenset({
    "sentry.io", "wixpress.com", "wix.com", "tilda.cc", "tilda.ws", "tildacdn.com",
    "beget.com", "beget.ru", "timeweb.ru", "timeweb.com", "reg.ru", "nic.ru", "jino.ru",
    "sprinthost.ru", "masterhost.ru", "hostland.ru", "fornex.com", "ps.kz", "hoster.kz",
    "1c-bitrix.ru", "bitrix24.ru", "bitrix24.kz", "wordpress.org", "wordpress.com", "joomla.org",
    "drupal.org", "opencart.com", "modx.com", "insales.ru", "nethouse.ru", "ukit.com", "ucoz.ru",
    "ucoz.net", "flexbe.com", "lpgenerator.ru", "squarespace.com", "godaddy.com", "cloudflare.com",
    "google.com", "googlegroups.com", "yandex-team.ru", "amocrm.ru", "jivosite.com", "jivo.ru",
    "carrotquest.io", "livetex.ru",
})
# Free mail providers: a small business often has nothing else, so these are
# kept - with lower priority and a note ("email на чужом домене mail.ru").
FREEMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "mail.ru", "bk.ru", "inbox.ru", "list.ru", "internet.ru",
    "yandex.ru", "yandex.kz", "yandex.by", "yandex.com", "ya.ru", "rambler.ru", "ro.ru",
    "yahoo.com", "outlook.com", "hotmail.com", "live.com", "icloud.com", "me.com",
    "proton.me", "protonmail.com", "mail.kz", "inbox.kz",
})
#: Mailbox names that mean "write to the company", in preference order.
PREFERRED_LOCALS = ("info", "sales", "office", "mail", "contact", "contacts", "hello", "zakaz", "order")
_HEX_ID = re.compile(r"[0-9a-f]{16,}")

TIER_OWN_PREFERRED = 0  # info@/sales@/... on the site's own domain
TIER_OWN = 1            # any other mailbox on the site's own domain
TIER_FREEMAIL = 2       # gmail.com, mail.ru, ...
TIER_FOREIGN = 3        # someone else's corporate domain
_SOURCE_RANK = {"mailto": 0, "jsonld": 0, "text": 1}


def normalize_email(value: str | None) -> str | None:
    """"mailto:Info%40Romashka.kz?subject=..." -> "info@romashka.kz"; None if
    what's left isn't an address."""
    if not value:
        return None
    candidate = unquote(value).strip().lower()
    if candidate.startswith("mailto:"):
        candidate = candidate[len("mailto:"):]
    candidate = candidate.split("?", 1)[0].strip().strip(".")
    return candidate if _EMAIL_EXACT.fullmatch(candidate) else None


def email_junk_reason(email: str) -> str | None:
    """Why an address is not a contact (None = keep it)."""
    local, _, domain = email.lower().rpartition("@")
    if domain.rsplit(".", 1)[-1] in _ASSET_EXTENSIONS:
        return "имя файла, а не адрес"
    if local in _NOREPLY_LOCALS or local.startswith(_NOREPLY_PREFIXES):
        return "служебный no-reply"
    if (local in _PLACEHOLDER_LOCALS or domain in _PLACEHOLDER_DOMAINS
            or domain.startswith("example.") or base_domain(domain) in _PLACEHOLDER_DOMAINS):
        return "шаблон-заглушка"
    if _HEX_ID.fullmatch(local):
        return "технический идентификатор"
    if _in_domain_set(domain, _SERVICE_DOMAINS):
        return "адрес хостинга/CMS/сервиса"
    return None


def _in_domain_set(domain: str, domains: frozenset[str]) -> bool:
    return domain in domains or any(domain.endswith("." + d) for d in domains)


@dataclass(frozen=True)
class EmailCandidate:
    email: str
    source: str  # "mailto" | "jsonld" | "text"
    page_url: str


@dataclass(frozen=True)
class RankedEmail:
    email: str
    tier: int
    source: str
    page_url: str

    @property
    def domain(self) -> str:
        return self.email.rsplit("@", 1)[1]

    @property
    def is_foreign(self) -> bool:
        """Not on the site's own domain - worth a note for whoever calls."""
        return self.tier >= TIER_FREEMAIL


def email_tier(email: str, own_bases: set[str]) -> int:
    local, _, domain = email.rpartition("@")
    if base_domain(domain) in own_bases:
        return TIER_OWN_PREFERRED if local in PREFERRED_LOCALS else TIER_OWN
    if _in_domain_set(domain, FREEMAIL_DOMAINS):
        return TIER_FREEMAIL
    return TIER_FOREIGN


def rank_emails(candidates: list[EmailCandidate], own_domains) -> list[RankedEmail]:
    """Best first: own-domain info@/sales@/office@/mail@/contact@ (in that
    order) > other own-domain mailboxes > free mail > other companies'
    domains. Ties: a mailto:/JSON-LD address beats one only seen in text,
    then page order. `own_domains` = the domain we were given plus wherever
    it redirected. Junk never ranks."""
    own_bases = {base_domain(domain) for domain in own_domains if domain}
    best: dict[str, tuple[tuple, EmailCandidate, int]] = {}
    for order, candidate in enumerate(candidates):
        if email_junk_reason(candidate.email):
            continue
        tier = email_tier(candidate.email, own_bases)
        local = candidate.email.split("@", 1)[0]
        preferred = PREFERRED_LOCALS.index(local) if local in PREFERRED_LOCALS else len(PREFERRED_LOCALS)
        key = (tier, preferred, _SOURCE_RANK.get(candidate.source, 2), order)
        if candidate.email not in best or key < best[candidate.email][0]:
            best[candidate.email] = (key, candidate, tier)
    return [RankedEmail(c.email, tier, c.source, c.page_url)
            for _, c, tier in sorted(best.values(), key=lambda item: item[0])]


# ---------------------------------------------------------------- phones

_PHONE_SEP = r"[\s‐-―().\-]"
# +7 or 8, then exactly ten digits with up to three separator characters
# between any two of them - covers 3-3-2-2, (4212) 12-34-56, 8 800 555-35-35
# and unbroken 89161234567 alike. Digits on either side void the match, which
# keeps ИНН/БИН/ОГРН/р/с runs from being read as phones.
_PHONE_IN_TEXT = re.compile(r"(?<![\d+])(?:\+\s?7|8)(?:" + _PHONE_SEP + r"{0,3}\d){10}(?!\d)")
# First digit of the 10-digit national number: 3/4/8 = RU geographic
# (8 also toll-free), 9 = RU mobile, 7 = Kazakhstan. 0/1/2/5/6 are not
# numbers anyone can call - usually a date, a price or an account number.
_NATIONAL_FIRST_DIGITS = frozenset("34789")


def normalize_phone(raw: str | None) -> str | None:
    """Any RU/KZ spelling -> "+7XXXXXXXXXX", or None. The core's PhoneUtil
    re-parses and classifies it; this only decides "is it a phone at all"."""
    value = (raw or "").strip()
    digits = re.sub(r"\D", "", value)
    if value.startswith("+"):
        # An explicit country code must be +7 followed by exactly ten digits:
        # a typo'd "+7 727 355 10 2" is NOT the ten-digit number 7727355102.
        if len(digits) != 11 or digits[0] != "7":
            return None
        national = digits[1:]
    elif len(digits) == 11 and digits[0] in "78":
        national = digits[1:]
    elif len(digits) == 10:
        national = digits
    else:
        return None
    if national[0] not in _NATIONAL_FIRST_DIGITS:
        return None
    return "+7" + national


@dataclass(frozen=True)
class PhoneCandidate:
    phone: str
    source: str  # "tel" | "jsonld" | "text"
    page_url: str


# ---------------------------------------------------------------- messengers

_TG_USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")
_TG_RESERVED = frozenset({
    "share", "joinchat", "addstickers", "addemoji", "addlist", "proxy", "socks", "iv",
    "setlanguage", "login", "boost", "invoice", "confirmphone",
})


@dataclass(frozen=True)
class Messenger:
    kind: str  # "whatsapp" | "telegram"
    url: str
    page_url: str


def messenger_from_href(href: str) -> tuple[str, str] | None:
    """A link -> ("whatsapp"|"telegram", canonical URL), or None. Share
    buttons, invite links and WhatsApp group chats are not contacts."""
    try:
        parts = urlsplit(href.strip())
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query = parse_qs(parts.query)
    first_segment = parts.path.strip("/").split("/", 1)[0]

    if scheme in ("http", "https") and host in ("t.me", "telegram.me", "telegram.dog"):
        return _telegram(first_segment)
    if scheme == "tg":
        return _telegram((query.get("domain") or [""])[0])
    if scheme in ("http", "https") and host == "wa.me":
        return _whatsapp(first_segment)
    if scheme in ("http", "https") and host in ("api.whatsapp.com", "web.whatsapp.com") \
            and parts.path.rstrip("/") in ("/send", ""):
        return _whatsapp((query.get("phone") or [""])[0])
    if scheme == "whatsapp":
        return _whatsapp((query.get("phone") or [""])[0])
    return None


def _telegram(name: str) -> tuple[str, str] | None:
    if not name or not _TG_USERNAME.fullmatch(name) or name.lower() in _TG_RESERVED:
        return None
    return "telegram", f"https://t.me/{name}"


def _whatsapp(raw: str) -> tuple[str, str] | None:
    digits = re.sub(r"\D", "", raw or "")
    if not 10 <= len(digits) <= 15:
        return None
    return "whatsapp", f"https://wa.me/{digits}"


def pick_messengers(messengers: list[Messenger]) -> list[Messenger]:
    """At most one of each kind, WhatsApp first: wa.me/<number> is a direct
    line to a person, while a t.me link on a website is as often a news
    channel."""
    picked = []
    for kind in ("whatsapp", "telegram"):
        match = next((m for m in messengers if m.kind == kind), None)
        if match is not None:
            picked.append(match)
    return picked


# ---------------------------------------------------------------- contact links

_CONTACT_PATTERNS = (
    (0, re.compile(r"(?<!\w)(?:контакт|contact|kontakt)")),
    (1, re.compile(r"(?<!\w)(?:связаться|свяжитесь|обратная связь|feedback|svyaz)")),
    (2, re.compile(r"(?<!\w)(?:о нас(?!\w)|о компании|about|o-nas|o-kompanii|o_kompanii|реквизит|rekvizit)")),
)
# Link text longer than this is an article title, not a menu item.
_MAX_LINK_TEXT = 60
_NON_HTML_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".jpg", ".jpeg",
                        ".png", ".gif", ".webp", ".svg", ".zip", ".rar", ".7z", ".mp4", ".mp3", ".txt",
                        ".xml", ".csv")


def contact_link_priority(link_text: str, path: str) -> int | None:
    """0 = contacts, 1 = "связаться"/feedback, 2 = about/реквизиты; None =
    not a contact-ish link. Matched on the link text or the URL path."""
    text = " ".join((link_text or "").lower().split())
    path = (path or "").lower()
    for priority, pattern in _CONTACT_PATTERNS:
        if (len(text) <= _MAX_LINK_TEXT and pattern.search(text)) or pattern.search(path):
            return priority
    return None


def _link_key(url: str) -> tuple[str, str, str]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host, parts.path.rstrip("/") or "/", parts.query


def find_contact_links(soup: BeautifulSoup, page_url: str, max_links: int = 3) -> list[str]:
    """Same-site links that look like a contacts/about page, best first.
    Skips other sites, anchors on the same page, mailto:/tel:/javascript:,
    documents and images."""
    page_base = base_domain(urlsplit(page_url).hostname or "")
    page_key = _link_key(page_url)
    found: list[tuple[int, int, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for order, anchor in enumerate(soup.find_all("a", href=True)):
        href = anchor["href"].strip()
        if not href or href.startswith("#"):
            continue
        parts = urlsplit(urljoin(page_url, href))
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        if base_domain(parts.hostname) != page_base:
            continue
        path = unquote(parts.path)
        if path.lower().endswith(_NON_HTML_EXTENSIONS):
            continue
        clean = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
        key = _link_key(clean)
        if key == page_key or key in seen:
            continue
        text = anchor.get_text(" ", strip=True) or anchor.get("title", "")
        priority = contact_link_priority(text, path)
        if priority is None:
            continue
        seen.add(key)
        found.append((priority, order, clean))

    found.sort()
    return [url for _, _, url in found[:max_links]]


# ---------------------------------------------------------------- one page

@dataclass
class PageContacts:
    url: str
    emails: list[EmailCandidate] = field(default_factory=list)
    phones: list[PhoneCandidate] = field(default_factory=list)
    messengers: list[Messenger] = field(default_factory=list)
    contact_links: list[str] = field(default_factory=list)
    # (email, reason) for every address thrown away - for logs and tests.
    rejected_emails: list[tuple[str, str]] = field(default_factory=list)


_INVISIBLE_TAGS = ["script", "style", "noscript", "template", "svg", "iframe", "object"]


def extract_page(html: str, page_url: str, max_links: int = 3) -> PageContacts:
    """Everything contact-shaped on one page. Structured sources (JSON-LD,
    mailto:/tel: links) are read first so they win over the same value seen
    in text; then the visible text is scanned."""
    soup = BeautifulSoup(html or "", "lxml")
    result = PageContacts(url=page_url)
    seen_emails: set[str] = set()
    seen_phones: set[str] = set()
    seen_messengers: set[str] = set()

    def add_email(raw: str, source: str) -> None:
        email = normalize_email(raw)
        if email is None or email in seen_emails:
            return
        seen_emails.add(email)
        reason = email_junk_reason(email)
        if reason:
            result.rejected_emails.append((email, reason))
        else:
            result.emails.append(EmailCandidate(email, source, page_url))

    def add_phone(raw: str, source: str) -> None:
        phone = normalize_phone(raw)
        if phone is not None and phone not in seen_phones:
            seen_phones.add(phone)
            result.phones.append(PhoneCandidate(phone, source, page_url))

    jsonld_emails, jsonld_phones = _jsonld_values(soup)
    for value in jsonld_emails:
        add_email(value, "jsonld")
    for value in jsonld_phones:
        add_phone(value, "jsonld")

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        lowered = href.lower()
        if lowered.startswith("mailto:"):
            for address in unquote(href[len("mailto:"):]).split("?", 1)[0].split(","):
                add_email(address, "mailto")
        elif lowered.startswith("tel:"):
            add_phone(unquote(href[len("tel:"):]), "tel")
        else:
            messenger = messenger_from_href(href)
            if messenger is not None and messenger[1] not in seen_messengers:
                seen_messengers.add(messenger[1])
                result.messengers.append(Messenger(messenger[0], messenger[1], page_url))

    if max_links > 0:
        result.contact_links = find_contact_links(soup, page_url, max_links)

    for tag in soup(_INVISIBLE_TAGS):
        tag.decompose()
    text = soup.get_text(" ")
    for match in _EMAIL_IN_TEXT.finditer(text):
        add_email(match.group(1), "text")
    for match in _PHONE_IN_TEXT.finditer(text):
        add_phone(match.group(0), "text")

    return result


def _jsonld_values(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    """email/telephone values from schema.org JSON-LD blocks - the most
    reliable contact data a page can carry, when it carries any."""
    emails: list[str] = []
    phones: list[str] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.IGNORECASE)}):
        try:
            data = json.loads(script.get_text() or "", strict=False)
        except (ValueError, TypeError):
            continue
        _walk_jsonld(data, emails, phones, depth=0)
    return emails, phones


def _walk_jsonld(node, emails: list[str], phones: list[str], depth: int) -> None:
    if depth > 20:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            name = str(key).lower()
            if name in ("email", "telephone"):
                values = value if isinstance(value, list) else [value]
                target = emails if name == "email" else phones
                target.extend(v for v in values if isinstance(v, str))
            else:
                _walk_jsonld(value, emails, phones, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _walk_jsonld(item, emails, phones, depth + 1)
