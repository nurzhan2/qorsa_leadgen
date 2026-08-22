# twogis

A "cold" lead source for `qorsa_leadgen`: pulls local businesses out of the
2GIS Catalog/Places API for a configured list of (city, category) pairs,
and forwards each one to the Java core over `POST /api/v1/companies/ingest`.
A business with **no website** is exactly the "нет сайта" signal the core's
own scoring already rewards (+40) - this worker just finds and reports such
businesses; it does no scoring itself.

Fully self-contained - its own `requirements.txt`, its own `.env`, its own
README. It does not import anything from, or get imported by, the Java core
or any other worker.

## Setup

### 1. Get a free 2GIS API key

1. Go to <https://dev.2gis.ru> and register/sign in.
2. Open the **API** section and request access to the **Catalog API**
   (sometimes listed as "Places API") - the free tier is enough to run this
   worker (with modest rate limits, which is why this worker paces its
   requests - see below).
3. Copy the issued key.

### 2. Configure

```bash
cd workers/twogis
cp .env.example .env
```

Fill in `.env`:

```
TWOGIS_API_KEY=your-key-here
CORE_URL=http://localhost:8081
TARGET_PER_DAY=300
RUN_ONCE=true
```

Install dependencies (a virtualenv is recommended):

```bash
python -m venv .venv
.venv/Scripts/activate   # or `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
```

### 3. Fill in `cities.yml` / `rubrics.yml`

Both are plain YAML lists, edited without touching code:

- `cities.yml` - city names, used as free-text search terms.
- `rubrics.yml` - business categories, each `{name, query}`; `query` is the
  search term combined with a city (e.g. `"кафе" + "Казань"` →
  `"кафе Казань"`). Ships with 18 categories that commonly lack a website
  (cafes, dental clinics, auto repair shops, beauty salons, ...).

#### Уточнение категорий через rubric_id

By default this worker searches with free text (`q=<rubric> <city>`), which
works out of the box without needing any 2GIS-internal IDs. For stricter
filtering you can instead filter by 2GIS's numeric `rubric_id` - to get the
authoritative list for your account/region, call:

```
https://catalog.api.2gis.com/2.0/catalog/rubric/list?key=YOUR_KEY
```

and wire the returned ids into `twogis_client.py`'s query params. Not done
by default here since those ids aren't something to guess at or hardcode
without verifying them against your own key's response first.

### 4. Run it

```bash
# from the repo root, with the Java core already running
python -m workers.twogis.main
```

With `RUN_ONCE=true` (the default) it does one pass across every
city × rubric combination (capped at `TARGET_PER_DAY` companies total) and
exits - good for a daily cron job / scheduled task. Set `RUN_ONCE=false` to
have it loop forever, sleeping `LOOP_INTERVAL_HOURS` (default 24) between
passes.

## What gets sent to the core

One `RawCompanyRequest` per company, batched 50-at-a-time to
`/api/v1/companies/ingest` (see the core's own README for the full JSON
contract):

| Field       | Value |
|-------------|-------|
| `name`      | The org's name as returned by 2GIS |
| `domain`    | Website contact, if 2GIS has one on file |
| `phone`     | First phone contact, if any |
| `address`   | 2GIS's address text for the org |
| `city`      | The city this search pass was for |
| `source`    | always `"TWOGIS"` |
| `sourceUrl` | A 2GIS site-search link for the name+city (2GIS's API response doesn't reliably include a direct firm-page permalink in this shape, so this is a working search deep-link rather than a guessed URL pattern) |
| `hasSite`   | `true` if a website contact was found, `false` otherwise - **this is the important one**: no website is a strong "needs a site" signal, and the core scores it +40 |
| `raw`       | `{ rubric, twogis_id }` |

## Duplicate handling

Two independent layers:

- **This worker**, in-memory, per run: won't send the same `twogis_id`
  twice, even if it turns up under more than one rubric query or a
  paginated overlap.
- **The core**, permanently: dedups by domain/phone/fuzzy-name across
  *all* sources, so re-running this worker daily is harmless even though
  the in-memory dedup set resets on every process restart.

## Rate limiting

2GIS's free tier is rate-limited, so this worker:

- caps concurrent requests with a semaphore (`MAX_CONCURRENT_REQUESTS`, default 2),
- pauses `REQUEST_DELAY_SECONDS` (default 0.34s) between requests,
- retries `429`/`5xx` responses with exponential backoff via `tenacity` (up
  to 5 attempts), and gives up on that one (city, rubric) combo without
  retrying on any other `4xx` (bad key, bad request - retrying won't help),
- caps pagination per combo at `MAX_PAGES_PER_COMBO` (default 3) so one
  popular category in one big city can't eat the whole day's
  `TARGET_PER_DAY` budget on its own.

If you see repeated `twogis.rate_limited` warnings, lower
`MAX_CONCURRENT_REQUESTS`/`PAGE_SIZE` or raise `REQUEST_DELAY_SECONDS` in `.env`.

## Tests

```bash
pip install -r requirements.txt   # includes pytest
pytest workers/twogis/tests
# or, from inside this directory:
cd workers/twogis && pytest
```

All tests run on hand-built fixture dicts/JSON - no network, no real 2GIS
API calls:

- `test_mapper.py` - item → `RawCompanyRequest` mapping, including the
  `hasSite` true/false logic.
- `test_twogis_client.py` - `parse_items()` against mocked 2GIS JSON
  response shapes (including malformed/empty ones).
- `test_dedup.py` - `DedupTracker` dedup-by-`twogis_id` behavior.

## Files

```
twogis/
├── __init__.py
├── config.py          # .env settings + cities.yml/rubrics.yml loaders (pydantic)
├── cities.yml          # editable city list
├── rubrics.yml          # editable category list
├── twogis_client.py      # 2GIS API HTTP client: pagination, retries, rate limiting
├── mapper.py              # 2GIS item -> RawCompanyRequest (pure)
├── dedup.py                # in-memory dedup-by-twogis_id tracker (pure)
├── core_client.py            # HTTP client to the Java core, with retries
├── runner.py                  # orchestrates city x rubric -> fetch -> dedup -> send
├── main.py                     # entry point
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py
    ├── test_mapper.py
    ├── test_twogis_client.py
    └── test_dedup.py
```
