# zakupki

A **hot** lead source for `qorsa_leadgen`: searches the Russian state
procurement portal ([zakupki.gov.ru](https://zakupki.gov.ru)) for purchase
notices related to website/software development, and forwards the
customer (the government/municipal body, or state-owned company under
223-FZ, placing the order) to the Java core over
`POST /api/v1/companies/ingest`. A public procurement notice with an
approved budget is about as strong a buying-intent signal as exists - the
core scores it accordingly (`raw.budgetMentioned = true` → +20, on top of
whatever else applies).

Fully self-contained - its own `requirements.txt`, its own `.env`, its own
README. It does not import anything from, or get imported by, the Java
core or any other worker.

## How it works

There is **no public JSON/REST API** for keyword-searching zakupki.gov.ru's
notice registry the way this worker needs to (the portal does have an
"open data" SOAP/XML feed, but it requires registering for access and is
built for bulk daily exports, not ad-hoc keyword search). So this worker
does the next best thing: it calls the **public search results page**
(`https://zakupki.gov.ru/epz/order/extendedsearch/results.html`) with a
`searchString` query parameter, the same page a person searching the site
in a browser would get, and parses the returned HTML (`parser.py`, via
BeautifulSoup).

For each keyword in `keywords.yml`, it pages through results (capped at
`MAX_PAGES_PER_KEYWORD`), and for each purchase notice card extracts:

- registration number (used for dedup and, combined with the base URL,
  the notice's own permalink),
  - customer name and, if shown, their ИНН (parsed out of the customer's
  organization-page link),
- the purchase subject (предмет закупки),
- the region, if shown,
- the starting price (НМЦК).

**Verified against the live site while building this**: region is
frequently **not present on the search-results card itself** for standard
(44-FZ) state purchase notices - only 223-FZ (corporate procurement)
notices reliably showed it in testing. So `city` on the resulting lead is
often `null` for this source, which is expected, not a parsing bug - the
customer name, purchase link, and budget are what carry the real signal
here.

### Хрупкость парсера (важно)

This is screen-scraping, not an API - it depends entirely on
zakupki.gov.ru's current HTML/CSS structure, which its operators can
change at any time without notice, and this worker has no way to know
when that happens except by suddenly returning fewer or zero results.

To keep this from being an all-or-nothing failure mode:

- Every field is looked up **by its label text**
  (`.registry-entry__body-title` contents like "Предмет закупки"/"Регион
  поставки"), not by position - so if the site reorders fields, lookups
  still find the right one instead of silently grabbing the wrong value.
- Every field extraction is wrapped in its own `try`/`except` - one
  missing/malformed field comes back as `None`, not a crashed card.
- Every card is parsed in its own `try`/`except` - one malformed card is
  skipped (and logged as `zakupki.card_parse_failed`), not a crashed page.
- A card with no registration number at all is skipped outright - there's
  nothing to dedup on or point `sourceUrl` at.

**If this worker suddenly stops finding anything**, the most likely cause
is that zakupki.gov.ru changed its markup and the CSS selectors in
`parser.py` (`.search-registry-entry-block`, `.registry-entry__body-block`,
`.registry-entry__body-title`/`-value`, `.registry-entry__body-href`,
`.price-block__value`) no longer match. Fix: open the search page in a
browser, inspect a result card's current HTML, and update the selectors in
`parser.py` to match. `tests/test_parser.py` has fixtures showing the
structure this was built and last verified against.

## Setup

### 1. No API key needed

The search this worker uses is public. No registration, no key.

### 2. TLS certificate note

zakupki.gov.ru's certificate is issued by a Russian CA ("Минцифры
России"/Russian Trusted Root CA) that isn't in most default OS/browser
trust stores outside Russia - you may see an SSL/TLS "untrusted root"
error even though the connection itself is fine. The honest fix is
installing the [Russian Trusted Root
CA](https://www.gosuslugi.ru/crt) into your system's trust store. Only as
a last resort, set `VERIFY_SSL=false` in `.env` - understand that this
disables certificate validation entirely (vulnerable to MITM) before doing
that.

### 3. Configure

```bash
cd workers/zakupki
cp .env.example .env
```

The defaults in `.env.example` already work as-is:

```
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

### 4. Fill in `keywords.yml`

A plain list of search phrases, edited without touching code. Ships with:
"разработка сайта", "создание сайта", "разработка программного
обеспечения", "внедрение программного обеспечения", "автоматизация
бизнес-процессов", "разработка чат-бота".

### 5. Run it

```bash
# from the repo root, with the Java core already running
python -m workers.zakupki.main
```

With `RUN_ONCE=true` (the default) it does one pass across every keyword
(capped at `TARGET_PER_DAY` companies total) and exits - good for a daily
cron job / scheduled task. Set `RUN_ONCE=false` to have it loop forever,
sleeping `LOOP_INTERVAL_HOURS` (default 24) between passes.

## What gets sent to the core

One `RawCompanyRequest` per purchase notice, batched 50-at-a-time to
`/api/v1/companies/ingest` (see the core's own README for the full JSON
contract).

| Field       | Value |
|-------------|-------|
| `name`      | The customer's name (заказчик) |
| `city`      | The region shown on the notice, if any |
| `source`    | always `"ZAKUPKI"` |
| `sourceUrl` | Link to the purchase notice itself |
| `hasSite`   | always `true` - the customer is a real government/municipal body or state-owned company and almost certainly already has a site; reporting `false` here would falsely trigger the core's "+40 no site" rule |
| `raw`       | `{ budget, subject, inn, keyword, reg_number, budgetMentioned: true }` |

`raw.budgetMentioned = true` is deliberate and always set when a lead is
produced: unlike a casually mentioned figure in a chat message, an НМЦК
(начальная (максимальная) цена контракта) is a real, already-approved
budget - exactly what the core's `budgetMentioned` rule (+20) is meant to
reward.

## Duplicate handling

Two independent layers:

- **This worker**, in-memory, per run: won't send the same purchase
  registration number twice, even if it matches more than one keyword.
- **The core**, permanently: dedups by domain/phone/fuzzy-name across
  *all* sources, so re-running this worker daily is harmless even though
  the in-memory dedup set resets on every process restart.

## Rate limiting

zakupki.gov.ru has no documented rate limit for this worker's traffic
pattern, but this worker is still a polite citizen of a government site:

- pauses `REQUEST_DELAY_SECONDS` (default 2.0s) after every request,
- retries `429`/`5xx` responses with exponential backoff via `tenacity`
  (up to 5 attempts), and gives up on that one keyword without retrying on
  any other `4xx`,
- caps pagination per keyword at `MAX_PAGES_PER_KEYWORD` (default 3).

## Tests

```bash
pip install -r requirements.txt   # includes pytest + pytest-asyncio
pytest workers/zakupki/tests
# or, from inside this directory:
cd workers/zakupki && pytest
```

All tests run on hand-built HTML/dict fixtures matching the real
zakupki.gov.ru markup (verified live against the actual site while
building this) - no network, no real requests to zakupki.gov.ru:

- `test_parser.py` - HTML → purchase dict parsing: a full valid card,
  multiple cards on one page, missing registration number (card skipped),
  missing customer link, missing price block, completely unexpected/blank
  markup, and both the relative (44-FZ) and absolute (223-FZ) notice link
  formats.
- `test_mapper.py` - purchase dict → `RawCompanyRequest` mapping,
  including the always-`hasSite=true`/always-`budgetMentioned=true` logic
  and the "no customer name → skip" case.
- `test_dedup.py` - `DedupTracker` dedup-by-registration-number behavior.

## Files

```
zakupki/
├── __init__.py
├── config.py           # .env settings + keywords.yml loader (pydantic)
├── keywords.yml          # editable search keyword list
├── zakupki_client.py       # HTTP client for the public search page: retries, rate limiting
├── parser.py                 # search-results HTML -> purchase dicts (pure, BeautifulSoup)
├── mapper.py                   # purchase dict -> RawCompanyRequest (pure)
├── dedup.py                      # in-memory dedup-by-reg-number tracker (pure)
├── core_client.py                  # HTTP client to the Java core, with retries
├── runner.py                         # orchestrates keyword -> search -> parse -> dedup -> send
├── main.py                             # entry point
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py
    ├── test_parser.py
    ├── test_mapper.py
    └── test_dedup.py
```
