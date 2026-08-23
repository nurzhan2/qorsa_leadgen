"""In-memory, per-run dedup by OSM element id.

The core still dedups permanently (by domain/phone/fuzzy-name) across ALL
sources - this is just a cheap guard against this worker sending the same
element twice within one run.

Keyed on "<type>/<id>" rather than the bare numeric id: OSM ids are only
unique WITHIN a type - node/123 and way/123 are different elements that
can both legitimately exist.
"""


class DedupTracker:
    def __init__(self):
        self._seen: set[str] = set()

    @staticmethod
    def key(osm_type: str, osm_id) -> str:
        return f"{osm_type}/{osm_id}"

    def seen(self, osm_type: str, osm_id) -> bool:
        return self.key(osm_type, osm_id) in self._seen

    def mark(self, osm_type: str, osm_id) -> None:
        self._seen.add(self.key(osm_type, osm_id))

    def __len__(self) -> int:
        return len(self._seen)
