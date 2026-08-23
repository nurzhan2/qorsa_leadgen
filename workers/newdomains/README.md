# newdomains

A **sleeping** lead source for `qorsa_leadgen`: a fresh domain
registration with no real site up yet is a strong "just started a
business, hasn't built a site" signal. This worker is a complete,
working scaffold that does nothing useful until you pay for and configure
a newly-registered-domains feed - see "Это платно, честно" below.

Fully self-contained - its own `requirements.txt`, its own `.env`, its own
README. It does not import anything from, or get imported by, the Java
core or any other worker.

## Это платно, честно

There is **no free stream** of newly-registered `.ru`/`.com` domains.
Registries don't publish one for free, and the handful of companies that
aggregate this data (by mirroring zone files and WHOIS records daily) sell
it as a paid product. This worker is built against **WhoisXML API's
"Newly Registered Domains"** feed as a concrete example:

- Product page: <https://newly-registered-domains.whoisxmlapi.com/>
- Pricing is subscription/plan-based and **not published as static
  numbers on the page** (it's rendered client-side) - check
  <https://newly-registered-domains.whoisxmlapi.com/> yourself for current
  plans; WhoisXML (and similar vendors) commonly offer a free trial with a
  small daily/monthly record quota, which is enough to test this worker
  end-to-end before committing to a paid plan.
- Alternatives exist (e.g. DomainTools, Domainhole, WHOISDS-based
  aggregators) - any of them can be wired in via the `custom` provider
  (see below) without touching this worker's core logic, as long as you
  can shape their response into `{domain, registered_date, registrar}`.

**Without a provider configured, this worker is inert by design**: it
logs `NEWDOMAINS provider not configured, exiting` and exits with code 0 -
no crash, no stack trace. The rest of `qorsa_leadgen` works completely
normally without it. Once you pay for and configure a real feed, this
starts working with **zero code changes** - just `.env` values.

## Setup (once you have a feed)

### 1. Get a WhoisXML API key

1. Go to <https://newly-registered-domains.whoisxmlapi.com/> and sign up.
2. Subscribe to a plan for the "Newly Registered Domains" product (check
   for a free trial first to test this worker before paying).
3. Copy the issued API key.

### 2. Configure

```bash
cd workers/newdomains
cp .env.example .env
```

Fill in `.env`:

```
DOMAINS_PROVIDER=whoisxml
DOMAINS_API_KEY=your-key-here
DOMAINS_TLDS=ru,com
CORE_URL=http://localhost:8081
```

Install dependencies (a virtualenv is recommended):

```bash
python -m venv .venv
.venv/Scripts/activate   # or `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
```

### 3. Run it

```bash
# from the repo root, with the Java core already running
python -m workers.newdomains.main
```

With `RUN_ONCE=true` (the default) it does one pass and exits - good for a
daily cron job (fresh-domain feeds are inherently a once-a-day thing:
there's a new batch each day, not a live stream). Set `RUN_ONCE=false` to
have it loop, sleeping `LOOP_INTERVAL_HOURS` (default 24) between passes.

### Using a different provider ("custom")

If you have (or prefer) a different NRD feed, set:

```
DOMAINS_PROVIDER=custom
DOMAINS_API_KEY=your-key-here
DOMAINS_API_URL=https://your-feed.example/api/new-domains
```

`CustomHttpProvider` (in `providers.py`) sends `Authorization: Bearer
<key>` and expects either a bare JSON array or a `{"domains": [...]}`/
`{"results": [...]}` envelope of `{domain, registered_date, registrar}`
objects. If your feed's shape doesn't match, either adapt it behind a tiny
proxy, or add a new `NewDomainsProvider` subclass in `providers.py` (the
interface is one async method: `fetch_new_domains`).

### ⚠️ WhoisXML integration: unverified against a live key

`WhoisXmlNrdProvider` (in `providers.py`) is built from WhoisXML's
publicly documented API shape. **It has not been tested against a real
key** - this product requires payment, and none was available while
building this. Before trusting it in production:

1. Run it once against your own key.
2. Log/inspect the raw JSON response.
3. Check it matches what `_extract_records()` in `providers.py` expects
   (`newRegisteredDomains` / `domainsList` / `results` - it tries a few
   known key names and returns nothing rather than raising for an
   unrecognized shape, so a mismatch shows up as **zero results**, not a
   crash).
4. Adjust the key names there if WhoisXML's API has changed, or your
   specific plan returns something different.

## How the "does it have a site" check works

For each fresh domain, `site_checker.py` does a real HTTP GET (`https`
first, falling back to `http`) and classifies the result:

- connection fails on both schemes → no site,
- HTTP status `>= 400` → no site,
- response body is very short (< 200 chars of actual content) → no site,
- response body matches a common parking/placeholder phrase ("this domain
  may be for sale", "coming soon", "parked free", "sedo", "under
  construction", ...) → no site,
- otherwise → has a site.

This isn't a perfect content classifier (a legitimate, very minimal
single-page site could get miscategorized as "parked"), but it correctly
handles the overwhelming majority of the two cases that actually matter
here: a domain sitting on a registrar parking page, and a domain with
nothing served at all.

## What gets sent to the core

One `RawCompanyRequest` per domain (see the core's own README for the full
JSON contract).

| Field       | Value |
|-------------|-------|
| `name`      | The domain itself |
| `domain`    | The domain itself |
| `phone`/`email` | Always blank - WHOIS contact info is almost always redacted (GDPR/registrar privacy) these days; the domain itself is still a useful lead |
| `source`    | always `"NEW_DOMAIN"` |
| `hasSite`   | Result of the site check above - `false` is the strong "just registered, hasn't built a site" signal the core scores +40 for |
| `raw`       | `{ registered_date, registrar }` |

## Duplicate handling

Two independent layers:

- **This worker**, in-memory, per run: won't send the same domain twice.
- **The core**, permanently: dedups by domain across *all* sources, so
  re-running this worker daily is harmless even though the in-memory
  dedup set resets on every process restart.

## Tests

```bash
pip install -r requirements.txt   # includes pytest + pytest-asyncio
pytest workers/newdomains/tests
# or, from inside this directory:
cd workers/newdomains && pytest
```

All tests run on hand-built fixtures and a mocked httpx transport - no
network, no real provider API calls, no real site checks:

- `test_site_checker.py` - parking-page/empty-page detection (pure logic)
  and `check_has_site()` against a mocked transport (real site, parking
  page, both schemes failing, https-fails-falls-back-to-http).
- `test_providers.py` - `create_provider()` selection logic for every
  configuration combination, plus both `WhoisXmlNrdProvider` and
  `CustomHttpProvider` against mocked responses (including unrecognized
  response shapes, which must yield zero results, not a crash).
- `test_mapper.py` - domain record → `RawCompanyRequest` mapping.
- `test_dedup.py` - `DedupTracker` dedup-by-domain behavior.
- `test_main.py` - locks down the exact condition the "no provider ->
  exit cleanly" behavior in `main.py` relies on, without ever calling
  `main._run()` end-to-end (which would risk making a real, billed
  provider API call if a real key is ever configured in this directory's
  `.env`).

## Files

```
newdomains/
├── __init__.py
├── config.py            # .env settings (pydantic) - no YAML files, provider is the only real input
├── providers.py            # NewDomainsProvider interface + WhoisXmlNrdProvider + CustomHttpProvider
├── site_checker.py            # has-a-real-site-or-not HTTP check (pure logic + async check)
├── mapper.py                     # domain record -> RawCompanyRequest (pure)
├── dedup.py                        # in-memory dedup-by-domain tracker (pure)
├── core_client.py                    # HTTP client to the Java core, with retries
├── runner.py                           # orchestrates fetch -> dedup -> site-check -> send
├── main.py                               # entry point (friendly exit if no provider configured)
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py
    ├── test_site_checker.py
    ├── test_providers.py
    ├── test_mapper.py
    ├── test_dedup.py
    └── test_main.py
```
