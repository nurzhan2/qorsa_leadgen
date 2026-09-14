# workers/site_audit

Measures a company's website and reports the result to the core, which turns
it into lead score.

This worker exists because of a hole in the scoring model. An OSM company
that owns a site could collect at most 25 points: `нет сайта +40` doesn't
apply to it, OSM carries no direct intent, and the two rules written for
exactly this case — `pagespeed` and `auditFails` — sat in `ScoringService`
as commented-out no-ops with no data source. 185 of 379 OSM companies were
permanently cold regardless of how bad their sites were. That is the whole
"платит за рекламу × кривой лендинг" thesis, and it did not work.

## Two signals

| signal | source | needs a key | weight |
|---|---|---|---|
| `pagespeed` | PageSpeed Insights (Lighthouse) | yes | +30 when below 50 |
| `auditFails` | local probe | no | +20 when above 2 |

They are independent. With no key the worker still runs and reports
`auditFails`; with a key it reports both.

**Be clear-eyed about which one carries the weight.** The local probe
catches only badly neglected sites — measured live, real small-business
sites score 1–3 failures, so the `> 2` threshold fires on a minority.
`pagespeed` is the load-bearing signal, and it needs the key.

## PageSpeed Insights

PSI is **not** the Places API. It is free, 25 000 requests/day, and does
**not** require an activated billing account — the `Your free trial requires
a prepayment` wall that blocks Places does not apply here.

1. Google Cloud console → enable **PageSpeed Insights API**
2. Create an API key
3. `PSI_API_KEY=...` in `workers/site_audit/.env`

Without a key PSI does answer anonymous callers, but rate-limits them
immediately — verified: 429 on the very first request. So an empty key is
treated as "PSI disabled" rather than pretending it will work.

`PSI_STRATEGY=mobile` is the default on purpose: these leads get most of
their traffic from a phone, and mobile scores are far harsher than desktop
ones. `PSI_MAX_PER_RUN` caps how many Lighthouse runs one pass may trigger —
PSI is slow (seconds per URL), and a scheduled job must not stall for hours.

## The local probe

One request per site — the homepage, followed through redirects. This is a
scoring signal, not a crawl.

| check | fires when |
|---|---|
| нет https / битый сертификат | TLS fails or only http answers |
| http не редиректит на https | the final URL is still plain http |
| сайт не открывается | neither scheme answers, or HTTP ≥ 400 |
| медленный отклик | over `SLOW_RESPONSE_SECONDS` |
| тяжёлая страница | over `HEAVY_PAGE_BYTES` |
| нет мобильной вёрстки | no `meta[name=viewport]` |
| нет title / meta description / h1 | absent or empty |
| смешанный контент | https page pulling http assets |
| нет favicon | no `link[rel*=icon]` |
| нет Open Graph | no `og:title` — a bare link when shared |
| картинки без alt | over half the images, and at least 4 of them |
| табличная вёрстка | a table nested inside a table |
| пустая страница | under 500 characters of body text |

Two deliberate asymmetries:

- **A good site is never penalised.** A fast, clean site adds zero, not a
  negative. A company whose site is fine simply isn't a lead for a studio
  that rebuilds sites; that's the honest answer, not a penalty.
- **An unreachable site reports no `auditFails` count.** Sending 1 there
  would be a lie in the direction that matters — the rule reads `auditFails`
  as "this many things are wrong with the site", and a dead domain has no
  site to be wrong with. It is recorded as attempted, with a note, and
  scores nothing.

## Run it

```powershell
pip install -r workers/site_audit/requirements.txt
Copy-Item workers/site_audit/.env.example workers/site_audit/.env
python -m workers.site_audit.main
```

Core must be up on `CORE_URL` first.

## One attempt per company

`raw.audit_attempted=true` is written on **every** call, including one
reporting a dead domain, so `pending-audit` never hands the same site out
twice. To re-measure after a site has been rebuilt, clear the flag:

```sql
update companies set raw = raw - 'audit_attempted' where domain = 'example.ru';
```

## Files

```
config.py       # .env settings (pydantic)
core_client.py  # the only code that talks to the core
psi_client.py   # PageSpeed Insights
checks.py       # the local probe — pure functions over one HTTP response
runner.py       # batching, concurrency, PSI budget
main.py         # entry point
```
