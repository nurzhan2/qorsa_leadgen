# google_places

A "cold" lead source for `qorsa_leadgen`: pulls local businesses out of
Google Places API (New) - Text Search - for a configured list of
(city, category) pairs, and forwards each one to the Java core over
`POST /api/v1/companies/ingest`. A business with no `websiteUri` is a
reliable "needs a site" signal - Text Search returns it whenever Google
has one on file, so its absence isn't an API/plan artifact.

Fully self-contained - its own `requirements.txt`, its own `.env`, its own
README. It does not import anything from, or get imported by, the Java
core or any other worker.

> **Биллинг обязателен.** В отличие от `workers/osm/` (бесплатный) и
> `workers/twogis/` (бесплатный тариф с ограничениями), у Google Places
> API (New) **нет полезного бесплатного тарифа** - даже пробный период
> требует привязанной карты/включённого биллинга в Google Cloud. Без
> активного биллинга Google вернёт ошибку (403/`PERMISSION_DENIED`,
> "This API method requires billing to be enabled") на **каждый** запрос.
> Это ожидаемо, не баг воркера - см. "Настройка" ниже.

## Setup

### 1. Включить Places API (New) и биллинг в Google Cloud

1. Зайдите в [Google Cloud Console](https://console.cloud.google.com) и
   создайте/выберите проект.
2. **Включите биллинг**: *Billing* → привяжите платёжный аккаунт (карту).
   Без этого шага ключ технически создастся, но каждый запрос будет падать
   с ошибкой биллинга - это не опечатка в README, так работает сам Google.
3. **Включите API**: *APIs & Services → Library* → найдите "Places API
   (New)" → **Enable**. (Не путайте со старым "Places API" - это разные
   продукты с разными эндпоинтами.)
4. **Создайте ключ**: *APIs & Services → Credentials* → **Create
   Credentials → API key**. Рекомендуется сразу ограничить ключ
   (*Restrict key*) списком API → только "Places API (New)".
5. Скопируйте ключ.

### 2. Configure

```bash
cd workers/google_places
cp .env.example .env
```

Fill in `.env`:

```
GOOGLE_PLACES_API_KEY=your-key-here
CORE_URL=http://localhost:8081
TARGET_PER_DAY=300
RUN_ONCE=true
```

**Если биллинг ещё не готов** - просто оставьте `GOOGLE_PLACES_API_KEY`
пустым. Воркер это обнаружит при старте, залогирует понятное сообщение
(`GOOGLE_PLACES_API_KEY not set, exiting`) и завершится с кодом 0 - без
трейсбека, без падения. Остальная часть проекта (ядро, другие воркеры)
продолжает работать как обычно.

Install dependencies (a virtualenv is recommended):

```bash
python -m venv .venv
.venv/Scripts/activate   # or `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
```

### 3. Fill in `cities.yml` / `categories.yml`

Both are plain YAML lists, edited without touching code:

- `categories.yml` - business categories, each `{name, query}`; `query`
  combined with a city name into a free-text search (e.g. `"кафе" +
  "Казань"` → `"кафе Казань"`). Ships with 14 categories.
- `cities.yml` - just city names (`{name}`) - Places' own Text Search
  handles the geocoding, no bbox/region_id needed here.

### 4. Run it

```bash
# from the repo root, with the Java core already running, billing enabled
python -m workers.google_places.main
```

With `RUN_ONCE=true` (the default) it does one pass across every
city × category combination (capped at `TARGET_PER_DAY` companies total)
and exits - good for a daily cron job / scheduled task. Set
`RUN_ONCE=false` to have it loop forever, sleeping `LOOP_INTERVAL_HOURS`
(default 24) between passes.

## How search works

For each (city, category) pair, one Places API (New) Text Search call:

```
POST https://places.googleapis.com/v1/places:searchText
X-Goog-Api-Key: <key>
X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,
                  places.nationalPhoneNumber,places.websiteUri,places.types,nextPageToken
Content-Type: application/json

{"textQuery": "кафе Казань"}
```

The `FieldMask` already requests phone/website directly from Text Search,
so a separate **Place Details** call per result isn't made here - that
would double the request count (and cost) for fields Text Search already
returns. Pagination follows `nextPageToken`, capped at
`MAX_PAGES_PER_QUERY` (default 3) per (city, category) pair; Google's own
docs note a fresh token needs a short pause before it's valid, which this
worker respects (`PAGE_TOKEN_DELAY_SECONDS`).

### О стоимости

`nationalPhoneNumber` и `websiteUri` относятся к тарифицируемой категории
полей "Contact Data" в SKU-based pricing Places API (New) - то есть дороже
базового набора (`id`/`displayName`/`formattedAddress`). Это осознанный
выбор: без телефона/сайта воркер не может выставить `hasSite` достоверно,
а именно это и есть его смысл. Актуальные цены - на
[странице тарифов Places API](https://mapsplatform.google.com/pricing/) -
уточните их перед боевым запуском на большом `TARGET_PER_DAY`.

## What gets sent to the core

One `RawCompanyRequest` per place, batched 50-at-a-time to
`/api/v1/companies/ingest` (see the core's own README for the full JSON
contract). Places with no `displayName` are skipped entirely.

| Field       | Value |
|-------------|-------|
| `name`      | `displayName.text` |
| `domain`    | `websiteUri`, normalized to a bare host |
| `phone`     | `nationalPhoneNumber` |
| `address`   | `formattedAddress` |
| `city`      | The name from `cities.yml` for this search pass |
| `source`    | always `"GOOGLE_MAPS"` |
| `sourceUrl` | `https://www.google.com/maps/place/?q=place_id:<id>` - Google's documented URL scheme for opening a place by id |
| `hasSite`   | `true` if `websiteUri` was returned, `false` otherwise - reliable here, Text Search returns it whenever Google has one on file |
| `raw`       | `{ place_id, types, category_query }` |

## Duplicate handling

Two independent layers:

- **This worker**, in-memory, per run: won't send the same `place_id`
  twice, even if it turns up under more than one category query.
- **The core**, permanently: dedups by domain/phone/fuzzy-name across
  *all* sources, so re-running this worker daily is harmless even though
  the in-memory dedup set resets on every process restart.

## Rate limiting / quota

- pauses `REQUEST_DELAY_SECONDS` (default 1.0s) between requests,
- retries `429`/`5xx` responses with exponential backoff via `tenacity`
  (up to 5 attempts), and gives up on that one query without retrying on
  any other `4xx` (bad key, billing disabled, invalid request - retrying
  won't help),
- `TARGET_PER_DAY` caps total companies ingested per run - since this API
  bills per request, treat it as a cost guard as much as a rate limit.

## Tests

```bash
pip install -r requirements.txt   # includes pytest + pytest-asyncio
pytest workers/google_places/tests
# or, from inside this directory:
cd workers/google_places && pytest
```

All tests run on hand-built fixture dicts/JSON matching the real Places
API (New) response shape - no network, no real Google API calls:

- `test_mapper.py` - place → `RawCompanyRequest` mapping, including the
  `hasSite` true/false logic.
- `test_places_client.py` - `parse_places()` (pure) plus, via a mocked
  httpx transport, that requests carry the right headers/FieldMask,
  `nextPageToken` pagination, the `max_pages_per_query` cap, and
  429/billing-error retry behavior.
- `test_dedup.py` - `DedupTracker` dedup-by-`place_id` behavior.
- `test_main.py` - locks down the exact condition the "no API key ->
  exit cleanly" behavior in `main.py` relies on, without ever calling
  `main._run()` end-to-end (which would risk making a real, billed API
  call if a real key is ever configured in this directory's `.env`).

## Files

```
google_places/
├── __init__.py
├── config.py               # .env settings + cities.yml/categories.yml loaders (pydantic)
├── cities.yml                # editable city list
├── categories.yml             # editable category list
├── places_client.py             # Places API (New) HTTP client: pagination, retries
├── mapper.py                      # place -> RawCompanyRequest (pure)
├── dedup.py                        # in-memory dedup-by-place_id tracker (pure)
├── core_client.py                    # HTTP client to the Java core, with retries
├── runner.py                          # orchestrates city x category -> fetch -> dedup -> send
├── main.py                             # entry point (friendly exit if no API key)
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py
    ├── test_mapper.py
    ├── test_places_client.py
    ├── test_dedup.py
    └── test_main.py
```
