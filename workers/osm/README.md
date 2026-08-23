# osm

A free, no-API-key "cold" lead source for `qorsa_leadgen`: pulls local
businesses out of OpenStreetMap (via the public Overpass API) for a
configured list of (city, category) pairs, and forwards each one to the
Java core over `POST /api/v1/companies/ingest`. A business with no
`website`/`contact:website` tag is a genuinely reliable "needs a site"
signal here - OSM contributors tag what they observe, so `hasSite=false`
means the tag was actually absent, not an artifact of an API plan
withholding data (unlike some other sources - see `workers/twogis/README.md`
for a contrasting example where that *is* a real caveat).

Fully self-contained - its own `requirements.txt`, its own `.env`, its own
README. It does not import anything from, or get imported by, the Java
core or any other worker.

## Setup

### 1. No API key needed

Overpass is free and public - there's no signup, no key. It does expect a
`User-Agent` that identifies your tool - see the important caveat about
*what that string can contain* below before you customize it.

### 2. Configure

```bash
cd workers/osm
cp .env.example .env
```

The defaults in `.env.example` already work as-is:

```
CORE_URL=http://localhost:8081
TARGET_PER_DAY=300
RUN_ONCE=true
OSM_USER_AGENT=qorsa-leadgen-osm-worker/1.0
```

Install dependencies (a virtualenv is recommended):

```bash
python -m venv .venv
.venv/Scripts/activate   # or `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
```

### 3. Fill in `cities.yml` / `categories.yml`

- `categories.yml` - OSM tags, each `{name, key, value}`. `value: null`
  matches any value for that key (e.g. a generic `shop=*` category). Ships
  with 14 categories covering cafes, dentists, auto repair shops, beauty
  salons, clinics, and more - businesses that commonly lack a website.
- `cities.yml` - cities as bounding boxes, `{name, bbox: [south, west, north, east]}`.
  Ships with 8 (Moscow, SPb, Novosibirsk, Ekaterinburg, Kazan, Nizhny
  Novgorod, Krasnodar, Rostov-on-Don) as approximate rectangles covering
  each city's built-up area - not exact administrative borders. Refine
  with [bboxfinder.com](http://bboxfinder.com) or
  [nominatim.openstreetmap.org](https://nominatim.openstreetmap.org) if you
  need tighter bounds, or add more cities the same way.

### User-Agent: важное ограничение (проверено вживую)

Overpass официально просит присылать `User-Agent`, который идентифицирует
инструмент **и способ связаться с автором** - обычно это советуют делать
как `AppName/1.0 (contact: email@example.com)`. На практике публичный
инстанс `overpass-api.de` **блокирует именно это** - живыми, многократно
повторёнными запросами подтверждено, что сервер отвечает `406 Not
Acceptable`, если `User-Agent` содержит:

- символ `@` (email в любом виде, даже без слова "contact"),
- схему URL `://` (`https://...`, `http://...`),
- "обфусцированный" email по схеме `слово at слово dot слово`
  (`yourname at example dot com`) - похоже на отдельное анти-спам
  правило, ловящее именно этот классический паттерн обхода парсеров почты.

Одна и та же строка проверялась по 2-4 раза подряд с интервалом ~8-10с,
чтобы отличить это от обычной перегрузки/флаки паблик-инстанса
(429/504 - те действительно нестабильны) - блокировка по этим паттернам
воспроизводилась стабильно, а обычные строки без них (`qorsa-leadgen-osm-worker/1.0`,
`... (contact via project readme)`, `... (test)`) стабильно проходили.

**Вывод:** не пытайтесь класть реальный email/URL в `OSM_USER_AGENT` для
этого паблик-инстанса - именно это его и блокирует. Дефолтное значение
(`qorsa-leadgen-osm-worker/1.0`) уже проверено и работает; если хотите
что-то добавить - только обычные слова в скобках, без `@`, `://` и
паттерна "at ... dot ...". Если вам принципиально важно указать реальный
контакт в UA (например, для своего собственного/корпоративного Overpass-
инстанса без такого фильтра) - это не проблема кода, а особенность
конкретного публичного сервера.

### 4. Run it

```bash
# from the repo root, with the Java core already running
python -m workers.osm.main
```

With `RUN_ONCE=true` (the default) it does one pass across every
city × category combination (capped at `TARGET_PER_DAY` companies total)
and exits - good for a daily cron job / scheduled task. Set
`RUN_ONCE=false` to have it loop forever, sleeping `LOOP_INTERVAL_HOURS`
(default 24) between passes.

## How search works (and its limits)

Overpass QL, not free text: for each (city, category) pair this worker
runs one query like:

```
[out:json][timeout:65];
(
  node["amenity"="cafe"](55.55,37.35,55.95,37.85);
  way["amenity"="cafe"](55.55,37.35,55.95,37.85);
  relation["amenity"="cafe"](55.55,37.35,55.95,37.85);
);
out tags 50;
```

Overpass has **no REST-style page/pageSize pagination** - a query just
returns everything matching. To keep one combo from pulling an unbounded
amount of data, the `out tags <N>;` limit (from `PAGE_SIZE`, default 50)
caps how many elements come back per combo, and this worker moves on to
the next combo rather than trying to paginate deeper into one. If a
category is very common in a big city, you're seeing a sample of it, not
the full set - raise `PAGE_SIZE` if you need more per combo (the public
Overpass instance will still enforce its own limits).

## What gets sent to the core

One `RawCompanyRequest` per element, batched 50-at-a-time to
`/api/v1/companies/ingest` (see the core's own README for the full JSON
contract). Elements with no `name` tag are skipped entirely - nothing
meaningful to send.

| Field       | Value |
|-------------|-------|
| `name`      | `tags.name` |
| `domain`    | `tags['contact:website']` or `tags.website`, normalized to a bare host |
| `phone`     | `tags['contact:phone']` or `tags.phone` |
| `address`   | `addr:street` + `addr:housenumber` joined ("ул. Абая, 10"), or whichever one is present |
| `city`      | The name from `cities.yml` for this search pass |
| `source`    | always `"OSM"` |
| `sourceUrl` | `https://www.openstreetmap.org/<type>/<id>` - OSM gives every element a real, permanent permalink, no guessing needed |
| `hasSite`   | `true` if a website tag was found, `false` otherwise - a genuinely reliable signal here (see the note at the top) |
| `raw`       | `{ osm_id, osm_type, category_tag }` |

## Duplicate handling

Two independent layers:

- **This worker**, in-memory, per run: won't send the same element twice.
  Deduped on `"<type>/<id>"`, not the bare numeric id - OSM ids are only
  unique *within* a type, so `node/1` and `way/1` are different elements
  that can both legitimately exist.
- **The core**, permanently: dedups by domain/phone/fuzzy-name across
  *all* sources, so re-running this worker daily is harmless even though
  the in-memory dedup set resets on every process restart.

## Rate limiting

The public Overpass instance rate-limits hard, so this worker:

- pauses `REQUEST_DELAY_SECONDS` (default 3.0s) after every request,
  success or failure - not optional politeness, the public instance
  punishes bursty clients,
- retries `429` (Too Many Requests) and `504` (Gateway Timeout - Overpass
  is slow and times out its own queries under load) with a **long**
  exponential backoff via `tenacity` (starts at 5s, caps at 120s, up to 5
  attempts),
- gives up on that one (city, category) combo without retrying on any
  other `4xx` (malformed query - retrying won't help),
- uses a long per-request timeout (`REQUEST_TIMEOUT_SECONDS`, default
  75s) since Overpass itself can be slow to answer.

If you see repeated `osm.rate_limited_or_timeout` warnings, raise
`REQUEST_DELAY_SECONDS` and/or lower `PAGE_SIZE` in `.env`. If you need to
run this a lot, consider running your own Overpass instance or using a
different public mirror.

## Tests

```bash
pip install -r requirements.txt   # includes pytest + pytest-asyncio
pytest workers/osm/tests
# or, from inside this directory:
cd workers/osm && pytest
```

All tests run on hand-built fixture dicts/JSON matching the real Overpass
"out tags" element shape (`{type, id, tags}`) - no network, no real
Overpass API calls:

- `test_mapper.py` - element → `RawCompanyRequest` mapping (name required,
  address joining, contact tag preference), and the `hasSite` logic.
- `test_overpass_client.py` - `build_query()`/`parse_elements()` (pure),
  plus (via a mocked httpx transport) that the User-Agent header is sent,
  and 429/504 retry vs. no-retry-on-4xx behavior.
- `test_dedup.py` - `DedupTracker` dedup-by-`type/id` behavior.

## Files

```
osm/
├── __init__.py
├── config.py              # .env settings + cities.yml/categories.yml loaders (pydantic)
├── cities.yml               # editable city bounding boxes
├── categories.yml            # editable OSM tag list
├── overpass_client.py          # Overpass API HTTP client: query building, retries, rate limiting
├── mapper.py                    # Overpass element -> RawCompanyRequest (pure)
├── dedup.py                      # in-memory dedup-by-type/id tracker (pure)
├── core_client.py                  # HTTP client to the Java core, with retries
├── runner.py                        # orchestrates city x category -> fetch -> dedup -> send
├── main.py                           # entry point
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py
    ├── test_mapper.py
    ├── test_overpass_client.py
    └── test_dedup.py
```
