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
