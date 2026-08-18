# qorsa_leadgen

Core engine (the "backbone") of the Qorsa lead-generation pipeline, built with
Spring Boot. This is the **first increment**: ingest boundary, deduplication,
scoring, and storage. It intentionally does **not** contain any real scraper
(Telegram, Google Maps, 2GIS, Avito, ...) — those are separate Python workers
that will POST raw findings to this service over HTTP. A demo profile with
fake data stands in for them so the whole pipeline can be exercised end to
end without a single real scraper.

## Architecture in one paragraph

A scraper worker POSTs a batch of raw company sightings to
`POST /api/v1/companies/ingest`. `IngestService` normalizes each one
(domain/phone/name), asks `DedupService` whether it already knows this
company (exact domain, exact phone, or fuzzy name within the same city), and
either merges the new data into the existing `Company` or creates a new one.
Every company is then scored by `ScoringService` using weights/thresholds
from configuration, and a `Lead` is created or refreshed with the result.
Nothing downstream of the ingest endpoint knows or cares which scraper the
data came from — that's the whole point of the boundary.

```
Python worker (Telegram / Maps / 2GIS / ...)
        │  POST /api/v1/companies/ingest  (List<RawCompanyRequest>)
        ▼
IngestController -> IngestService
        │              │
        │              ├─ NormalizationUtil   (pure functions)
        │              ├─ DedupService        (domain/phone/fuzzy-name match)
        │              └─ ScoringService       (weighted rules -> score+reason)
        ▼
   Company / Lead  (Postgres, via Spring Data JPA + Flyway)
        ▲
        │  GET /api/v1/leads/top, GET /api/v1/leads?status=HOT
        │
     consumers (dashboard, CRM sync, sales bot, ...)
```

## Stack

- Java 21, Spring Boot 3.3, Maven
- Spring Web, Spring Data JPA, Spring Validation
- PostgreSQL 16 + Flyway migrations
- Lombok
- `me.xdrop:fuzzywuzzy` for fuzzy name matching (token-sort ratio)
- JUnit 5 + Testcontainers (Postgres) for the integration test

## Running it

### 1. Start Postgres

```bash
docker compose up -d
```

This starts `postgres:16` on `localhost:5432` with the credentials from
`.env.example` (copy it to `.env` and adjust if you like — the defaults
already match `application.yml`).

### 2. Run the app with fake demo data

```bash
mvn spring-boot:run -Dspring-boot.run.profiles=demo
```

On startup, `DemoSeeder` (only active under the `demo` profile) pushes ~8
fake companies through the real `IngestService.ingest(...)` pipeline —
including a domain-based duplicate, a fuzzy-name duplicate, a Telegram order
with a mentioned budget, and a company with a negative competitor review —
then logs the top 5 scored leads so you can see dedup and scoring working.

Without the `demo` profile (`mvn spring-boot:run`), the app starts empty and
only responds to the REST API — this is how it runs in a real deployment,
fed by Python workers.

### 3. Try the API

```bash
curl -X POST http://localhost:8080/api/v1/companies/ingest \
  -H "Content-Type: application/json" \
  -d '[{"name":"Кофейня Ромашка","city":"Almaty","hasSite":false,"source":"DEMO"}]'

curl "http://localhost:8080/api/v1/leads/top?limit=10"
curl "http://localhost:8080/api/v1/leads?status=HOT"
```

### Running tests

```bash
mvn test
```

Unit tests (`DedupServiceTest`, `ScoringServiceTest`) run with no database.
`IngestControllerIT` spins up a real Postgres via Testcontainers (Docker must
be running), applies the actual Flyway migration, and exercises the full
ingest -> dedup -> score flow over HTTP.

## API contract for Python workers

This is the **entire** interface a scraper worker needs to know. Everything
else (storage, dedup, scoring) is this service's problem, not the worker's.

### `POST /api/v1/companies/ingest`

Body: a bare JSON array of company sightings (not wrapped in an envelope).

```json
[
  {
    "name": "Кофейня Ромашка",
    "domain": "romashka-coffee.kz",
    "phone": "+7 701 111 22 33",
    "email": "info@romashka-coffee.kz",
    "messenger": "@romashka_coffee",
    "address": "ул. Абая 10",
    "city": "Almaty",
    "source": "TELEGRAM_ORDER",
    "sourceUrl": "https://t.me/some_channel/12345",
    "hasSite": false,
    "raw": {
      "budgetMentioned": true,
      "competitorNegativeReview": false,
      "anythingElseYouWant": "kept as-is in the raw jsonb column"
    }
  }
]
```

Field notes:

| Field       | Required | Notes |
|-------------|----------|-------|
| `name`      | yes      | Only required field. |
| `domain`    | no       | Bare, with `http(s)://`, `www.` or a full URL — all normalized the same way. |
| `phone`     | no       | Any format; only digits are kept for dedup (last 10 kept). |
| `email`     | no       | Also used to recover a domain if `domain` is absent. |
| `messenger` | no       | e.g. a Telegram handle. |
| `address`   | no       | Free text. |
| `city`      | no       | Required for fuzzy-name dedup to run. |
| `source`    | no       | One of `TELEGRAM_ORDER`, `GOOGLE_MAPS`, `TWOGIS`, `YANDEX_REVIEW`, `VACANCY`, `AVITO_JOB`, `DEMO`, `OTHER`. Defaults to `OTHER`. |
| `sourceUrl` | no       | Link back to the original listing/post/review. |
| `hasSite`   | no       | Defaults to `true`. Set `false` when the worker confirms no website exists — this is a strong scoring signal. |
| `raw`       | no       | Free-form map, stored as-is in `companies.raw` (jsonb). `budgetMentioned` and `competitorNegativeReview` booleans are read by the scorer today; anything else is just carried along for later use. |

Response (`IngestResponse`):

```json
{ "created": 1, "merged": 0, "leadsScored": 1, "hotCount": 1 }
```

- `created` — brand-new companies.
- `merged` — candidates matched to an existing company (by domain, phone, or
  fuzzy name) and enriched into it instead of duplicating it.
- `leadsScored` — one scoring pass per input element (equal to batch size).
- `hotCount` — how many of those scoring passes landed the lead in `HOT`.

### `GET /api/v1/leads/top?limit=20`

Top leads by score, descending.

### `GET /api/v1/leads?status=HOT`

Leads filtered by status (`NEW`, `QUALIFIED`, `HOT`, `CONTACTED`, `REJECTED`).
Omit `status` to get everything (capped at 100).

Both endpoints return an array of `LeadResponse`:

```json
{
  "id": "…",
  "companyId": "…",
  "companyName": "Кофейня Ромашка",
  "domain": null,
  "phone": "+7 701 111 22 33",
  "city": "Almaty",
  "source": "TELEGRAM_ORDER",
  "score": 70,
  "hotReason": "нет сайта; прямой интент",
  "niche": null,
  "status": "HOT",
  "createdAt": "2026-08-18T12:00:00Z"
}
```

## Scoring rules (configurable, `leadgen.scoring` in `application.yml`)

| Condition                                             | Weight | Reason              |
|--------------------------------------------------------|-------:|----------------------|
| `hasSite == false`                                     | +40    | "нет сайта" |
| `source` in `{TELEGRAM_ORDER, VACANCY, AVITO_JOB}`      | +35    | "прямой интент" |
| `raw.competitorNegativeReview == true`                  | +30    | "недоволен конкурентом" |
| `raw.budgetMentioned == true`                           | +20    | "упомянут бюджет" |
| `phone` and `city` both present                         | +10    | "есть контакт+гео" |

Lead status thresholds: `score >= 70` → `HOT`, `score >= 40` → `QUALIFIED`,
otherwise `NEW`.

Two rules are reserved for a **future** site-audit worker and are wired into
config (`pagespeed-weight`/`pagespeed-threshold`,
`audit-fails-weight`/`audit-fails-threshold`) but not evaluated yet, since no
source of that data exists in this increment.

## How to add a new source

You do **not** touch this service. Write a new Python worker that:

1. Scrapes/collects whatever it collects (Telegram channel, Google Maps
   listing, a job board, ...).
2. Maps each finding to the `RawCompanyRequest` shape above, picking the
   right `source` enum value (add a new one to `LeadSource` only if it's a
   genuinely new kind of source — most new workers reuse an existing value or
   `OTHER`).
3. `POST`s a batch (or one-by-one) to `/api/v1/companies/ingest`.

Dedup and scoring apply automatically. If the new source needs a scoring
signal that doesn't exist yet, that's a small, explicit change to
`ScoringService`/`ScoringProperties` — not a change to how ingest works.

## Package layout

```
kz.qorsa.leadgen
├── LeadgenApplication
├── config      ScoringProperties
├── domain      Company, Lead, Outreach, LeadStatus, OutreachStatus, LeadSource
├── repository  CompanyRepository, LeadRepository
├── service     NormalizationUtil, DedupService, ScoringService, IngestService
├── web         IngestController, LeadController, GlobalExceptionHandler, dto/
└── seed        DemoSeeder (@Profile("demo"))
```
