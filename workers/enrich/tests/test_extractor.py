"""Tests for extractor.py: a realistic contacts page and homepage
(tests/fixtures), plus small inline snippets for each rule. Pure - no
network.

The fixtures carry the junk a real page carries next to the real contacts
(no-reply notices, form placeholders, Sentry DSNs, retina asset names,
site-builder support addresses, requisites full of long digit runs), so
"finds the right thing" and "ignores the wrong thing" are tested on the same
page.
"""

import pytest

from workers.enrich.extractor import (
    TIER_FOREIGN,
    TIER_FREEMAIL,
    TIER_OWN,
    TIER_OWN_PREFERRED,
    EmailCandidate,
    Messenger,
    contact_link_priority,
    decode_html,
    email_junk_reason,
    extract_page,
    messenger_from_href,
    normalize_email,
    normalize_phone,
    pick_messengers,
    rank_emails,
)
from workers.enrich.tests.support import load_fixture

HOME_URL = "https://romashka.kz/"
CONTACTS_URL = "https://romashka.kz/kontakty/"


@pytest.fixture(scope="module")
def contacts_page():
    return extract_page(load_fixture("contacts_page.html"), CONTACTS_URL)


@pytest.fixture(scope="module")
def home_page():
    return extract_page(load_fixture("home_page.html"), HOME_URL)


def page(body: str, url: str = HOME_URL, max_links: int = 3):
    return extract_page(f"<html><body>{body}</body></html>", url, max_links=max_links)


def emails_in(body: str) -> list[str]:
    return [c.email for c in page(body).emails]


def phones_in(body: str) -> list[str]:
    return [p.phone for p in page(body).phones]


def cand(email: str, source: str = "text") -> EmailCandidate:
    return EmailCandidate(email, source, CONTACTS_URL)


# --- the fixture contacts page: what IS a contact ------------------------------


def test_contacts_page_yields_exactly_the_real_emails(contacts_page):
    assert [c.email for c in contacts_page.emails] == [
        "info@romashka.kz",       # JSON-LD (and a mailto:, and the text)
        "sales@romashka.kz",      # mailto: with a url-encoded ?subject=
        "romashka.buh@mail.ru",   # plain text only
    ]


def test_structured_sources_win_over_text(contacts_page):
    sources = {c.email: c.source for c in contacts_page.emails}
    assert sources == {"info@romashka.kz": "jsonld", "sales@romashka.kz": "mailto",
                       "romashka.buh@mail.ru": "text"}


def test_contacts_page_yields_the_phones_normalized_and_deduplicated(contacts_page):
    # +7 (727) 355-10-20 appears in JSON-LD, a tel: link and the text; the
    # 8-800 number as tel:88005553535 and as "8 800 555–35–35" (en dashes).
    assert [p.phone for p in contacts_page.phones] == ["+77273551020", "+78005553535", "+77012345678"]
    assert [p.source for p in contacts_page.phones] == ["jsonld", "tel", "text"]


def test_contacts_page_yields_whatsapp_and_telegram_but_not_the_share_button(contacts_page):
    assert [(m.kind, m.url) for m in contacts_page.messengers] == [
        ("whatsapp", "https://wa.me/77012345678"),
        ("telegram", "https://t.me/romashka_kz"),
    ]


# --- the fixture contacts page: what is NOT ------------------------------------


def test_junk_addresses_on_the_page_are_rejected_with_a_reason(contacts_page):
    rejected = dict(contacts_page.rejected_emails)
    assert rejected["noreply@romashka.kz"] == "служебный no-reply"
    assert rejected["support@tilda.cc"] == "адрес хостинга/CMS/сервиса"
    assert rejected["user@example.com"] == "шаблон-заглушка"


def test_scripts_and_attributes_are_never_scanned(contacts_page):
    seen = {c.email for c in contacts_page.emails} | {e for e, _ in contacts_page.rejected_emails}
    assert "robot@romashka.kz" not in seen             # inside <script>
    assert "example@mail.ru" not in seen               # form placeholder attribute
    assert not any("sentry" in email for email in seen)  # DSN inside <script>
    assert not any(email.endswith((".png", ".jpg")) for email in seen)  # src/srcset attributes


def test_requisites_dates_and_coordinates_are_not_phones(contacts_page):
    # БИН/ИИК/р/с/ОГРН, "15.08.2026", "09:00–18:00" and map coordinates all
    # sit on the page; none may come out as a phone.
    assert len(contacts_page.phones) == 3


def test_best_email_on_the_fixture_is_the_corporate_info(contacts_page):
    ranked = rank_emails(contacts_page.emails, {"romashka.kz"})

    assert [r.email for r in ranked] == ["info@romashka.kz", "sales@romashka.kz", "romashka.buh@mail.ru"]
    assert ranked[0].tier == TIER_OWN_PREFERRED and not ranked[0].is_foreign
    assert ranked[-1].tier == TIER_FREEMAIL and ranked[-1].is_foreign


# --- the fixture homepage: finding the contacts page ----------------------------


def test_homepage_contact_links_best_first(home_page):
    assert home_page.contact_links == [
        "https://romashka.kz/kontakty/",   # "Контакты"
        "https://romashka.kz/feedback/",   # "Связаться с нами"
        "https://romashka.kz/o-kompanii/", # "О компании"
    ]


def test_homepage_link_limit(home_page):
    limited = extract_page(load_fixture("home_page.html"), HOME_URL, max_links=1)
    assert limited.contact_links == ["https://romashka.kz/kontakty/"]


def test_homepage_phone_in_three_spellings_is_one_phone(home_page):
    # tel:+77273551020, "+7 (727) 355-10-20" and "8 (727) 355-10-20" - and the
    # "+7 (727) 000-00-00" inside the Metrika <script> is not scanned at all.
    assert [p.phone for p in home_page.phones] == ["+77273551020"]


def test_homepage_has_no_emails_and_no_messengers(home_page):
    assert home_page.emails == []
    assert home_page.messengers == []  # t.me/share is a button, not a contact


# --- email junk filter -----------------------------------------------------------


@pytest.mark.parametrize("email", [
    "noreply@romashka.kz",
    "no-reply@romashka.kz",
    "no_reply@romashka.kz",
    "donotreply@romashka.kz",
    "noreply-orders@romashka.kz",
    "mailer-daemon@romashka.kz",
    "example@romashka.kz",
    "user@example.com",
    "info@example.ru",
    "name@domain.ru",
    "test@test.com",
    "logo@2x.png",
    "icon-sprite@3x.webp",
    "photo@1x.jpg",
    "bundle@4.2.min.js",
    "3f2a9c1be4d84b1f9e0c7a6d5b4c3e21@o450123.ingest.sentry.io",
    "support@tilda.cc",
    "info@beget.com",
    "robot@sentry-next.wixpress.com",
    "abuse@reg.ru",
])
def test_junk_email_is_rejected(email):
    assert email_junk_reason(email) is not None


@pytest.mark.parametrize("email", [
    "info@romashka.kz",
    "sales@romashka.kz",
    "mail@romashka.kz",
    "romashka.buh@mail.ru",
    "director@romashka-group.ru",
    "office@xn--80aa0bc.xn--p1ai",
    "hello@romashka.io",
])
def test_real_email_is_kept(email):
    assert email_junk_reason(email) is None


def test_an_email_shaped_file_name_in_visible_text_is_rejected():
    result = page("<p>Схема проезда: map-scheme@2x.png</p><p>Почта: info@romashka.kz</p>")
    assert [c.email for c in result.emails] == ["info@romashka.kz"]
    assert dict(result.rejected_emails)["map-scheme@2x.png"] == "имя файла, а не адрес"


@pytest.mark.parametrize("raw, expected", [
    ("mailto:Info@Romashka.KZ", "info@romashka.kz"),
    ("mailto:info%40romashka.kz?subject=hi", "info@romashka.kz"),
    ("info@romashka.kz.", "info@romashka.kz"),
    ("not an email", None),
    ("", None),
])
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


def test_email_glued_to_cyrillic_text_is_still_found():
    assert emails_in("<p>Почта:info@romashka.kzТелефон</p>") == ["info@romashka.kz"]


def test_obfuscated_addresses_are_deliberately_not_decoded():
    # A site that hides its email from bots has made a choice - see README.
    body = ('<p>info [at] romashka [dot] kz</p>'
            '<a href="/cdn-cgi/l/email-protection" data-cfemail="d1b8bfb7be">[email&#160;protected]</a>')
    assert emails_in(body) == []


# --- email priority ----------------------------------------------------------------


def test_priority_corporate_preferred_then_corporate_then_freemail_then_foreign():
    ranked = rank_emails([
        cand("ivan@romashka.kz"),
        cand("romashka@gmail.com", "mailto"),
        cand("partner@logistics.kz", "mailto"),
        cand("office@romashka.kz"),
        cand("info@romashka.kz"),
    ], {"romashka.kz"})

    assert [r.email for r in ranked] == [
        "info@romashka.kz", "office@romashka.kz", "ivan@romashka.kz",
        "romashka@gmail.com", "partner@logistics.kz",
    ]
    assert [r.tier for r in ranked] == [TIER_OWN_PREFERRED, TIER_OWN_PREFERRED, TIER_OWN,
                                        TIER_FREEMAIL, TIER_FOREIGN]


def test_preferred_mailboxes_follow_the_documented_order():
    ranked = rank_emails([cand(f"{local}@romashka.kz") for local in
                          ("contact", "mail", "office", "sales", "info")], {"romashka.kz"})
    assert [r.email.split("@")[0] for r in ranked] == ["info", "sales", "office", "mail", "contact"]


def test_corporate_email_counts_as_own_when_the_site_is_on_a_subdomain_or_www():
    for site in ("shop.romashka.kz", "www.romashka.kz"):
        ranked = rank_emails([cand("romashka@gmail.com"), cand("info@romashka.kz")], {site})
        assert ranked[0].email == "info@romashka.kz"


def test_the_redirect_target_domain_counts_as_own():
    ranked = rank_emails([cand("romashka@gmail.com"), cand("sales@romashka-group.com")],
                         {"romashka.kz", "romashka-group.com"})
    assert ranked[0].email == "sales@romashka-group.com"
    assert not ranked[0].is_foreign


def test_a_mailto_link_beats_plain_text_within_a_tier():
    ranked = rank_emails([cand("ivan@romashka.kz", "text"), cand("petr@romashka.kz", "mailto")],
                         {"romashka.kz"})
    assert ranked[0].email == "petr@romashka.kz"


def test_the_same_address_seen_twice_ranks_once_with_its_best_source():
    ranked = rank_emails([cand("info@romashka.kz", "text"), cand("info@romashka.kz", "mailto")],
                         {"romashka.kz"})
    assert len(ranked) == 1
    assert ranked[0].source == "mailto"


def test_junk_never_ranks():
    assert rank_emails([cand("noreply@romashka.kz"), cand("logo@2x.png")], {"romashka.kz"}) == []


# --- phones ------------------------------------------------------------------------


@pytest.mark.parametrize("spelling, expected", [
    ("+7 (727) 355-10-20", "+77273551020"),
    ("8 (727) 355-10-20", "+77273551020"),
    ("+7 701 234 56 78", "+77012345678"),
    ("8-701-234-56-78", "+77012345678"),
    ("+7(4212)12-34-56", "+74212123456"),
    ("8 800 555–35–35", "+78005553535"),
    ("+79161234567", "+79161234567"),
    ("89161234567", "+79161234567"),
    ("+7 916 123-45-67 доб. 204", "+79161234567"),
])
def test_ru_and_kz_phone_spellings_in_text(spelling, expected):
    assert phones_in(f"<p>Тел.: {spelling}</p>") == [expected]


@pytest.mark.parametrize("not_a_phone", [
    "БИН 180540012345",
    "р/с 40702810900000012345",
    "ИИК KZ12345678901234567890",
    "Обновлено 15.08.2026 в 18:00",
    "Цена 8 000 000 тг",
    "+7 (012) 345-67-89",           # no RU/KZ number starts with 0
    "Артикул 8-123-456-78-90",      # ...or with 1
    "+1 (212) 555-01-23",
])
def test_digit_runs_that_are_not_phones(not_a_phone):
    assert phones_in(f"<p>{not_a_phone}</p>") == []


@pytest.mark.parametrize("href, expected", [
    ("tel:+7-727-355-10-20", "+77273551020"),
    ("tel:87273551020", "+77273551020"),
    ("tel:7273551020", "+77273551020"),
    ("tel:%2B77012345678", "+77012345678"),
    ("tel:+1-212-555-0123", None),
])
def test_tel_links(href, expected):
    result = phones_in(f'<a href="{href}">позвонить</a>')
    assert result == ([expected] if expected else [])


def test_normalize_phone_rejects_wrong_lengths():
    assert normalize_phone("+7 727 355 10 2") is None   # a digit short, NOT a bare 10-digit number
    assert normalize_phone("+7 727 355 10 200") is None
    assert normalize_phone("+49 30 1234567") is None
    assert normalize_phone("") is None
    assert normalize_phone(None) is None


def test_a_truncated_tel_link_is_not_turned_into_a_different_number():
    assert phones_in('<a href="tel:+7 727 355 10 2">позвонить</a>') == []


# --- messengers ----------------------------------------------------------------------


@pytest.mark.parametrize("href, expected", [
    ("https://t.me/romashka_kz", ("telegram", "https://t.me/romashka_kz")),
    ("https://telegram.me/RomashkaBot", ("telegram", "https://t.me/RomashkaBot")),
    ("tg://resolve?domain=romashka_kz", ("telegram", "https://t.me/romashka_kz")),
    ("https://wa.me/77012345678?text=hi", ("whatsapp", "https://wa.me/77012345678")),
    ("https://api.whatsapp.com/send?phone=+77012345678&text=hi", ("whatsapp", "https://wa.me/77012345678")),
    ("whatsapp://send?phone=77012345678", ("whatsapp", "https://wa.me/77012345678")),
])
def test_messenger_links(href, expected):
    assert messenger_from_href(href) == expected


@pytest.mark.parametrize("href", [
    "https://t.me/share/url?url=https%3A%2F%2Fromashka.kz",
    "https://t.me/joinchat/AAAAAEk3hS1",
    "https://t.me/+AbCdEf123456",
    "https://t.me/ab",
    "https://chat.whatsapp.com/InviteCode123",
    "https://wa.me/message/ABCDEF123",
    "https://romashka.kz/telegram/",
])
def test_not_messenger_contacts(href):
    assert messenger_from_href(href) is None


def test_whatsapp_is_preferred_over_telegram_and_one_of_each_is_kept():
    picked = pick_messengers([
        Messenger("telegram", "https://t.me/romashka_kz", HOME_URL),
        Messenger("whatsapp", "https://wa.me/77012345678", HOME_URL),
        Messenger("whatsapp", "https://wa.me/77019999999", HOME_URL),
    ])
    assert [m.url for m in picked] == ["https://wa.me/77012345678", "https://t.me/romashka_kz"]


# --- contact-link discovery ------------------------------------------------------------


@pytest.mark.parametrize("text, path, expected", [
    ("Контакты", "/x/", 0),
    ("", "/contacts/", 0),
    ("Контактная информация", "/info/", 0),
    ("", "/kontakty/", 0),
    ("Связаться с нами", "/x/", 1),
    ("Обратная связь", "/x/", 1),
    ("О нас", "/x/", 2),
    ("О компании", "/x/", 2),
    ("", "/about-us/", 2),
    ("Реквизиты", "/x/", 2),
    ("Всё о насосах", "/news/nasosy/", None),   # "о нас" must be a whole phrase
    ("ВКонтакте", "/x/", None),                  # "контакт" must start a word
    ("Каталог", "/catalog/", None),
])
def test_contact_link_priority(text, path, expected):
    assert contact_link_priority(text, path) == expected


def test_contact_links_skip_other_sites_anchors_documents_and_the_page_itself():
    result = page(
        '<a href="https://romashka.kz/">Контакты на главной</a>'
        '<a href="#contacts">Контакты</a>'
        '<a href="mailto:info@romashka.kz">Контакты</a>'
        '<a href="javascript:void(0)">Контакты</a>'
        '<a href="/files/kontakty.pdf">Контакты (PDF)</a>'
        '<a href="https://other.kz/contacts">Контакты</a>'
        '<a href="https://www.romashka.kz/contacts/#map">Контакты</a>'
        '<a href="/contacts/">Контакты ещё раз</a>',
        url="https://romashka.kz/")
    # www. is the same site; the #fragment is dropped; the duplicate collapses.
    assert result.contact_links == ["https://www.romashka.kz/contacts/"]


def test_a_long_article_title_is_not_a_menu_item():
    title = "Как связаться с нашим отделом логистики в праздничные дни и что делать, если"
    assert page(f'<a href="/news/123/">{title}</a>').contact_links == []


# --- encodings -------------------------------------------------------------------------


def test_undeclared_windows_1251_page_is_decoded_as_1251():
    body = "<html><body><a href='/k/'>Контакты</a> Тел.: 8 (727) 355-10-20</body></html>".encode("cp1251")
    assert "Контакты" in decode_html(body)


def test_meta_charset_is_honoured():
    body = '<html><head><meta charset="windows-1251"></head><body>О компании</body></html>'.encode("cp1251")
    assert "О компании" in decode_html(body)


def test_a_wrong_latin1_header_loses_to_the_pages_own_meta():
    body = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1251">'
            '</head><body>Контакты</body></html>').encode("cp1251")
    assert "Контакты" in decode_html(body, "ISO-8859-1")


def test_utf8_without_declaration():
    assert "Контакты" in decode_html("<p>Контакты</p>".encode("utf-8"))


def test_contact_link_found_on_a_1251_page_end_to_end():
    body = ('<html><head><meta charset="windows-1251"></head><body>'
            '<a href="/page/17/">Контакты</a></body></html>').encode("cp1251")
    result = extract_page(decode_html(body), HOME_URL)
    assert result.contact_links == ["https://romashka.kz/page/17/"]
