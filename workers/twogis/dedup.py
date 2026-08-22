"""In-memory, per-run dedup by 2GIS org id.

The core still dedups permanently (by domain/phone/fuzzy-name) across ALL
sources - this is just a cheap guard against this worker sending the same
org twice within one run, e.g. because two rubric queries both surface it,
or pagination overlaps at a page boundary.
"""


class DedupTracker:
    def __init__(self):
        self._seen: set[str] = set()

    def seen(self, twogis_id: str) -> bool:
        return twogis_id in self._seen

    def mark(self, twogis_id: str) -> None:
        self._seen.add(twogis_id)

    def __len__(self) -> int:
        return len(self._seen)
