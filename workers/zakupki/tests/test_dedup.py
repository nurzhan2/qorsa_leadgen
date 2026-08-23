"""Unit tests for dedup.py - pure in-memory logic, no network."""

from workers.zakupki.dedup import DedupTracker


def test_unseen_reg_number_is_not_seen():
    tracker = DedupTracker()

    assert tracker.seen("123") is False


def test_marked_reg_number_is_seen():
    tracker = DedupTracker()

    tracker.mark("123")

    assert tracker.seen("123") is True


def test_different_reg_numbers_are_independent():
    tracker = DedupTracker()

    tracker.mark("123")

    assert tracker.seen("123") is True
    assert tracker.seen("456") is False


def test_marking_twice_is_a_no_op():
    tracker = DedupTracker()

    tracker.mark("123")
    tracker.mark("123")

    assert len(tracker) == 1
