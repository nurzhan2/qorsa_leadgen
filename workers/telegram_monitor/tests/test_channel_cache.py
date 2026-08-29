"""Unit tests for channel_cache.py - pure JSON/TTL logic, no Telegram, no
network. Every test writes into pytest's tmp_path, never the real
channel_cache.json.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from workers.telegram_monitor.channel_cache import (
    STATUS_NOT_FOUND,
    STATUS_NOT_JOINED,
    STATUS_OK,
    ChannelCache,
    build_report_rows,
    is_stale,
    load_cache,
    make_entry,
    normalize_key,
    render_report,
    save_cache,
)


def iso_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# --- normalize_key ---------------------------------------------------------


def test_normalize_key_strips_at_sign_and_lowercases():
    # Telegram usernames are case-insensitive; without this, "@ForDev" and
    # "fordev" would be two entries and each would get resolved separately.
    assert normalize_key("@ForDev") == "fordev"
    assert normalize_key("fordev") == "fordev"
    assert normalize_key("  @FORDEV  ") == "fordev"


def test_normalize_key_handles_numeric_ids():
    assert normalize_key(-1001234567890) == "-1001234567890"


# --- make_entry / is_stale -------------------------------------------------


def test_make_entry_stamps_resolved_at_and_keeps_fields():
    entry = make_entry(status=STATUS_OK, id=-1001, access_hash=42, title="Тест")

    assert entry["status"] == STATUS_OK
    assert entry["id"] == -1001
    assert entry["access_hash"] == 42
    assert entry["title"] == "Тест"
    assert entry["resolved_at"]
    # Parseable and timezone-aware.
    assert datetime.fromisoformat(entry["resolved_at"]).tzinfo is not None


def test_fresh_entry_is_not_stale():
    entry = make_entry(status=STATUS_OK, id=-1001, resolved_at=iso_days_ago(1))

    assert is_stale(entry, ttl_days=30) is False


def test_entry_past_the_ttl_is_stale():
    entry = make_entry(status=STATUS_OK, id=-1001, resolved_at=iso_days_ago(31))

    assert is_stale(entry, ttl_days=30) is True


def test_entry_exactly_inside_the_ttl_is_not_stale():
    entry = make_entry(status=STATUS_OK, id=-1001, resolved_at=iso_days_ago(29.9))

    assert is_stale(entry, ttl_days=30) is False


def test_negative_statuses_also_expire_rather_than_being_permanent():
    """A dead channel shouldn't be retried every start - but it also
    shouldn't be written off forever, since usernames get reused and people
    join channels later."""
    fresh = make_entry(status=STATUS_NOT_JOINED, resolved_at=iso_days_ago(1))
    old = make_entry(status=STATUS_NOT_JOINED, resolved_at=iso_days_ago(45))

    assert is_stale(fresh, ttl_days=30) is False
    assert is_stale(old, ttl_days=30) is True


@pytest.mark.parametrize(
    "entry",
    [
        None,
        {},
        {"status": "ok"},                                  # no resolved_at
        {"status": "ok", "resolved_at": "not-a-date"},     # unparseable
        {"status": "weird", "resolved_at": iso_days_ago(1)},  # unknown status
        "not-a-dict",
    ],
)
def test_missing_or_malformed_entries_count_as_stale(entry):
    # Fail towards "ask Telegram again", never towards trusting garbage.
    assert is_stale(entry, ttl_days=30) is True


def test_zero_ttl_expires_everything_immediately():
    entry = make_entry(status=STATUS_OK, id=-1001, resolved_at=iso_days_ago(0))

    assert is_stale(entry, ttl_days=0) is True


def test_naive_timestamp_is_treated_as_utc_instead_of_crashing():
    naive = datetime.now() - timedelta(days=1)  # noqa: DTZ005 - deliberately naive
    entry = {"status": STATUS_OK, "resolved_at": naive.isoformat(), "id": -1}

    assert is_stale(entry, ttl_days=30) is False


# --- ChannelCache: get / put / fresh ---------------------------------------


def test_put_then_get_round_trips(tmp_path):
    cache = ChannelCache(tmp_path / "c.json")
    cache.put("@ForDev", make_entry(status=STATUS_OK, id=-100123))

    assert cache.get("fordev")["id"] == -100123
    # Same entry regardless of how the caller spells it.
    assert cache.get("@FORDEV")["id"] == -100123
    assert "fordev" in cache


def test_get_returns_none_for_unknown_channel(tmp_path):
    assert ChannelCache(tmp_path / "c.json").get("nope") is None


def test_fresh_returns_the_entry_when_usable(tmp_path):
    cache = ChannelCache(tmp_path / "c.json")
    cache.put("fordev", make_entry(status=STATUS_OK, id=-1, resolved_at=iso_days_ago(2)))

    assert cache.fresh("fordev", ttl_days=30) is not None


def test_fresh_returns_none_when_stale_so_the_caller_re_resolves(tmp_path):
    cache = ChannelCache(tmp_path / "c.json")
    cache.put("fordev", make_entry(status=STATUS_OK, id=-1, resolved_at=iso_days_ago(99)))

    assert cache.fresh("fordev", ttl_days=30) is None


def test_counts_by_status(tmp_path):
    cache = ChannelCache(tmp_path / "c.json")
    cache.put("a", make_entry(status=STATUS_OK, id=-1))
    cache.put("b", make_entry(status=STATUS_OK, id=-2))
    cache.put("c", make_entry(status=STATUS_NOT_JOINED))
    cache.put("d", make_entry(status=STATUS_NOT_FOUND))

    counts = cache.counts_by_status()

    assert counts == {STATUS_OK: 2, STATUS_NOT_JOINED: 1, STATUS_NOT_FOUND: 1}


# --- persistence: the whole point (surviving a restart) --------------------


def test_cache_survives_a_restart(tmp_path):
    path = tmp_path / "channel_cache.json"

    first_run = ChannelCache(path)
    first_run.put("fordev", make_entry(status=STATUS_OK, id=-100123, access_hash=999, title="Вакансии"))
    first_run.put("deadchan", make_entry(status=STATUS_NOT_FOUND))
    first_run.save()

    # A completely separate load, as a fresh process would do.
    second_run = load_cache(path)

    assert len(second_run) == 2
    assert second_run.get("fordev")["access_hash"] == 999
    assert second_run.get("fordev")["title"] == "Вакансии"
    assert second_run.get("deadchan")["status"] == STATUS_NOT_FOUND


def test_saved_file_is_readable_utf8_json(tmp_path):
    path = tmp_path / "c.json"
    cache = ChannelCache(path)
    cache.put("chan", make_entry(status=STATUS_OK, id=-1, title="Русский заголовок"))
    save_cache(cache)

    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["chan"]["title"] == "Русский заголовок"


def test_save_does_not_leave_a_temp_file_behind(tmp_path):
    path = tmp_path / "c.json"
    cache = ChannelCache(path)
    cache.put("chan", make_entry(status=STATUS_OK, id=-1))
    cache.save()

    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_loading_a_missing_file_gives_an_empty_cache(tmp_path):
    cache = load_cache(tmp_path / "does-not-exist.json")

    assert len(cache) == 0
    assert cache.get("anything") is None


def test_loading_a_corrupt_file_gives_an_empty_cache_instead_of_crashing(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{ this is not json", encoding="utf-8")

    cache = load_cache(path)

    # One wasted resolve pass beats refusing to start.
    assert len(cache) == 0


def test_loading_a_json_list_instead_of_an_object_does_not_crash(tmp_path):
    path = tmp_path / "c.json"
    path.write_text('["unexpected"]', encoding="utf-8")

    assert len(load_cache(path)) == 0


def test_non_dict_values_in_the_file_are_dropped(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"good": {"status": "ok"}, "bad": "oops"}), encoding="utf-8")

    cache = load_cache(path)

    assert cache.get("good") is not None
    assert cache.get("bad") is None


# --- --report --------------------------------------------------------------


def test_report_lists_worst_status_first(tmp_path):
    cache = ChannelCache(tmp_path / "c.json")
    cache.put("okchan", make_entry(status=STATUS_OK, id=-1, title="Живой"))
    cache.put("nojoin", make_entry(status=STATUS_NOT_JOINED))
    cache.put("gone", make_entry(status=STATUS_NOT_FOUND))

    statuses = [row[1] for row in build_report_rows(cache)]

    # The ones worth deleting from channels.yml float to the top.
    assert statuses == [STATUS_NOT_FOUND, STATUS_NOT_JOINED, STATUS_OK]


def test_report_renders_every_channel_and_a_summary(tmp_path):
    cache = ChannelCache(tmp_path / "c.json")
    cache.put("okchan", make_entry(status=STATUS_OK, id=-1, title="Живой"))
    cache.put("gone", make_entry(status=STATUS_NOT_FOUND))

    report = render_report(cache)

    assert "okchan" in report
    assert "gone" in report
    assert "Total 2 cached" in report
    assert "1 ok" in report
    assert "1 not_found" in report


def test_report_on_an_empty_cache_explains_what_to_do(tmp_path):
    report = render_report(ChannelCache(tmp_path / "c.json"))

    assert "empty" in report.lower()
    assert "workers.telegram_monitor.main" in report
