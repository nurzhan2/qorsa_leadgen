"""Unit tests for dedup.py - pure in-memory logic, no network."""

from workers.newdomains.dedup import DedupTracker


def test_unseen_domain_is_not_seen():
    tracker = DedupTracker()

    assert tracker.seen("example.ru") is False


def test_marked_domain_is_seen():
    tracker = DedupTracker()

    tracker.mark("example.ru")

    assert tracker.seen("example.ru") is True


def test_different_domains_are_independent():
    tracker = DedupTracker()

    tracker.mark("a.ru")

    assert tracker.seen("a.ru") is True
    assert tracker.seen("b.ru") is False


def test_marking_twice_is_a_no_op():
    tracker = DedupTracker()

    tracker.mark("a.ru")
    tracker.mark("a.ru")

    assert len(tracker) == 1
