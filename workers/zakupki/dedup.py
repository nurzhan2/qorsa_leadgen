"""In-memory, per-run dedup by purchase registration number.

The core still dedups permanently (by domain/phone/fuzzy-name) across ALL
sources - this is just a cheap guard against sending the same purchase
notice twice within one run, e.g. because it matches more than one keyword.
"""


class DedupTracker:
    def __init__(self):
        self._seen: set[str] = set()

    def seen(self, reg_number: str) -> bool:
        return reg_number in self._seen

    def mark(self, reg_number: str) -> None:
        self._seen.add(reg_number)

    def __len__(self) -> int:
        return len(self._seen)
