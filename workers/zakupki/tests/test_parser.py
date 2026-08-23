"""Unit tests for parser.py - hand-built HTML fixtures matching the real
zakupki.gov.ru search-results markup (verified live against the actual
site: .search-registry-entry-block cards, .registry-entry__body-block
label/value pairs, .registry-entry__body-href for the customer link,
.price-block__value for the budget). No network.
"""

from workers.zakupki.parser import parse_search_html

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
