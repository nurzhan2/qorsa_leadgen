"""Pulls a contact route out of a matched post's own text.

We only ever read what the *author* voluntarily put in their own post (an
@username, a t.me link, a phone number, an email) - never anything looked
up about them via the API. If the post itself carries no contact, we fall
back to the message sender's own username (still something they chose to
make public), and only give up entirely (raw.no_direct_contact=true) when
neither is available - the core still ingests the lead in that case, it
just scores it without a direct contact route.
"""

import re
from dataclasses import dataclass

_USERNAME_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})")
_TME_LINK_RE = re.compile(
    r"(?:https?://)?t\.me/(?!joinchat\b|\+)([A-Za-z][A-Za-z0-9_]{4,31})(?:/\d+)?",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Loose scan for phone-shaped runs of digits/separators; normalize_phone()
# below is what actually decides whether a candidate is a real phone number.
_PHONE_CANDIDATE_RE = re.compile(r"\+?\d[\d\-\s()]{8,16}\d")


@dataclass(frozen=True)
class ContactInfo:
    messenger: str | None = None
    phone: str | None = None
    email: str | None = None
    no_direct_contact: bool = False


def normalize_phone(raw: str) -> str | None:
    """Digits only, last 10 kept - mirrors the Java core's
    NormalizationUtil.normalizePhone so the same number normalizes to the
    same dedup key on both sides."""
    digits = re.sub(r"\D+", "", raw)
    if len(digits) < 10:
        return None
    return digits[-10:]


def _extract_messenger(text: str) -> str | None:
    match = _USERNAME_RE.search(text)
    if match:
        return f"https://t.me/{match.group(1)}"
    match = _TME_LINK_RE.search(text)
    if match:
        return f"https://t.me/{match.group(1)}"
    return None


def _extract_phone(text: str) -> str | None:
    for candidate in _PHONE_CANDIDATE_RE.findall(text):
        normalized = normalize_phone(candidate)
        if normalized:
            return normalized
    return None


def _extract_email(text: str) -> str | None:
    match = _EMAIL_RE.search(text)
    return match.group(0) if match else None


def extract_contact(text: str, sender_username: str | None = None) -> ContactInfo:
    text = text or ""
    messenger = _extract_messenger(text)
    phone = _extract_phone(text)
    email = _extract_email(text)

    if messenger or phone or email:
        return ContactInfo(messenger=messenger, phone=phone, email=email, no_direct_contact=False)

    if sender_username:
        return ContactInfo(messenger=f"https://t.me/{sender_username}", no_direct_contact=False)

    return ContactInfo(no_direct_contact=True)
