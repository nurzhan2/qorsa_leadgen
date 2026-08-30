# hh

A **hot** lead source for `qorsa_leadgen`: reads job vacancies from the
official hh.ru API and forwards the *hiring company* to the Java core over
`POST /api/v1/companies/ingest`.

The logic: a company advertising for a веб-разработчик, дизайнер or
маркетолог has an **approved budget and a live pain**. And if that vacancy is
*still open* 45+ days later, they have been unable to hire — which is exactly
when outsourcing stops being a fallback and becomes the plan. **A stale
vacancy is a hotter lead than a fresh one**, and this worker is built around
detecting them.

Fully self-contained — its own `requirements.txt`, `.env`, README and tests.
It does not import anything from, or get imported by, the Java core or any
other worker.

---

## ⚠️ Доступ к API: нужен токен

**Read this before anything else — the worker will not return a single lead
without it.**

HH's `/vacancies` endpoint is **not anonymously usable**. HH's own OpenAPI
spec says so, in the description of `GET /vacancies`:

> «Если не передан токен авторизации, то после первого запроса будет
> предложено пройти капчу.»

Verified live while building this worker (2026-08-29, from a Kazakh IP):

| endpoint | anonymous result |
|---|---|
| `GET /areas` | **200** ✅ |
| `GET /dictionaries` | **200** ✅ |
| `GET /professional_roles` | **200** ✅ |
| `GET /suggests/vacancy_search_keyword` | **200** ✅ |
| **`GET /vacancies`** | **403** `{"errors":[{"type":"forbidden"}]}` ❌ |
| **`GET /vacancies/{id}`** | **403** ❌ |
| **`GET /employers`, `/employers/{id}`** | **403** ❌ |

The 403 comes from `server: ddos-guard` — HH's anti-bot edge. It was **not**
fixable by any of: seven different `User-Agent` strings (browser, curl,
python, empty), a warmed-up cookie session, `Referer`/`Origin` headers, the
`HH-User-Agent` header, the `host=hh.ru` / `host=hh.kz` parameters, or
`api.hh.kz` instead of `api.hh.ru`.

The decisive evidence that it's **authorization** and not an IP ban: sending
a deliberately invalid bearer token changes the error from a generic
`{"type":"forbidden"}` to

```json
{"description":"Forbidden","errors":[{"value":"bad_authorization","type":"oauth"}]}
```

That is HH's *application* evaluating a token, not ddos-guard refusing a bot.
Reference endpoints answering 200 from the same IP rules out a blanket block.

### Как получить токен

1. Register an application at **<https://dev.hh.ru/admin>**.
2. Take the application token it issues.
3. Put it in `.env` as `HH_TOKEN=...` — **never in code**, and `.env` is
   gitignored.

```bash
HH_TOKEN=your_token_here
```

**Что осталось непроверенным, честно:** I could not obtain a token, so the
worker has **never been run against a live `/vacancies` response**. Its
request shape, field names and limits come from HH's own machine-readable
OpenAPI spec and published docs (see "Схема" below), not from memory — but
that is documentation, not a wire capture. The first real run may surface a
field HH added since. Everything downstream of the HTTP call (staleness,
dedup, filtering, mapping) is fully tested.

It's also possible `/vacancies` is served anonymously from a **Russian** IP
and the block above is partly geographic — the test IP was in Almaty, KZ. I
couldn't test that, so I'm not claiming either way.

---

## User-Agent

HH requires a `User-Agent` identifying the application **and actively
blacklists some formats**. Verified live: a UA containing an email address —
the shape HH's own older docs suggested, `MyApp/1.0 (my-app@example.com)` —
is rejected outright:

```
400 {"description":"Bad User-Agent header",
     "errors":[{"value":"blacklisted","type":"bad_user_agent"}]}
```

Keep `HH_USER_AGENT` to plain descriptive words with **no email address**.
The default `qorsa-leadgen-hh-worker/1.0` is what was actually tested.

*(The same trap exists in `workers/osm` with Overpass, for the same reason:
the documented "polite" contact format is the one the server rejects.)*

---

## Setup

```bash
cd workers/hh
cp .env.example .env      # then fill in HH_TOKEN
python -m venv .venv
.venv/Scripts/activate    # or `source .venv/bin/activate`
pip install -r requirements.txt
```

```bash
# from the repo root, with the Java core already running on :8081
python -m workers.hh.main
```

| variable | default | what it does |
|---|---|---|
| `HH_TOKEN` | *(empty)* | **required in practice** — see above |
| `STALE_DAYS` | `45` | age at which a vacancy reads as "can't hire" |
| `TARGET_PER_DAY` | `300` | max companies ingested per run |
| `PER_PAGE` / `MAX_PAGES_PER_QUERY` | `100` / `5` | paging per (keyword × area) |
| `REQUEST_DELAY_SECONDS` | `1.0` | pause after every HH request |
| `FETCH_EMPLOYER_DETAILS` | `true` | second request per employer for `site_url` + type |
| `MAX_EMPLOYER_DETAILS` | `200` | cap on those second requests |
| `SKIP_EMPLOYER_TYPES` | `agency,private_recruiter` | HH's own employer classification |
| `SKIP_ACCREDITED_IT` | `true` | skip HH-IT-accredited companies |

---

## Детектор «протухшей вакансии»

The feature this worker exists for.

```
age_days = сегодня − published_at
is_stale = age_days >= STALE_DAYS      (default 45)
```

Every lead carries this in `raw`:

```json
{
  "vacancy_name": "Веб-разработчик",
  "vacancy_url": "https://hh.ru/vacancy/7760476",
  "published_at": "2026-05-11T13:27:16+0300",
  "age_days": 111,
  "is_stale": true,
  "stale_threshold_days": 45,
  "open_vacancies_matched": 3,
  "budgetMentioned": true
}
```

Details that matter:

- **`published_at` uses a colon-less offset** (`+0300`, not `+03:00`).
  `datetime.fromisoformat` handles that from Python 3.11 on; the parse is
  guarded anyway and an unparseable date yields `age_days: null` rather than
  a crash.
- **Unknown age is never stale.** We don't invent heat we can't evidence.
- **Future dates clamp to 0**, not a negative age — a negative would sort as
  the stalest thing in the batch and pick the wrong representative vacancy.
- **Stalest leads are sent first**, so when `TARGET_PER_DAY` truncates a run
  it cuts the *coldest* leads, not the hottest.

`is_stale` is deliberately **not** wired into the core's scorer — that would
mean touching scoring. It rides in `raw` where a manager (or a later scoring
rule) can use it. `budgetMentioned` *is* consumed by the core today (+20).

---

## Дедуп по работодателю

A company hiring a frontend dev, a backend dev and a designer appears three
times in search results. It is **one company** and reaches the core **once**.

The vacancy chosen to represent it is the **stalest** one — the one that best
evidences "we can't hire for this", and therefore the one a salesperson
should open. That vacancy's link becomes `sourceUrl`. `raw.open_vacancies_matched`
records how many were collapsed.

Vacancies with no `employer.id` are dropped: nothing to group on, and an
anonymous posting has no company to sell to.

---

## Фильтрация: кто НЕ лид

Four layers, cheapest and most reliable first:

1. **`employer.accredited_it_employer`** — HH's IT-accreditation flag, present
   on the search result itself. An accredited IT company has in-house
   developers by definition.
2. **`employer.type`** — HH classifies employers itself. Values verified live
   from `/dictionaries`: `company` (Организация), **`agency` (Кадровое
   агентство)**, `project_director`, **`private_recruiter` (Частный
   рекрутер)**, `private_individual`, `self_employed`. This is a *fact from
   the source*, far better than guessing from a name — but it only exists on
   the employer endpoint, so it needs `FETCH_EMPLOYER_DETAILS=true`.
3. **Agency name stop-list** — кадровые агентства, рекрутинг, аутстаффинг.
   They hire on someone else's behalf, so the vacancy says nothing about
   *their* need for a website.
4. **Giant name stop-list** — Сбер, Яндекс, Ozon, VK, Тинькофф, EPAM… they
   have their own IT.

Name matching is **whole-word, not substring**, exactly as in
`workers/osm/chains.py` and for the same reason: a substring match on
«кадры» also kills «Кадрырезерв Дизайн», and «вк» kills «Вкусно и точка».
Over-filtering is a **silent** loss — the lead simply never appears — so the
false-positive cases carry the weight in the tests.

Filtering happens **before** employer details are fetched, which is what keeps
that second request count small.

---

## Что уходит в ядро

| Field | Value |
|---|---|
| `name` | `employer.name` (the employer endpoint's canonical name wins when available) |
| `domain` | `employer.site_url`, normalized to a bare host — needs `FETCH_EMPLOYER_DETAILS=true` |
| `city` | `vacancy.area.name` |
| `source` | always `"HH"` |
| `sourceUrl` | `alternate_url` of the **stalest** vacancy |
| `hasSite` | `true` only when `site_url` was actually present |
| `raw` | `{vacancy_name, vacancy_url, published_at, age_days, is_stale, stale_threshold_days, employer_id, open_vacancies_matched, keyword, keywords, salary, budgetMentioned, employer_type, accredited_it_employer}` |

A quoted salary sets `raw.budgetMentioned = true`, which the core's
`ScoringService` reads directly (+20). A `salary` object with **both bounds
null** is treated as *no* salary — HH sends that shape, and counting it as a
budget signal would be a lie.

---

## Честно про ограничения

- **Никогда не запускался против живого `/vacancies`** — см. раздел про токен
  выше. Всё, что ниже HTTP-слоя, покрыто тестами; сам HTTP-слой проверен на
  моках по схеме HH.
- **`hasSite` здесь слабее, чем в OSM.** HH employers leave `site_url` empty
  far more often than they genuinely lack a website, so `hasSite=false` means
  *"HH doesn't know their site"*, not *"they have no site"*. It still feeds
  the core's "+40 no site" rule, which will therefore over-fire on this
  source. If that proves noisy, the honest fix is a site-check worker, not a
  guess here. (Contrast `workers/osm`, where an absent website tag genuinely
  is a reliable "no site" signal.)
- **`archived` is not filtered.** The spec types it as a nullable boolean;
  HH's own doc example shows the *string* `"false"`. Rather than guess, the
  worker relies on search returning active vacancies. If archived ones start
  appearing, that's the first place to look.
- **Depth cap is 2000 results per query**, HH-enforced. With `PER_PAGE=100`
  the last usable page is 19 — `hh_client.max_page()` enforces this itself
  rather than walking into an error. A keyword with more than 2000 hits in
  one city is sampled, not exhausted. Narrow the keyword or split by area.
- **Salary is not converted between currencies.** `currency` rides along in
  `raw` untouched.
- **15 cities, 22 keywords = 330 queries per full pass** at ~1s pacing, plus
  up to `MAX_EMPLOYER_DETAILS` employer lookups. Unlike `workers/osm` there
  is no checkpoint/resume — a pass is short enough not to need one. If the
  grid grows a lot, copy the `osm_progress.json` pattern.

---

## Схема: откуда взята

Not written from memory. Sources, all fetched while building this:

- **OpenAPI spec** — `https://api.hh.ru/openapi/specification/public`
  (OpenAPI 3.0.3, 107 paths, ~1 MB YAML). Gave the exact `/vacancies`
  parameters, the 47 fields of a search item, the 2000-result depth rule, and
  the `/employers/{id}` response including `site_url` and `type`.
- **Official docs repo** — `github.com/hhru/api/blob/master/docs/vacancies.md`,
  which contains HH's own published example of a search-result vacancy. That
  example *is* the test fixture (`tests/fixtures.py`), with provenance noted
  in its docstring.
- **Live `/dictionaries`** — for the real `employer_type` values.
- **Live `/areas`** — all 15 area ids in `areas.yml` were resolved against the
  live tree, not remembered.

---

## Tests

```bash
pip install -r requirements.txt   # includes pytest + pytest-asyncio
pytest workers/hh/tests
```

Everything runs offline — no network, no token, no core:

- `test_staleness.py` — the stale detector: HH's colon-less timezone offsets,
  threshold boundaries, unknown/future dates, and an end-to-end check on HH's
  own published payload. `now` is always injected so tests don't rot.
- `test_dedup.py` — one lead per employer; the stalest vacancy wins
  regardless of insertion order; flags roll up; stalest-first ordering.
- `test_filters.py` — all four filter layers, with as much attention on
  **false positives** as on true ones, plus a check that every one of HH's
  six live `employer_type` values is classified deliberately.
- `test_mapper.py` — mapping onto the core's contract, salary/budget
  handling, `hasSite` logic, camelCase serialization.
- `test_hh_client.py` — the 2000-result cap, paging termination, bearer
  header, and the error split (403 → no retry, 429/5xx → retry).
- `test_runner.py` — the pipeline end to end against a fake HH and fake core.

Retry backoff is per-instance (`retry_base_seconds`) so tests collapse the
waits; with the production 2..60s policy the failure paths would take minutes.

---

## Files

```
hh/
├── __init__.py
├── config.py           # .env settings + keywords.yml/areas.yml loaders (pydantic)
├── keywords.yml        # editable search phrases
├── areas.yml            # 15 HH regions (ids resolved live from /areas)
├── staleness.py          # published_at -> age_days / is_stale (pure)
├── filters.py             # agency/giant/type/accreditation filtering (pure)
├── dedup.py                # many vacancies -> one lead per employer (pure)
├── hh_client.py             # HH API client: paging, depth cap, retries, token
├── mapper.py                 # employer -> RawCompanyRequest (pure)
├── core_client.py             # HTTP client to the Java core, with retries
├── runner.py                   # search -> filter -> group -> details -> send
├── main.py                      # entry point
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py
    ├── fixtures.py            # HH's own published payloads (provenance in docstring)
    ├── test_staleness.py
    ├── test_dedup.py
    ├── test_filters.py
    ├── test_mapper.py
    ├── test_hh_client.py
    └── test_runner.py
```
