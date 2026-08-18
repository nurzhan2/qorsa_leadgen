"""Unit tests for extractor.py - plain string fixtures, no network."""

from workers.telegram_monitor.extractor import extract_contact, normalize_phone


def test_extracts_at_username_as_messenger_link():
    contact = extract_contact("Нужен сайт, пишите @ivan_dev по любым вопросам")

    assert contact.messenger == "https://t.me/ivan_dev"
    assert contact.phone is None
    assert contact.email is None
    assert contact.no_direct_contact is False


def test_extracts_tme_link_as_messenger():
    contact = extract_contact("Нужен бот, контакт тут: https://t.me/ivan_dev")

    assert contact.messenger == "https://t.me/ivan_dev"


def test_extracts_bare_tme_link_without_scheme():
    contact = extract_contact("пишите на t.me/ivan_dev")

    assert contact.messenger == "https://t.me/ivan_dev"


def test_ignores_tme_joinchat_and_plus_invite_links():
    contact = extract_contact("вот наш чат https://t.me/+AbCdEfGhIjKl, велком")

    assert contact.messenger is None


def test_extracts_phone_with_plus7_and_separators():
    contact = extract_contact("нужен лендинг, звоните +7 (701) 111-22-33")

    assert contact.phone == "7011112233"


def test_extracts_phone_with_8_prefix():
    contact = extract_contact("нужен сайт, тел 8-701-111-22-33")

    assert contact.phone == "7011112233"


def test_extracts_email():
    contact = extract_contact("нужен сайт, пишите на info@example.kz")

    assert contact.email == "info@example.kz"
    # The email's local part must not be mistaken for an @username.
    assert contact.messenger is None


def test_extracts_multiple_contact_kinds_at_once():
    contact = extract_contact("нужен бот, @ivan_dev, тел +77011112233, mail info@example.kz")

    assert contact.messenger == "https://t.me/ivan_dev"
    assert contact.phone == "7011112233"
    assert contact.email == "info@example.kz"
    assert contact.no_direct_contact is False


def test_falls_back_to_sender_username_when_post_has_no_contact():
    contact = extract_contact("нужен сайт для магазина", sender_username="anna_shopper")

    assert contact.messenger == "https://t.me/anna_shopper"
    assert contact.phone is None
    assert contact.email is None
    assert contact.no_direct_contact is False


def test_marks_no_direct_contact_when_nothing_is_available():
    contact = extract_contact("нужен сайт для магазина", sender_username=None)

    assert contact.messenger is None
    assert contact.phone is None
    assert contact.email is None
    assert contact.no_direct_contact is True


def test_normalize_phone_keeps_last_10_digits():
    assert normalize_phone("+7 (701) 111-22-33") == "7011112233"
    assert normalize_phone("8 701 111 22 33") == "7011112233"
    assert normalize_phone("123") is None
    assert normalize_phone("") is None
