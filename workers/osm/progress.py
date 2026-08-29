"""Checkpoint of which (city, category) pairs have already been crawled.

Why this exists: the grid is 30 cities x 63 categories = 1890 Overpass
queries. That is far too much for one run - at the (deliberately slow) pacing
this worker uses it would take hours and lean on a free shared service the
whole time. So a run does a bounded slice (COMBOS_PER_RUN) and records what
it finished; the next run picks up exactly where this one stopped.

The other half of the value is crash-safety: an interrupted run must not
re-query the pairs it already paid for. The file is written after every
completed pair, so a kill -9 costs at most one query.

Pure JSON + filesystem, no network, no Overpass import - trivially testable.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def combo_key(city_name: str, category_name: str) -> str:
    """Stable identity for one (city, category) pair. Cities and categories
    are identified by NAME rather than by index, so inserting a city at the
    top of cities.yml doesn't silently shift every checkpoint entry onto a
    different pair."""
    return f"{city_name}||{category_name}"


class ProgressTracker:
    """Remembers completed pairs across runs.

    `cycle_started_at` marks when the current sweep of the grid began; it's
    reset along with the done-set whenever a full cycle completes or
    RESET_PROGRESS is set, which is what makes this a rolling crawl rather
    than a one-shot.
    """

    def __init__(self, path: Path, done: set[str] | None = None, cycle_started_at: str | None = None):
        self.path = Path(path)
        self._done: set[str] = set(done or ())
        self.cycle_started_at = cycle_started_at or _utc_now()

    # --- reads ---------------------------------------------------------

    def is_done(self, city_name: str, category_name: str) -> bool:
        return combo_key(city_name, category_name) in self._done

    def pending(self, combos: list[tuple]) -> list[tuple]:
        """The subset of (city, category) pairs still to do, in order."""
        return [c for c in combos if not self.is_done(c[0].name, c[1].name)]

    @property
    def done_count(self) -> int:
        return len(self._done)

    def __len__(self) -> int:
        return len(self._done)

    # --- writes --------------------------------------------------------

    def mark_done(self, city_name: str, category_name: str) -> None:
        self._done.add(combo_key(city_name, category_name))

    def reset(self) -> None:
        """Begin a fresh sweep of the grid."""
        self._done.clear()
        self.cycle_started_at = _utc_now()

    def save(self) -> None:
        payload = {
            "cycle_started_at": self.cycle_started_at,
            "updated_at": _utc_now(),
            "done": sorted(self._done),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.error("osm.progress_save_failed", path=str(self.path), error=str(exc))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_progress(path: Path) -> ProgressTracker:
    """Never raises. A missing file is an empty checkpoint; a corrupt one is
    logged and treated as empty - re-crawling the grid is annoying but
    strictly better than refusing to start."""
    path = Path(path)
    if not path.exists():
        return ProgressTracker(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("osm.progress_unreadable", path=str(path), error=str(exc))
        return ProgressTracker(path)
    if not isinstance(data, dict):
        log.warning("osm.progress_unexpected_shape", path=str(path))
        return ProgressTracker(path)
    done = data.get("done")
    if not isinstance(done, list):
        done = []
    return ProgressTracker(
        path,
        done={item for item in done if isinstance(item, str)},
        cycle_started_at=data.get("cycle_started_at"),
    )
