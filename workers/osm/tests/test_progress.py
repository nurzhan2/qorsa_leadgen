"""Unit tests for progress.py - pure JSON/filesystem, no network. Every test
writes into pytest's tmp_path, never the real osm_progress.json."""

import json

from workers.osm.config import Category, City
from workers.osm.progress import ProgressTracker, combo_key, load_progress


def city(name: str) -> City:
    return City(name=name, bbox=[55.0, 37.0, 56.0, 38.0])


def category(name: str) -> Category:
    return Category(name=name, key="amenity", value="cafe")


def grid(city_names, category_names):
    return [(city(c), category(k)) for c in city_names for k in category_names]


# --- keys -----------------------------------------------------------------


def test_combo_key_is_stable_and_distinguishes_pairs():
    assert combo_key("Москва", "Кафе") == combo_key("Москва", "Кафе")
    assert combo_key("Москва", "Кафе") != combo_key("Москва", "Аптеки")
    assert combo_key("Москва", "Кафе") != combo_key("Казань", "Кафе")


def test_progress_is_keyed_by_name_not_position(tmp_path):
    """Inserting a city at the top of cities.yml must not shift every
    checkpoint entry onto a different pair."""
    tracker = ProgressTracker(tmp_path / "p.json")
    tracker.mark_done("Казань", "Кафе")

    # A new city appears first in the grid on the next run.
    combos = grid(["Москва", "Казань"], ["Кафе"])
    pending = tracker.pending(combos)

    assert [(c.name, k.name) for c, k in pending] == [("Москва", "Кафе")]


# --- pending / mark_done --------------------------------------------------


def test_everything_is_pending_on_a_fresh_checkpoint(tmp_path):
    tracker = ProgressTracker(tmp_path / "p.json")
    combos = grid(["Москва", "Казань"], ["Кафе", "Аптеки"])

    assert len(tracker.pending(combos)) == 4
    assert tracker.done_count == 0


def test_marked_pairs_drop_out_of_pending(tmp_path):
    tracker = ProgressTracker(tmp_path / "p.json")
    combos = grid(["Москва", "Казань"], ["Кафе", "Аптеки"])

    tracker.mark_done("Москва", "Кафе")
    tracker.mark_done("Казань", "Аптеки")

    pending = [(c.name, k.name) for c, k in tracker.pending(combos)]
    assert pending == [("Москва", "Аптеки"), ("Казань", "Кафе")]


def test_pending_preserves_grid_order(tmp_path):
    tracker = ProgressTracker(tmp_path / "p.json")
    combos = grid(["Москва", "Казань"], ["Кафе", "Аптеки"])

    pending = [(c.name, k.name) for c, k in tracker.pending(combos)]

    # City-major: one city's categories are crawled together.
    assert pending == [
        ("Москва", "Кафе"), ("Москва", "Аптеки"),
        ("Казань", "Кафе"), ("Казань", "Аптеки"),
    ]


# --- persistence: the point of the whole module ---------------------------


def test_checkpoint_survives_a_restart(tmp_path):
    path = tmp_path / "osm_progress.json"

    first_run = ProgressTracker(path)
    first_run.mark_done("Москва", "Кафе")
    first_run.mark_done("Москва", "Аптеки")
    first_run.save()

    # A completely separate load, as a new process would do.
    second_run = load_progress(path)

    assert second_run.done_count == 2
    assert second_run.is_done("Москва", "Кафе")
    assert second_run.is_done("Москва", "Аптеки")
    assert not second_run.is_done("Казань", "Кафе")


def test_a_second_run_resumes_where_the_first_stopped(tmp_path):
    """The behaviour the checkpoint exists for: no pair is queried twice, and
    none is skipped."""
    path = tmp_path / "osm_progress.json"
    combos = grid(["Москва", "Казань", "Уфа"], ["Кафе", "Аптеки"])  # 6 pairs

    run1 = load_progress(path)
    slice1 = run1.pending(combos)[:4]
    for c, k in slice1:
        run1.mark_done(c.name, k.name)
    run1.save()

    run2 = load_progress(path)
    slice2 = run2.pending(combos)[:4]

    assert len(slice2) == 2  # only the leftovers
    covered = [(c.name, k.name) for c, k in slice1] + [(c.name, k.name) for c, k in slice2]
    assert len(covered) == 6
    assert len(set(covered)) == 6  # nothing queried twice, nothing missed


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "p.json"
    tracker = ProgressTracker(path)
    tracker.mark_done("Москва", "Кафе")
    tracker.save()

    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_saved_file_is_readable_utf8_json(tmp_path):
    path = tmp_path / "p.json"
    tracker = ProgressTracker(path)
    tracker.mark_done("Нижний Новгород", "Кондитерские")
    tracker.save()

    data = json.loads(path.read_text(encoding="utf-8"))

    assert "Нижний Новгород||Кондитерские" in data["done"]
    assert data["cycle_started_at"]


# --- reset ----------------------------------------------------------------


def test_reset_clears_progress_and_starts_a_new_cycle(tmp_path):
    tracker = ProgressTracker(tmp_path / "p.json")
    tracker.mark_done("Москва", "Кафе")
    started = tracker.cycle_started_at

    tracker.reset()

    assert tracker.done_count == 0
    assert not tracker.is_done("Москва", "Кафе")
    assert tracker.cycle_started_at >= started


# --- robustness -----------------------------------------------------------


def test_missing_file_gives_an_empty_checkpoint(tmp_path):
    tracker = load_progress(tmp_path / "nope.json")

    assert tracker.done_count == 0


def test_corrupt_file_gives_an_empty_checkpoint_instead_of_crashing(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{ not json at all", encoding="utf-8")

    # Re-crawling is annoying; refusing to start is worse.
    assert load_progress(path).done_count == 0


def test_unexpected_json_shape_is_tolerated(tmp_path):
    path = tmp_path / "p.json"
    path.write_text('["a list, not an object"]', encoding="utf-8")

    assert load_progress(path).done_count == 0


def test_non_string_entries_in_done_are_dropped(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"done": ["Москва||Кафе", 42, None]}), encoding="utf-8")

    tracker = load_progress(path)

    assert tracker.done_count == 1
    assert tracker.is_done("Москва", "Кафе")
