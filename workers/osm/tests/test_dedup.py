"""Unit tests for dedup.py - pure in-memory logic, no network."""

from workers.osm.dedup import DedupTracker


def test_unseen_id_is_not_seen():
    tracker = DedupTracker()

    assert tracker.seen("node", 1) is False


def test_marked_id_is_seen():
    tracker = DedupTracker()

    tracker.mark("node", 1)

    assert tracker.seen("node", 1) is True


def test_node_and_way_with_same_numeric_id_are_independent():
    """OSM ids are only unique WITHIN a type - node/1 and way/1 are
    different elements that can both legitimately exist."""
    tracker = DedupTracker()

    tracker.mark("node", 1)

    assert tracker.seen("node", 1) is True
    assert tracker.seen("way", 1) is False


def test_marking_twice_is_a_no_op():
    tracker = DedupTracker()

    tracker.mark("node", 1)
    tracker.mark("node", 1)

    assert len(tracker) == 1


def test_len_reflects_distinct_marked_keys():
    tracker = DedupTracker()

    tracker.mark("node", 1)
    tracker.mark("way", 1)
    tracker.mark("relation", 2)

    assert len(tracker) == 3
