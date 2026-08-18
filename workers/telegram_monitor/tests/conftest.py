"""Makes `workers.telegram_monitor` importable when pytest is run from
anywhere (e.g. `pytest workers/telegram_monitor/tests` from the repo
root, or `pytest` from inside this worker's own directory)."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
