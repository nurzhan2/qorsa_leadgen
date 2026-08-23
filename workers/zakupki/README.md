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

## How it works: два уровня запросов

There is **no public JSON/REST API** for keyword-searching zakupki.gov.ru's
notice registry the way this worker needs to (the portal does have an
"open data" SOAP/XML feed, but it requires registering for access and is
built for bulk daily exports, not ad-hoc keyword search). So this worker
does the next best thing, in **two levels**:

**Уровень 1 - страница результатов поиска.** For each keyword in
`keywords.yml`, calls the public search page
(`https://zakupki.gov.ru/epz/order/extendedsearch/results.html`) with a
`searchString` parameter and parses the HTML (`parser.py:
parse_search_html`). This gives, per purchase: registration number,
customer name (+ ИНН when the link happens to carry it), a possibly-
truncated subject, region (often absent - see below), and the raw price
text.

**Уровень 2 - карточка каждой закупки (ОПЦИОНАЛЬНО, включено по
умолчанию).** For each purchase found, additionally fetches that
purchase's own detail page and parses it (`parser.py: parse_purchase_card`)
for the fields the list page doesn't reliably carry: the **full** subject,
**budget as an actual number** (НМЦК, parsed from "8 398 003,33 ₽" style
text), the submission deadline, the law type (44-FZ/223-FZ), and - on
44-FZ notices - a **customer contact phone** straight from the notice's
own "Контактная информация" block.

**This doubles (or worse) the request volume** - one extra request per
purchase, on top of the search page. That's exactly why it's gated:

- `FETCH_DETAILS=true|false` (default `true`) - turn level 2 off entirely
  to only use the search page (faster, half the requests, less data per lead).
- `MAX_DETAILS` (default: same as `TARGET_PER_DAY`) - hard cap on how many
  detail pages get fetched in one run, regardless of how many purchases
  were found, so a run can never balloon into hundreds of extra requests
  by accident.

If a detail fetch is skipped (disabled, cap reached, or the request
itself failed) the lead still gets built from level-1 data alone -
`budget` still comes out as a real number either way (parsed from
whichever page provided the price), just without the deadline/law
type/contact phone that only the detail page carries.

**Verified against the live site while building this** (real search
results, real detail pages, both 44-FZ and 223-FZ notices):

- Region is frequently **not present on the search-results card itself**
  for standard 44-FZ notices, but the 44-FZ **detail page** often has it
  under "Контактная информация" - so level 2 also fills in `city` more
  often than level 1 alone.
- A customer's own **ИНН is often genuinely absent** from the 44-FZ detail
  page too - the only "ИНН" label found in testing belonged to the
  Federal Treasury's payment routing details (a different entity
  entirely), and the parser deliberately does NOT pick that up as the
  customer's INN. Coming back `None` here is expected, not a bug.
- **223-FZ notices use a different detail-page template** than 44-FZ ones.
  `parse_purchase_card`'s selectors were built and verified against a
  44-FZ notice; on a live 223-FZ example, `budget` still came through
  correctly (that part of the markup happens to be shared), but
  `subject`/`deadline`/`law_type`/`customer_phone` came back `None`. This
  is a known gap, not a silent failure - the lead is still sent with
  whatever level 1 + partial level 2 data is available.

### Хрупкость парсера (важно)

This is screen-scraping, not an API - it depends entirely on
zakupki.gov.ru's current HTML/CSS structure, which its operators can
change at any time without notice, and this worker has no way to know
when that happens except by suddenly returning fewer or zero results (or,
for level 2, fewer enriched fields).

To keep this from being an all-or-nothing failure mode:

- Every field is looked up **by its label text** - `.registry-entry__body-title`
  contents ("Предмет закупки"/"Регион поставки") on the search page,
  `.cardMainInfo__title`/`.section__title` contents ("Объект закупки"/
  "Номер контактного телефона"/...) on the detail page - not by position,
  so if the site reorders fields, lookups still find the right one instead
  of silently grabbing the wrong value.
- Looking up a detail-page field by its title class does **not** assume
  any particular wrapping container class either: "Начальная цена" turned
  out to live in a plain `<div class="price">` rather than the
  `.cardMainInfo__section` wrapper every other summary field uses (also
  found live) - the lookup walks up to the title element's own immediate
  parent for the matching value, whatever that parent's class happens to
  be, rather than assuming a specific one.
- Every field extraction is wrapped in its own `try`/`except` - one
  missing/malformed field comes back as `None`, not a crashed card.
- Every card/detail page is parsed in its own `try`/`except` - one
  malformed one is skipped (and logged as `zakupki.card_parse_failed` /
  `zakupki.detail_page_parse_failed`), not a crashed run.
- A card with no registration number at all is skipped outright - there's
  nothing to dedup on or point `sourceUrl` at.

**If this worker suddenly stops finding anything (or stops enriching
anything)**, the most likely cause is that zakupki.gov.ru changed its
markup. Fix: open the relevant page in a browser, inspect a card's/detail
page's current HTML, and update the selectors in `parser.py` to match.
`tests/test_parser.py` has fixtures showing the exact structure this was
built and last verified against, for both pages.

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
that. This applies to **both** levels of requests (search page and detail
pages) - they share the same underlying HTTP client/settings.

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
FETCH_DETAILS=true
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
| `phone`     | The contact phone from the detail page's "Контактная информация" block, when found (44-FZ notices) - lets the core's contact+geo (+10) and phone-type rules fire |
| `city`      | The region, from the search page or (more often) the detail page |
| `source`    | always `"ZAKUPKI"` |
| `sourceUrl` | Link to the purchase notice itself |
| `hasSite`   | always `true` - the customer is a real government/municipal body or state-owned company and almost certainly already has a site; reporting `false` here would falsely trigger the core's "+40 no site" rule |
| `raw`       | `{ budget, subject, inn, deadline, law_type, keyword, reg_number, budgetMentioned: true }` |

`raw.budget` is always a **number** (or `null`) - parsed from whichever
page had the price text, never the raw "8 398 003,33 ₽" string.
`raw.budgetMentioned = true` is deliberate and always set when a lead is
produced: unlike a casually mentioned figure in a chat message, an НМЦК
(начальная (максимальная) цена контракта) is a real, already-approved
budget - exactly what the core's `budgetMentioned` rule (+20) is meant to
reward.

### В Google Таблице

The core's Sheets export doesn't have dedicated "Предмет"/"Бюджет" columns
(most sources don't have that kind of data), so for `ZAKUPKI` leads
specifically, the "Причина" column gets `raw.subject`/`raw.budget`
appended, e.g.:

```
упомянут бюджет | Госзакупка: Разработка сайта для МКУ, бюджет 1 500 000 ₽
```

See `kz.qorsa.leadgen.export.SheetsExporter.displayReason` in the core.

## Duplicate handling

Two independent layers:

- **This worker**, in-memory, per run: won't send the same purchase
  registration number twice, even if it matches more than one keyword.
- **The core**, permanently: dedups by domain/phone/fuzzy-name across
  *all* sources, so re-running this worker daily is harmless even though
  the in-memory dedup set resets on every process restart.

## Rate limiting

zakupki.gov.ru has no documented rate limit for this worker's traffic
pattern, but this worker is still a polite citizen of a government site -
**and detail-page fetching literally doubles the request count**, which
is exactly why `FETCH_DETAILS`/`MAX_DETAILS` exist:

- pauses `REQUEST_DELAY_SECONDS` (default 2.0s) after every request, on
  **both** levels,
- retries `429`/`5xx` responses with exponential backoff via `tenacity`
  (up to 5 attempts) on both the search page and detail page requests,
  and gives up on that one keyword/purchase without retrying on any other
  `4xx`,
- caps pagination per keyword at `MAX_PAGES_PER_KEYWORD` (default 3),
- caps total detail-page fetches per run at `MAX_DETAILS` (default: same
  as `TARGET_PER_DAY`).

If you're getting blocked/rate-limited, the first thing to try is
`FETCH_DETAILS=false` (halves your request count immediately), then
raising `REQUEST_DELAY_SECONDS` and/or lowering `MAX_PAGES_PER_KEYWORD`.

## Tests

```bash
pip install -r requirements.txt   # includes pytest + pytest-asyncio
pytest workers/zakupki/tests
# or, from inside this directory:
cd workers/zakupki && pytest
```

All tests run on hand-built HTML/dict fixtures matching the real
zakupki.gov.ru markup (verified live against the actual site, both the
search page and a real purchase's detail page, while building this) - no
network, no real requests to zakupki.gov.ru:

- `test_parser.py` - `parse_search_html` (search page → purchase dicts:
  full card, multiple cards, missing fields, both 44-FZ/223-FZ link
  formats), `parse_purchase_card` (detail page → enrichment dict: full
  card, missing price block, the "customer INN vs. Treasury INN" honest
  distinction above, blank/garbage input), and `parse_money` (Russian
  currency-text → float, including non-breaking spaces).
- `test_mapper.py` - `merge_purchase_with_details` (level-1/level-2 field
  precedence, budget-parsing fallback when no details were fetched) and
  `map_purchase_to_lead` (dict → `RawCompanyRequest`, including the
  always-`hasSite=true`/always-`budgetMentioned=true` logic).
- `test_dedup.py` - `DedupTracker` dedup-by-registration-number behavior.

## Files

```
zakupki/
├── __init__.py
├── config.py           # .env settings + keywords.yml loader (pydantic)
├── keywords.yml          # editable search keyword list
├── zakupki_client.py       # HTTP client: search page + detail pages, retries, rate limiting
├── parser.py                 # HTML -> dicts (pure, BeautifulSoup): search results + detail card + money parsing
├── mapper.py                   # purchase dict (+ optional details) -> RawCompanyRequest (pure)
├── dedup.py                      # in-memory dedup-by-reg-number tracker (pure)
├── core_client.py                  # HTTP client to the Java core, with retries
├── runner.py                         # orchestrates keyword -> search -> [details] -> dedup -> send
├── main.py                             # entry point
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py
    ├── test_parser.py
    ├── test_mapper.py
    └── test_dedup.py
```
