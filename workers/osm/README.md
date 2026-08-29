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

**Охват:** 30 крупнейших городов РФ × 36 категорий малого бизнеса = 1080 пар,
обходимых постепенно, по 50 за запуск - см. "Режим постепенного обхода".

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
REQUEST_DELAY_SECONDS=5.0
COMBOS_PER_RUN=50
```

| variable | default | what it does |
|---|---|---|
| `REQUEST_DELAY_SECONDS` | `5.0` | pause after **every** Overpass request. Not optional politeness |
| `COMBOS_PER_RUN` | `50` | how many (city × category) pairs one run covers - see "Режим постепенного обхода" |
| `RESET_PROGRESS` | `false` | discard the checkpoint and crawl the grid from the start |
| `OVERPASS_ENDPOINTS` | 3 public mirrors | tried in order; a persistently busy one is rotated away from |
| `CHAIN_STOPLIST` | built-in list | chain names to skip (they already have websites) |

Install dependencies (a virtualenv is recommended):

```bash
python -m venv .venv
.venv/Scripts/activate   # or `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
```

### 3. Охват: `cities.yml` / `categories.yml`

Ships with **30 cities × 36 categories = 1080 (city, category) pairs**.

**`cities.yml`** - Russia's 30 largest cities as bounding boxes,
`{name, bbox: [south, west, north, east]}`: every million-plus city plus
major regional centres, from Москва down to Кемерово.

The boxes are **generated from a centre + radius, not hand-typed** - hand
arithmetic over 30 pairs of decimals is exactly where a lat/lon swap creeps
in. The longitude span is scaled by `1/cos(latitude)` so a box stays roughly
square on the ground instead of being stretched east-west up north. Sizes
vary deliberately: ~50×50 km for Moscow, ~20×20 km for a 500k regional
centre.

`config.py` re-validates every box at load time - latitude within ±90,
longitude within ±180, `south < north`, `west < east`. That range check is
what catches the classic `[lon, lat, lon, lat]` mistake: for Владивосток
(43.1 N, **131.9 E**) a swap puts 131.9 in a latitude slot, which is
impossible. Without it the ordering check alone would accept the box and
then silently return nothing for that city forever.

**Verified live** against `overpass-api.de`: the Moscow box returns 6233
cafes and the Vladivostok box 349 - the second being the one that proves the
eastern cities aren't swapped.

**`categories.yml`** - OSM tags, each `{name, key, value}`. 36 small-business
categories that plausibly lack a website: food, medical, beauty, auto,
education, business services, home/repair, retail.

**Every tag was verified, not guessed** - twice:

1. **taginfo** - is the tag real, documented on the OSM wiki, and used
   worldwide?
2. **A live Overpass count inside the Moscow bbox** - is it used *in Russia*?
   A tag can be popular worldwide and effectively absent here.

The `# ~N в Москве` comments in the file are that second number, so a future
editor can see which categories earn their query. If you add one, check it
the same way first: an unused tag still costs one Overpass request per city
(30 of them) and returns nothing.

#### Чего в списке нет и почему (честно)

Four commonly-requested categories have **no usable OSM tag in Russia**.
Verified live - the count inside the Moscow bbox is literally zero:

| категория | ближайший тег | в Москве |
|---|---|---|
| клининг | `craft=cleaning` | **0** |
| свадебные агентства | `office=wedding_planner` | **0** |
| event-агентства | `office=event_management` | **0** |
| ремонт квартир | `craft=builder` | **0** (`craft=carpenter`: 8) |

They're deliberately **not** in `categories.yml`. Adding them would burn 30
Overpass requests each per full cycle and return nothing - the opposite of
what the rate-limit work below is for. If you specifically need these
business types, OSM is the wrong source; 2GIS or Яндекс.Справочник have them.

Two more tags that *sound* right but aren't real: `shop=windows` (254 uses
worldwide, no wiki page - use `craft=window_construction`) and
`shop=moving_company` (1 use - use `office=logistics`).

The previous generic `shop=*` catch-all was also removed: at 30 cities it was
the single heaviest query in the set and fully redundant with the 20+
specific `shop=*` categories. There's a commented-out block at the bottom of
`categories.yml` if you want it back.

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

With `RUN_ONCE=true` (the default) it does **one bounded slice** of the grid
and exits - good for a scheduled task. Set `RUN_ONCE=false` to loop forever,
sleeping `LOOP_INTERVAL_HOURS` (default 24) between passes.

A run stops at whichever limit comes first:

- `COMBOS_PER_RUN` (default 50) - how many Overpass queries to make;
- `TARGET_PER_DAY` (default 300) - how many companies to ingest.

## Режим постепенного обхода

30 cities × 36 categories = **1080 Overpass queries** for one full sweep.
Done naively in a single run that is a guaranteed IP ban on the public
instance - and it would take over an hour of continuous querying even at the
pacing this worker uses.

So a run takes a **slice**: `COMBOS_PER_RUN` pairs, then stops and records
what it finished. The next run continues from exactly where the last one
stopped. At the default 50, **22 runs cover the whole grid**, after which it
starts a fresh cycle automatically (so a scheduled worker keeps refreshing
rather than going quiet forever).

Point it at a schedule and forget it:

```bash
# hourly - covers the full 30-city grid roughly once a day
0 * * * * cd /path/to/qorsa_leadgen && python -m workers.osm.main
```

Every run prints where it got to and where the next one will resume:

```
Обработано 350/1080 пар (за этот прогон 50), следующий запуск продолжит с: Уфа / Мебель
```

The grid is walked **city-major** - one city's categories are crawled
together - so a partial sweep gives you usable coverage of somewhere,
rather than a thin smear across everywhere.

### Чекпоинт: `osm_progress.json`

Which pairs are already done. Written **after every completed pair**, not at
the end of the run, for two reasons:

1. **Resumption.** The next run reads it and skips what's done.
2. **Crash-safety.** A killed or crashed run must not re-pay for queries it
   already made. At worst you lose one query's worth of progress.

Pairs are keyed by **name** (`"Москва||Кафе"`), not by index, so inserting a
city at the top of `cities.yml` doesn't silently shift every checkpoint entry
onto a different pair.

A pair whose query **failed** is deliberately left unmarked, so a later run
retries it instead of leaving a permanent hole in the grid.

The file is machine-local run state, not source - it's gitignored. To start
over:

```bash
RESET_PROGRESS=true python -m workers.osm.main   # one-off
rm workers/osm/osm_progress.json                 # or just delete it
```

A corrupt or missing file is treated as an empty checkpoint (logged, not
fatal): re-crawling is annoying, refusing to start is worse.

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

The public Overpass instances rate-limit hard, so this worker:

- pauses `REQUEST_DELAY_SECONDS` (default **5.0s**, raised from 3s when the
  grid grew) after every request, success or failure - not optional
  politeness, the public instances punish bursty clients,
- retries `429` (Too Many Requests) and `504` (Gateway Timeout - Overpass
  is slow and times out its own queries under load) with a **long**
  exponential backoff via `tenacity` (starts at 5s, caps at 120s, up to 5
  attempts),
- **rotates to another public mirror** when one keeps answering 429/504
  (`OVERPASS_ENDPOINTS`, see below),
- gives up on that one (city, category) combo without retrying on any
  other `4xx` (malformed query - retrying won't help, and another mirror
  would reject it identically, so a 4xx does *not* trigger rotation),
- uses a long per-request timeout (`REQUEST_TIMEOUT_SECONDS`, default
  75s) since Overpass itself can be slow to answer,
- caps the whole run with `COMBOS_PER_RUN`.

### Зеркала: `OVERPASS_ENDPOINTS`

A comma-separated list, tried in order (`overpass-api.de`,
`overpass.kumi.systems`, `overpass.osm.ch` by default). An endpoint only gets
rotated away from **after it has exhausted the full retry/backoff sequence** -
we never hop away at the first 429.

### Этика: Overpass - общий бесплатный ресурс

Worth being plain about, because this worker exists to take data from a
service nobody is paying for:

- **Overpass is donated infrastructure.** overpass-api.de and the public
  mirrors are run by volunteers and funded by donations, for everyone. There
  is no paid tier we're declining to buy; there's just a shared resource.
- **This worker is deliberately slow.** 5 seconds between requests, one
  request at a time, no concurrency. A full 1080-pair sweep spends ~90
  minutes just pausing. That's the point - it's spread over ~22 scheduled
  runs rather than fired off at once.
- **Mirror rotation spreads load, it does not multiply quota.** The same
  pacing and the same backoff apply to whichever endpoint is in use, and we
  only move on when an instance has actively told us it's overloaded. That
  is what mirrors are for. Rotating in order to *keep going faster* after
  being told to slow down would be abuse; this isn't that.
- **A 429 is the server working correctly**, not an obstacle. The worker
  backs off and waits, it doesn't try to route around the limit.
- **If you need this data at volume, run your own Overpass instance.** It's
  the honest answer for heavy use - point `OVERPASS_ENDPOINTS` at it and drop
  `REQUEST_DELAY_SECONDS`. Data © OpenStreetMap contributors, ODbL.

If you see repeated `osm.rate_limited_or_timeout` warnings, raise
`REQUEST_DELAY_SECONDS`, lower `COMBOS_PER_RUN`, and/or lower `PAGE_SIZE`.

## Качество данных

Beyond "must have a `name`", the worker drops **national chains and
franchises** by name (`CHAIN_STOPLIST`): a Пятёрочка or a Сбербанк branch
already has a website and an in-house IT team, so it's noise, not a lead.

Matching is **whole-word, not raw substring**, and that distinction is
load-bearing: a substring match on `Магнит` also kills *«Магнитогорская
аптека»*, and one on `Метро` kills *«Кафе у метро»* - exactly the small
independent businesses this pipeline exists to find. Over-filtering is a
*silent* failure, so the stop-list deliberately avoids entries that are also
ordinary Russian words (`Метро`, `Верный`, `Бургер` are **not** in it).

Every run ends with a stats line:

```
osm.run_summary  combos_processed=50 grid_progress=350/1080 remaining_combos=730
                 collected=143 elements_seen=250 skipped_no_name=50
                 skipped_chains=30 skipped_duplicates=27
                 with_phone=88 without_site=101
                 endpoint_switches=0 rate_limit_hits={...}
```

plus an `osm.chains_filtered` line naming the top-10 chains that were
filtered, which is how you tune the stop-list on real data.

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
- `test_endpoint_failover.py` - mirror rotation: switching on persistent
  429/504, walking through several mirrors, per-endpoint hit counting, and
  that a `4xx` does **not** rotate (another mirror would reject it too).
- `test_progress.py` - the checkpoint: save/load across a simulated restart,
  resuming exactly where a run stopped without repeating or skipping a pair,
  name-keying, reset, and corrupt/missing files.
- `test_chains.py` - the stop-list, with as much attention on **false
  positives** (`Магнитогорская аптека` must survive) as on true ones.
- `test_dedup.py` - `DedupTracker` dedup-by-`type/id` behavior.
- `test_runner.py` - the bounded slice, checkpoint integration across runs,
  chain filtering, and the end-of-run statistics.

Retry backoff is per-instance (`retry_base_seconds` etc.) rather than a
module-level decorator specifically so these can run fast: with the
production 5..120s policy a single "endpoint is busy" case slept for minutes,
which made the failover path effectively untestable.

## Files

```
osm/
├── __init__.py
├── config.py              # .env settings + cities.yml/categories.yml loaders (pydantic)
├── cities.yml               # 30 city bounding boxes (generated + validated)
├── categories.yml            # 36 OSM tags (each verified via taginfo + live count)
├── overpass_client.py          # Overpass HTTP client: query building, retries, mirror rotation
├── progress.py                  # (city, category) crawl checkpoint (pure JSON)
├── osm_progress.json             # the checkpoint itself - machine-local, not committed
├── chains.py                      # chain/franchise stop-list filter (pure)
├── mapper.py                       # Overpass element -> RawCompanyRequest (pure)
├── dedup.py                         # in-memory dedup-by-type/id tracker (pure)
├── core_client.py                    # HTTP client to the Java core, with retries
├── runner.py                          # bounded slice: fetch -> filter -> dedup -> send
├── main.py                             # entry point
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py
    ├── test_mapper.py
    ├── test_overpass_client.py
    ├── test_endpoint_failover.py
    ├── test_progress.py
    ├── test_chains.py
    ├── test_runner.py
    └── test_dedup.py
```
