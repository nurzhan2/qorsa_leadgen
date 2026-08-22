"""Unit tests for twogis_client.parse_items() - hand-built fixture JSON,
no network, no real 2GIS API calls."""

from workers.twogis.twogis_client import parse_items


def test_parses_items_and_total_from_a_normal_response():
    payload = {
        "meta": {"code": 200},
        "result": {
            "items": [
                {"id": "1", "name": "Кофейня Ромашка"},
                {"id": "2", "name": "Sunrise Auto"},
            ],
            "total": 42,
        },
    }

    items, total = parse_items(payload)

    assert len(items) == 2
    assert items[0]["name"] == "Кофейня Ромашка"
    assert total == 42


def test_empty_items_list_yields_no_items():
    payload = {"result": {"items": [], "total": 0}}

    items, total = parse_items(payload)

    assert items == []
    assert total == 0


def test_missing_result_key_does_not_raise():
    payload = {"meta": {"code": 200}}

    items, total = parse_items(payload)

    assert items == []
    assert total == 0


def test_missing_items_key_does_not_raise():
    payload = {"result": {"total": 5}}

    items, total = parse_items(payload)

    assert items == []
    assert total == 5


def test_missing_total_falls_back_to_item_count():
    payload = {"result": {"items": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}}

    items, total = parse_items(payload)

    assert len(items) == 3
    assert total == 3


def test_completely_empty_payload_does_not_raise():
    items, total = parse_items({})

    assert items == []
    assert total == 0
