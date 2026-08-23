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

- `rubrics.yml` - business categories, each `{name, query}`; `query` is the
  free-text search term sent as-is. Ships with 18 categories that commonly
  lack a website (cafes, dental clinics, auto repair shops, beauty
  salons, ...).
- `cities.yml` - cities, each `{name, region_id}`.

#### Почему region_id, а не название города в тексте запроса

`GET /3.0/items?q=<рубрика> <город>` (город внутри текстового запроса)
надёжно возвращает **0 результатов**, даже когда рубрика и город реальные.
`GET /3.0/items?q=<рубрика>&region_id=<id>` (город - отдельный параметр)
возвращает данные корректно. Это подтверждено вручную по нескольким
городам: например, `q=кофейня&region_id=38` (Санкт-Петербург) даёт
`total: 3129`. Поэтому `cities.yml` хранит `region_id`, а `twogis_client.py`
всегда передаёт рубрику и регион отдельными параметрами, никогда не
склеивая город в текст `q`.

#### Как узнать / перепроверить region_id города

```
https://catalog.api.2gis.com/2.0/region/search?q=ГОРОД&key=YOUR_KEY
```

Ответ - список кандидатов с полями `id`/`name`; берите `id` того элемента,
чьё `name` точно совпадает с нужным городом (у крупных городов иногда
рядом есть область/край с похожим названием - не перепутайте, как в
примере "Краснодар" (город) vs "Краснодарский край" (регион) при поиске
по этому городу).

Значения в `cities.yml` были получены и перепроверены именно так (плюс
дополнительно подтверждены живым запросом к `/3.0/items` с каждым
`region_id` - у всех 16 `total` > 300 на запрос "кафе"). **Первая версия
этого файла содержала во многом неверные `region_id`** (только Москва,
Санкт-Петербург и Новосибирск совпадали, остальные 13 указывали не туда) -
если вы правили `cities.yml` вручную и не уверены в каком-то значении,
перепроверьте его тем же способом, а не по памяти/со сторонних источников.

#### Уточнение категорий через rubric_id

By default this worker searches rubrics with free text (`q=<rubric>`,
`region_id=<city>`), which works out of the box without needing any
2GIS-internal rubric IDs. For stricter category filtering you can instead
filter by 2GIS's numeric `rubric_id` - to get the authoritative list for
your account/region, call:

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
contract). Requested 2GIS response fields:
`items.contact_groups,items.address_name,items.full_name,items.point,items.rubrics`.

| Field       | Value |
|-------------|-------|
| `name`      | `full_name` if present, else `name` |
| `domain`    | Website contact, if 2GIS returned one in `contact_groups` |
| `phone`     | First `contact_groups` phone contact, if any |
| `address`   | `address_name` |
| `city`      | The name from `cities.yml` for this search pass - **not** anything out of the 2GIS response, which doesn't reliably echo the city back |
| `source`    | always `"TWOGIS"` |
| `sourceUrl` | A 2GIS site-search link for the name+city (2GIS's API response doesn't reliably include a direct firm-page permalink in this shape, so this is a working search deep-link rather than a guessed URL pattern) |
| `hasSite`   | `true` if a website contact was found, `false` otherwise - **this is the important one**: no website is a strong "needs a site" signal, and the core scores it +40 |
| `raw`       | `{ twogis_id, rubrics, rubric_query }` - `rubrics` is 2GIS's own category tags for the org; `rubric_query` is which of *our* `rubrics.yml` entries found it |

`contact_groups` is parsed as `contact_groups[].contacts[]`, matching on
`{"type": "phone" | "website" | "link", "value": "..."}` - see `mapper.py`.

> **Известное ограничение ключа.** На бесплатном/пробном тарифе 2GIS поле
> `contact_groups` (а иногда и `full_name`) может вообще не возвращаться в
> ответе API - без ошибки, `meta.code` всё равно `200`, просто ключа в
> объекте нет. Это проверено вручную (в том числе на известной сети
> заведений, не только на мелких точках) - воркер не падает и корректно
> трактует это как "сайта нет" (`hasSite=false`, `phone=null`), но **если
> у вашего ключа именно так**, это означает, что `hasSite=false` будет
> проставляться почти всем компаниям не потому, что у них правда нет
> сайта, а потому что этот тариф не отдаёт контактные данные вообще.
> Прежде чем полагаться на `hasSite`/`phone` в проде, проверьте вручную,
> возвращает ли ваш ключ `contact_groups` (см. команду выше с
> `fields=items.contact_groups`) - если нет, потребуется тариф 2GIS с
> доступом к контактным данным.

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
pip install -r requirements.txt   # includes pytest + pytest-asyncio
pytest workers/twogis/tests
# or, from inside this directory:
cd workers/twogis && pytest
```

All tests run on hand-built fixture dicts/JSON matching the real 2GIS
response shape - no network, no real 2GIS API calls:

- `test_mapper.py` - item → `RawCompanyRequest` mapping (name/full_name
  fallback, address, city always from config), and the `hasSite` true/
  false logic including missing/empty `contact_groups`.
- `test_twogis_client.py` - `parse_items()` against mocked 2GIS JSON
  response shapes (including malformed/empty ones), plus (via a mocked
  httpx transport) that requests use `region_id` with a rubric-only `q`,
  pagination stops correctly (short page, `total` reached, or the
  `max_pages_per_combo` cap), and 429/5xx retry vs. no-retry-on-4xx
  behavior.
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
