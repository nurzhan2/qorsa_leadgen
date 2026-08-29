"""Unit tests for parser.py - hand-built HTML fixtures matching the real
zakupki.gov.ru search-results markup (verified live against the actual
site: .search-registry-entry-block cards, .registry-entry__body-block
label/value pairs, .registry-entry__body-href for the customer link,
.price-block__value for the budget). No network.
"""

from workers.zakupki.parser import parse_money, parse_purchase_card, parse_search_html

ONE_CARD_HTML = """
<html><body>
<div class="search-registry-entry-block box-shadow-search-input">
  <div class="registry-entry__header-mid__number">
    <a href="/epz/order/notice/ea44/notice/common-info.html?regNumber=0123456789012345678">
        № 0123456789012345678
    </a>
  </div>
  <div class="registry-entry__body">
    <div class="registry-entry__body-block">
      <div class="registry-entry__body-title">Предмет закупки</div>
      <div class="registry-entry__body-value">Разработка <span class="highlightColor">сайта</span> для
        муниципального учреждения</div>
    </div>
    <div class="registry-entry__body-block">
      <div class="registry-entry__body-title">Регион поставки</div>
      <div class="registry-entry__body-value">Свердловская область</div>
    </div>
    <div class="registry-entry__body-block">
      <div class="registry-entry__body-title">Заказчик</div>
      <div class="registry-entry__body-href">
        <a href="/epz/organization/view/info.html?inn=6663012345&amp;kpp=667101001">
          МБУ "Городской информационный центр"
        </a>
      </div>
    </div>
  </div>
  <div class="price-block">
    <div class="price-block__title">Начальная цена контракта</div>
    <div class="price-block__value">1 500 000,00 &#8381;</div>
  </div>
</div>
</body></html>
"""


def test_parses_a_full_valid_card():
    purchases = parse_search_html(ONE_CARD_HTML)

    assert len(purchases) == 1
    purchase = purchases[0]
    assert purchase["reg_number"] == "0123456789012345678"
    assert purchase["detail_url"] == (
        "https://zakupki.gov.ru/epz/order/notice/ea44/notice/common-info.html?regNumber=0123456789012345678"
    )
    assert "сайта" in purchase["subject"]
    assert purchase["region"] == "Свердловская область"
    assert "Городской информационный центр" in purchase["customer_name"]
    assert purchase["inn"] == "6663012345"
    assert "1" in purchase["price"]


TWO_CARDS_HTML = """
<html><body>
<div class="search-registry-entry-block">
  <div class="registry-entry__header-mid__number">
    <a href="/epz/order/notice/x.html?regNumber=1">№ 1</a>
  </div>
</div>
<div class="search-registry-entry-block">
  <div class="registry-entry__header-mid__number">
    <a href="/epz/order/notice/x.html?regNumber=2">№ 2</a>
  </div>
</div>
</body></html>
"""


def test_parses_multiple_cards_on_one_page():
    purchases = parse_search_html(TWO_CARDS_HTML)

    assert [p["reg_number"] for p in purchases] == ["1", "2"]


def test_card_with_no_registration_number_is_skipped():
    html = """
    <div class="search-registry-entry-block">
      <div class="registry-entry__body">
        <div class="registry-entry__body-block">
          <div class="registry-entry__body-title">Предмет закупки</div>
          <div class="registry-entry__body-value">Что-то без номера</div>
        </div>
      </div>
    </div>
    """

    purchases = parse_search_html(html)

    assert purchases == []


def test_card_with_no_customer_link_still_returns_other_fields():
    html = """
    <div class="search-registry-entry-block">
      <div class="registry-entry__header-mid__number">
        <a href="/epz/order/notice/x.html?regNumber=999">№ 999</a>
      </div>
      <div class="registry-entry__body">
        <div class="registry-entry__body-block">
          <div class="registry-entry__body-title">Предмет закупки</div>
          <div class="registry-entry__body-value">Автоматизация без заказчика в разметке</div>
        </div>
      </div>
    </div>
    """

    purchases = parse_search_html(html)

    assert len(purchases) == 1
    assert purchases[0]["reg_number"] == "999"
    assert purchases[0]["customer_name"] is None
    assert purchases[0]["inn"] is None
    assert "Автоматизация" in purchases[0]["subject"]


def test_card_with_no_price_block_does_not_crash():
    html = """
    <div class="search-registry-entry-block">
      <div class="registry-entry__header-mid__number">
        <a href="/epz/order/notice/x.html?regNumber=42">№ 42</a>
      </div>
    </div>
    """

    purchases = parse_search_html(html)

    assert len(purchases) == 1
    assert purchases[0]["price"] is None
    assert purchases[0]["subject"] is None
    assert purchases[0]["region"] is None


def test_completely_unexpected_markup_does_not_raise():
    assert parse_search_html("<html><body><p>Ничего не найдено</p></body></html>") == []


def test_blank_or_none_input_does_not_raise():
    assert parse_search_html("") == []
    assert parse_search_html(None) == []


def test_absolute_detail_url_is_kept_as_is():
    """223-FZ (corporate procurement) result cards link with an already-
    absolute URL, unlike the relative paths 44-FZ (state procurement)
    cards use - both must work."""
    html = """
    <div class="search-registry-entry-block">
      <div class="registry-entry__header-mid__number">
        <a href="https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=1">№ 1</a>
      </div>
    </div>
    """

    purchases = parse_search_html(html)

    assert purchases[0]["detail_url"] == (
        "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=1"
    )


# --- parse_money() ---------------------------------------------------------


def test_parse_money_handles_spaces_comma_and_currency_sign():
    assert parse_money("8 398 003,33 ₽") == 8398003.33


def test_parse_money_handles_non_breaking_space():
    assert parse_money("1\xa0500\xa0000,00 ₽") == 1500000.0


def test_parse_money_handles_plain_dot_decimal():
    assert parse_money("1500000.50") == 1500000.5


def test_parse_money_blank_or_none_is_none():
    assert parse_money(None) is None
    assert parse_money("") is None
    assert parse_money("   ") is None


def test_parse_money_garbage_text_is_none():
    assert parse_money("цена по запросу") is None


# --- parse_purchase_card() --------------------------------------------------
#
# Fixture matches the real zakupki.gov.ru purchase detail/card page
# (verified live against an actual 44-FZ notice while building this):
# .cardMainInfo__title carries the law type as its own direct text with a
# nested <span> for the purchase method right after it; the summary header
# uses .cardMainInfo__section/-title/-content, while the sections further
# down the page (contact info, procedure info, ...) use a DIFFERENT but
# structurally analogous .blockInfo__section/section__title/section__info
# convention - parse_purchase_card understands both.

DETAIL_CARD_HTML = """
<html><body>
<div class="cardMainInfo row">
  <div class="sectionMainInfo borderRight col-6">
    <div class="sectionMainInfo__header">
      <div class="cardMainInfo__title d-flex text-truncate">44-ФЗ
        <span class="cardMainInfo__title distancedText ml-1">
          Запрос котировок в электронной форме
        </span>
      </div>
    </div>
    <div class="sectionMainInfo__body">
      <div class="cardMainInfo__section">
        <span class="cardMainInfo__title">Объект закупки</span>
        <span class="cardMainInfo__content text-break">Оказание комплекса услуг по разработке
          дизайн-концепции мини-игры «Тренажер закупок»</span>
      </div>
    </div>
  </div>
  <div class="sectionMainInfo borderRight col-3 colSpaceBetween">
    <div class="price">
      <span class="cardMainInfo__title">Начальная цена</span>
      <span class="cardMainInfo__content cost">8 398 003,33 ₽</span>
    </div>
    <div class="date">
      <div class="cardMainInfo__section">
        <span class="cardMainInfo__title">Окончание подачи заявок</span>
        <span class="cardMainInfo__content">28.08.2026</span>
      </div>
    </div>
  </div>
</div>

<div class="container">
  <div class="row blockInfo">
    <div class="col">
      <h2 class="blockInfo__title">Контактная информация</h2>
      <section class="blockInfo__section section">
        <span class="section__title">Организация, осуществляющая размещение</span>
        <span class="section__info">ДЕПАРТАМЕНТ ГОРОДА МОСКВЫ ПО КОНКУРЕНТНОЙ ПОЛИТИКЕ</span>
      </section>
      <section class="blockInfo__section section">
        <span class="section__title">Адрес электронной почты</span>
        <span class="section__info">mostender@mos.ru</span>
      </section>
      <section class="blockInfo__section section">
        <span class="section__title">Номер контактного телефона</span>
        <span class="section__info">7-495-9579977</span>
      </section>
      <section class="blockInfo__section section">
        <span class="section__title">Регион</span>
        <span class="section__info">Москва</span>
      </section>
    </div>
  </div>
</div>
</body></html>
"""


def test_parses_a_full_detail_card():
    details = parse_purchase_card(DETAIL_CARD_HTML)

    assert details["law_type"] == "44-ФЗ"
    assert "дизайн-концепции" in details["subject"]
    assert details["budget"] == 8398003.33
    assert details["deadline"] == "28.08.2026"
    assert details["customer_phone"] == "7-495-9579977"
    assert details["region"] == "Москва"


def test_customer_inn_is_none_when_not_labeled_as_such():
    """Verified live: a customer's own ИНН is often simply not present on
    this page for standard 44-FZ notices at all (the one "ИНН" label found
    while testing belonged to the Federal Treasury's payment routing
    details - a different entity - and must NOT be picked up here)."""
    details = parse_purchase_card(DETAIL_CARD_HTML)

    assert details["customer_inn"] is None


def test_treasury_inn_in_an_unrelated_table_is_not_mistaken_for_customer_inn():
    html = """
    <html><body>
    <section class="blockInfo__section">
        <span class="section__title">Объект закупки</span>
        <span class="section__info">Что-то</span>
    </section>
    <section class="blockInfo__section">
        <span class="section__title">Реквизиты счета для перечисления денежных средств</span>
        <span class="section__info">
          <span class="greyText">ИНН: </span><span>7704515009</span>
        </span>
    </section>
    </body></html>
    """

    details = parse_purchase_card(html)

    assert details["customer_inn"] is None


def test_all_fields_are_none_for_blank_or_empty_input():
    for value in (None, ""):
        details = parse_purchase_card(value)
        assert details == {
            "subject": None,
            "budget": None,
            "deadline": None,
            "law_type": None,
            "customer_phone": None,
            "customer_inn": None,
            "region": None,
        }


def test_completely_unexpected_detail_markup_does_not_raise():
    details = parse_purchase_card("<html><body><p>404</p></body></html>")

    assert all(value is None for value in details.values())


def test_missing_price_block_does_not_crash():
    html = """
    <div class="cardMainInfo__section">
      <span class="cardMainInfo__title">Объект закупки</span>
      <span class="cardMainInfo__content">Что-то</span>
    </div>
    """

    details = parse_purchase_card(html)

    assert details["budget"] is None
    assert details["subject"] == "Что-то"


# --- parse_purchase_card(), 223-FZ branch -----------------------------------
#
# Fixture copied from the markup of a REAL 223-FZ notice (verified live
# 2026-08-29, regNumber 32616327624 -
# zakupki.gov.ru/epz/order/notice/notice223/common-info.html), trimmed to the
# blocks the parser reads but with their structure and class names left exactly
# as the site emits them - including:
#   - label/value as SIBLINGS under a shared .col-9.mr-auto wrapper,
#   - the law type inline with the purchase method in ONE header string,
#   - the ИНН grey-label pattern where the LABEL itself also carries
#     .common-text__value (the trap a parent-scoped lookup falls into),
#   - a nested .common-text__value inside the organisation-name value,
#   - .price-block__value, the one class the 44-FZ and 223-FZ pages share.

DETAIL_223_HTML = """
<html><body>
<div class="col pr-0 d-flex align-headers-center">
  <div class="registry-entry__header-top__title">
      223-ФЗ Конкурс в электронной форме, участниками которого могут быть
      только субъекты малого и среднего предпринимательства
  </div>
</div>

<div class="common-text__caption">Сведения о закупке</div>
<div class="col-9 mr-auto">
  <div class="common-text__title">Реестровый номер извещения</div>
  <div class="common-text__value">32616327624</div>
</div>
<div class="col-9 mr-auto">
  <div class="common-text__title">Наименование закупки</div>
  <div class="common-text__value">
      Оказание работ по разработке, адаптации и модернизации программного
      обеспечения для ЭВМ и программирования баз данных с целью поискового
      продвижениям сайта ООО "Кристаллдиам"
  </div>
</div>

<div class="price-block">
  <div class="price-block__title">Начальная цена</div>
  <div class="price-block__value">400 000,00 &#8381;</div>
</div>

<div class="common-text__caption">Сведения о заказчике</div>
<div class="col-9 mr-auto">
  <div class="common-text__title">Наименование организации</div>
  <div class="common-text__value">
    <div class="common-text__value common-text__value_no-padding">
      <a href="/epz/organization/view223/info.html?inn=6731002565&amp;kpp=673101001">
        ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "КРИСТАЛЛДИАМ"</a>
    </div>
  </div>
</div>
<div class="row">
  <div class="col-4 d-flex">
    <div class="common-text__value common-text__value--gray">ИНН</div>
    <div class="ml-1 common-text__value">6731002565</div>
  </div>
  <div class="col d-flex">
    <div class="common-text__value common-text__value--gray">КПП</div>
    <div class="ml-1 common-text__value">673101001</div>
  </div>
  <div class="col-4 d-flex">
    <div class="common-text__value common-text__value--gray">ОГРН</div>
    <div class="ml-1 common-text__value">1026701429402</div>
  </div>
</div>

<div class="common-text__caption">Контактная информация</div>
<div class="col-9 mr-auto">
  <div class="common-text__title">Контактное лицо</div>
  <div class="common-text__value">Тантушян А.М.</div>
</div>
<div class="col-9 mr-auto">
  <div class="common-text__title">Адрес электронной почты</div>
  <div class="common-text__value">info@kristalldiam.ru</div>
</div>
<div class="col-9 mr-auto">
  <div class="common-text__title">Контактный телефон</div>
  <div class="common-text__value">84812311237</div>
</div>

<div class="common-text__caption">Порядок проведения процедуры</div>
<div class="col-9 mr-auto">
  <div class="common-text__title">Дата начала срока подачи заявок</div>
  <div class="common-text__value">27.08.2026 (МСК)</div>
</div>
<div class="col-9 mr-auto">
  <div class="common-text__title">Дата и время окончания срока подачи заявок
    (по местному времени заказчика)</div>
  <div class="common-text__value">04.09.2026 12:00 (МСК)</div>
</div>
</body></html>
"""

DETAIL_223_URL = (
    "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=32616327624"
)


def test_parses_a_full_223_detail_card():
    details = parse_purchase_card(DETAIL_223_HTML, url=DETAIL_223_URL)

    assert details["law_type"] == "223-ФЗ"
    assert "разработке" in details["subject"]
    assert "Кристаллдиам" in details["subject"]
    assert details["budget"] == 400000.0
    assert details["deadline"] == "04.09.2026 12:00 (МСК)"
    assert details["customer_phone"] == "84812311237"
    assert details["customer_inn"] == "6731002565"


def test_223_card_dispatches_on_markup_alone_without_a_url():
    """The URL is the authoritative discriminator, but parse_purchase_card
    must still pick the right branch when called with HTML only."""
    details = parse_purchase_card(DETAIL_223_HTML)

    assert details["law_type"] == "223-ФЗ"
    assert details["customer_phone"] == "84812311237"
    assert "разработке" in details["subject"]


def test_223_customer_inn_is_the_number_not_the_grey_label():
    """Regression guard for the actual trap in this template: the "ИНН"
    LABEL itself also carries .common-text__value, so a parent-scoped
    lookup returns the string "ИНН" instead of the digits. Must be the
    number, and must not pick up КПП/ОГРН from the same row."""
    details = parse_purchase_card(DETAIL_223_HTML, url=DETAIL_223_URL)

    assert details["customer_inn"] == "6731002565"
    assert details["customer_inn"] != "ИНН"
    assert details["customer_inn"] not in ("673101001", "1026701429402")


def test_223_region_is_none_because_the_template_has_no_region_field():
    """Verified live on three real 223-FZ notices: there is no "Регион"
    label anywhere on the page. The nearest thing is "Место нахождения", a
    full postal address, which mapper.py would feed straight into the
    lead's `city` - so this stays None deliberately rather than being
    faked from an address. Documented in README.md."""
    details = parse_purchase_card(DETAIL_223_HTML, url=DETAIL_223_URL)

    assert details["region"] is None


def test_223_law_type_falls_back_to_the_url_when_the_header_is_gone():
    """If the site restyles the header element away, a /223/ URL still
    tells us the law type unambiguously - that's the URL namespace, not a
    guess."""
    html = """
    <div class="col-9 mr-auto">
      <div class="common-text__title">Наименование закупки</div>
      <div class="common-text__value">Разработка сайта</div>
    </div>
    """

    details = parse_purchase_card(html, url=DETAIL_223_URL)

    assert details["law_type"] == "223-ФЗ"
    assert details["subject"] == "Разработка сайта"


def test_223_redirected_notice223_url_is_also_recognized():
    """A /223/purchase/... link 302-redirects to /epz/order/notice/notice223/...;
    whichever of the two the caller passes must dispatch the same way."""
    redirected = "https://zakupki.gov.ru/epz/order/notice/notice223/common-info.html?regNumber=32616327624"

    details = parse_purchase_card(DETAIL_223_HTML, url=redirected)

    assert details["law_type"] == "223-ФЗ"
    assert details["customer_inn"] == "6731002565"


def test_44_card_is_not_misdetected_as_223():
    """The 44-FZ branch must keep working now that dispatch exists - a
    44-FZ page carries none of the 223 template's classes (measured live:
    .common-text__title is 0 on 44-FZ pages, 29+ on 223-FZ ones)."""
    details = parse_purchase_card(DETAIL_CARD_HTML)

    assert details["law_type"] == "44-ФЗ"
    assert "дизайн-концепции" in details["subject"]
    assert details["budget"] == 8398003.33
    assert details["region"] == "Москва"


def test_44_card_with_an_explicit_url_still_uses_the_44_branch():
    details = parse_purchase_card(
        DETAIL_CARD_HTML,
        url="https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=0132300001726000686",
    )

    assert details["law_type"] == "44-ФЗ"
    assert details["customer_phone"] == "7-495-9579977"


def test_223_branch_returns_the_same_seven_keys_as_the_44_branch():
    """Callers (mapper.py) must never have to know which branch ran."""
    from_223 = parse_purchase_card(DETAIL_223_HTML, url=DETAIL_223_URL)
    from_44 = parse_purchase_card(DETAIL_CARD_HTML)

    assert set(from_223) == set(from_44)


def test_223_card_with_missing_blocks_does_not_crash():
    html = """
    <div class="registry-entry__header-top__title">223-ФЗ Запрос котировок</div>
    <div class="col-9 mr-auto">
      <div class="common-text__title">Наименование закупки</div>
      <div class="common-text__value">Только предмет и больше ничего</div>
    </div>
    """

    details = parse_purchase_card(html, url=DETAIL_223_URL)

    assert details["subject"] == "Только предмет и больше ничего"
    assert details["law_type"] == "223-ФЗ"
    assert details["budget"] is None
    assert details["deadline"] is None
    assert details["customer_phone"] is None
    assert details["customer_inn"] is None
