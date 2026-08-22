"""Unit tests for dedup.py - pure in-memory logic, no network."""

from workers.twogis.dedup import DedupTracker


def test_unseen_id_is_not_seen():
    tracker = DedupTracker()

    assert tracker.seen("70000001029160997") is False


def test_marked_id_is_seen():
    tracker = DedupTracker()

    tracker.mark("70000001029160997")

    assert tracker.seen("70000001029160997") is True


def test_different_ids_are_independent():
    tracker = DedupTracker()

    tracker.mark("1")

    assert tracker.seen("1") is True
    assert tracker.seen("2") is False


def test_marking_twice_is_a_no_op():
    tracker = DedupTracker()

    tracker.mark("1")
    tracker.mark("1")

    assert len(tracker) == 1


def test_len_reflects_distinct_marked_ids():
    tracker = DedupTracker()

    tracker.mark("1")
    tracker.mark("2")
    tracker.mark("3")

    assert len(tracker) == 3
