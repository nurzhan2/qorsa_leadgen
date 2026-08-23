"""Unit tests for dedup.py - pure in-memory logic, no network."""

from workers.google_places.dedup import DedupTracker


def test_unseen_id_is_not_seen():
    tracker = DedupTracker()

    assert tracker.seen("ChIJ1") is False


def test_marked_id_is_seen():
    tracker = DedupTracker()

    tracker.mark("ChIJ1")

    assert tracker.seen("ChIJ1") is True


def test_different_ids_are_independent():
    tracker = DedupTracker()

    tracker.mark("ChIJ1")

    assert tracker.seen("ChIJ1") is True
    assert tracker.seen("ChIJ2") is False


def test_marking_twice_is_a_no_op():
    tracker = DedupTracker()

    tracker.mark("ChIJ1")
    tracker.mark("ChIJ1")

    assert len(tracker) == 1


def test_len_reflects_distinct_marked_ids():
    tracker = DedupTracker()

    tracker.mark("ChIJ1")
    tracker.mark("ChIJ2")
    tracker.mark("ChIJ3")

    assert len(tracker) == 3
