"""In-memory, per-run dedup by Google place_id.

The core still dedups permanently (by domain/phone/fuzzy-name) across ALL
sources - this is just a cheap guard against this worker sending the same
place twice within one run, e.g. because two category queries both surface it.
"""


class DedupTracker:
    def __init__(self):
        self._seen: set[str] = set()

    def seen(self, place_id: str) -> bool:
        return place_id in self._seen

    def mark(self, place_id: str) -> None:
        self._seen.add(place_id)

    def __len__(self) -> int:
        return len(self._seen)
