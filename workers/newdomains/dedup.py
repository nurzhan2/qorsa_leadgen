"""In-memory, per-run dedup by domain name.

The core still dedups permanently (by domain/phone/fuzzy-name) across ALL
sources - this is just a cheap guard against sending the same domain
twice within one run.
"""


class DedupTracker:
    def __init__(self):
        self._seen: set[str] = set()

    def seen(self, domain: str) -> bool:
        return domain in self._seen

    def mark(self, domain: str) -> None:
        self._seen.add(domain)

    def __len__(self) -> int:
        return len(self._seen)
