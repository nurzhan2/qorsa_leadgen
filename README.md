# qorsa_leadgen

**A polyglot B2B lead-generation platform: a Java / Spring Boot core with independent Python collector workers, feeding a scored, deduplicated lead pipeline and a live spreadsheet dashboard.**

Built to solve a real problem — finding businesses that need web / bot / AI development — by aggregating many public sources, qualifying them automatically, and surfacing only the hottest leads.

---

## Why this project

Most "scrapers" dump raw contacts into a file. This one is designed as a **pipeline**: many sources feed a single core that **deduplicates, scores, and qualifies** every company, so a human only ever looks at leads worth contacting. The architecture is deliberately **polyglot** — a strongly-typed Java core for the business logic that must be correct, and disposable Python workers for the messy, source-specific scraping.

```
┌─────────────────────────────────────────────────────────────┐
│                     COLLECTOR WORKERS (Python, async)        │
│                                                              │
│  Telegram        OpenStreetMap     2GIS        Google Places │
│  (order feeds)   (Overpass API)   (Catalog)    (Places API)  │
│                                                              │
│  Госзакупки      New domains                                 │
│  (zakupki.gov)   (WHOIS feed)                                │
└───────────────────────────┬─────────────────────────────────┘
                            │  POST /api/v1/companies/ingest
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  CORE (Java 21 / Spring Boot 3)              │
│                                                              │
│   Ingest → Normalize → Deduplicate → Score → Persist        │
│                                                              │
│   • fuzzy + exact dedup (domain / phone / name)             │
│   • config-driven scoring engine (no hardcoded weights)     │
│   • phone classification (mobile / landline / toll-free)    │
│   • REST API  •  PostgreSQL + Flyway  •  JPA / Hibernate    │
└───────────────────────────┬─────────────────────────────────┘
                            │  scheduled sync (every 5 min)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│         DASHBOARD  —  Google Sheets (live, formatted)       │
│   tabs by temperature (🔥 hot / ❄️ cold) and by source      │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech stack

**Core** — Java 21, Spring Boot 3.3, Spring Data JPA / Hibernate, PostgreSQL, Flyway (versioned migrations), Maven, JUnit 5 + Testcontainers, Google Sheets API v4.

**Workers** — Python 3.11, `asyncio`, `httpx`, `pydantic` v2, `structlog`, `tenacity` (retry/backoff), `BeautifulSoup`, `Telethon`, `pytest`.

**Infra** — Docker Compose (local PostgreSQL), config-via-environment, secrets isolated in `.env` / service-account files (git-ignored).

---

## Architecture decisions

- **Polyglot by design.** The core owns everything that must be correct and typed — dedup, scoring, persistence, the ingest contract. Workers are cheap and replaceable; a broken scraper never touches core logic. The seam between them is a single REST contract (`POST /companies/ingest`), so a new source is *just another worker*, no core changes required.

- **Config-driven scoring.** Every scoring weight and threshold lives in `application.yml`, not in code. Rules like *"no website → +40"*, *"direct intent → +35"*, *"mobile number → +15"* can be tuned without recompiling.

- **Deduplication as a first-class concern.** Companies arrive from many overlapping sources. Dedup runs on normalized domain, normalized phone, and fuzzy name matching (FuzzyWuzzy token-sort ratio), merging records and enriching empty fields rather than creating duplicates.

- **Qualification over volume.** The goal is never "300 raw contacts" — it's a funnel where automatic scoring pushes the ~20-40 genuinely hot leads to the top and leaves the rest as a cold reserve.

- **Intent filtering.** The Telegram worker distinguishes *one-off orders* ("need a landing page, budget X") from *staff vacancies* ("hiring a developer, salary Y") via an anti-hiring keyword layer, plus a **channel-rater** that samples each channel's history and scores it on order-rate so low-signal channels can be dropped automatically.

---

## Data model

| Entity | Purpose |
|---|---|
| `Company` | Deduplicated business entity — name, domain, phones (classified), address, city, source, raw JSONB payload |
| `Lead` | Scored view of a company — score, status (`NEW`/`QUALIFIED`/`HOT`), human-readable reason, audit JSONB |
| `Outreach` | First-contact tracking — channel, message, status |

---

## Sources

| Source | Type | Signal |
|---|---|---|
| **Telegram** order channels | Live stream | Direct intent — someone actively asking for a service |
| **OpenStreetMap** (Overpass) | Cold batch | Businesses by category × city, with contacts where available |
| **2GIS Catalog** | Cold batch | Russian business directory (region-scoped) |
| **Google Places** | Cold batch | High-quality contacts (phone + website) |
| **Госзакупки** (zakupki.gov.ru) | Warm batch | Public tenders for software/site development — **approved budget** |
| **New domains** | Cold batch | Freshly registered domains without a real site yet |

Each source is an isolated worker with its own `requirements.txt`, `.env`, tests, and README.

---

## Engineering problems solved along the way

A short, honest log — these are the kind of real-world issues the project ran into and how they were fixed:

- **`LazyInitializationException`** on lead serialization — fixed with targeted JPA fetch-joins rather than a blanket `EAGER` (which would have caused N+1 elsewhere).
- **PostgreSQL encoding** — the DB was created as `WIN1251` on a Russian Windows host and rejected the `₽` symbol from tender data; rebuilt the database as `UTF8` (`template0`, `C` locale).
- **Russian state CA TLS** — `zakupki.gov.ru` uses a Ministry-issued certificate absent from the default trust store; handled via an explicit, documented `VERIFY_SSL` toggle.
- **Two-level tender parsing** — search-results pages omit budget/subject; the worker now follows each tender into its card and parses `44-ФЗ` / `223-ФЗ` layouts defensively (per-field `try/except`).
- **API rate limits** — Overpass `429/504` and Telegram `FloodWait` are handled with exponential backoff and per-request pacing instead of hammering endpoints.
- **Verified over assumed data** — region IDs and API response shapes were validated against live endpoints before being trusted in code.

---

## Running it

**Core** (requires Docker + JDK 21 + Maven):

```bash
docker compose up -d                 # PostgreSQL
mvn spring-boot:run                  # starts the core on :8080
```

**A worker** (example — OpenStreetMap, no API key needed):

```bash
cd workers/osm
cp .env.example .env                 # set CORE_URL
pip install -r requirements.txt
python -m workers.osm.main
```

**Tests:**

```bash
mvn test                             # Java: unit + Testcontainers integration
pytest workers/                      # Python: all workers
```

---

## Project layout

```
qorsa_leadgen/
├── src/main/java/kz/qorsa/leadgen/
│   ├── domain/          # JPA entities + enums
│   ├── service/         # dedup, scoring, normalization, phone classification
│   ├── web/             # REST controllers + DTOs
│   ├── export/          # Google Sheets exporter
│   └── repository/      # Spring Data JPA
├── src/main/resources/
│   ├── application.yml  # config-driven scoring weights
│   └── db/migration/    # Flyway migrations
├── workers/
│   ├── telegram_monitor/  # live order-channel monitor + channel rater
│   ├── osm/               # OpenStreetMap / Overpass
│   ├── twogis/            # 2GIS Catalog API
│   ├── google_places/     # Google Places API
│   ├── zakupki/           # government tenders (two-level parse)
│   └── newdomains/        # freshly-registered domains
├── docker-compose.yml
└── pom.xml
```

---

## Roadmap

- Additional sources: Yandex Maps, Avito business listings
- Message queue between workers and core (currently direct HTTP — sufficient at this scale)
- AI-generated first-contact drafts per hot lead
- Cloud deployment (Railway) for the core + PostgreSQL

---

*Built as a full-stack, polyglot systems project — Spring Boot backend, async Python workers, real external APIs, and the operational problems that come with real data.*
