# telegram_monitor

The first scraper worker for `qorsa_leadgen`: a Telegram *userbot* that
watches the message stream of order-style channels/chats, recognizes posts
like "нужен сайт" / "ищу разработчика" / "требуется бот", extracts whatever
contact the author left in their own post, and forwards the lead to the Java
core over HTTP.

This directory is fully self-contained - its own `requirements.txt`, its own
`.env`, its own README. It does not import anything from, or get imported
by, the Java core; the only connection between them is the HTTP call to
`POST /api/v1/companies/ingest`.

## What this does and does not read

This worker reads the **public message stream** of channels/chats the
account has already joined by hand - i.e. posts where someone voluntarily
announced "I need a website" and (usually) left a way to reach them.

It deliberately does **not**:

- call `get_participants` or anything else that lists channel members/subscribers;
- collect or store a subscriber/member list of any kind;
- read comment threads under a post (Telegram's discussion-group replies);
- join any channel automatically - you decide, by hand, which channels the
  account is a member of, and only those are monitored.

If a post's author left a contact (an `@username`, a `t.me/...` link, a
phone number, an email) *in the post itself*, that's what gets extracted.
If they didn't, the worker falls back to the message's sender username -
still something Telegram already makes public about them as the poster of
that message, not something looked up separately. If neither is available,
the lead is still forwarded (the core can still act on it), just flagged
`raw.no_direct_contact = true`.

## Setup

### 1. Get a Telegram API id/hash

1. Go to <https://my.telegram.org> and log in with the phone number of the
   account you want to run the monitor as.
2. Open **API development tools**.
3. Fill in the short form (app title/short name - anything works, e.g.
   "qorsa-leadgen-monitor" / "leadgen").
4. You'll get an **App api_id** (a number) and an **App api_hash** (a hex
   string). Copy both.

### 2. Configure

```bash
cd workers/telegram_monitor
cp .env.example .env
```

Fill in `.env`:

```
TG_API_ID=123456
TG_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TG_SESSION=leadgen_session
CORE_URL=http://localhost:8081
LOG_LEVEL=INFO

# channel_rater.py only - see "Оценка каналов" below.
RATE_SAMPLE=50
RATE_CHANNEL_DELAY_SECONDS=2.0

# Channel resolution & FloodWait policy - see "Кэш каналов и FloodWait".
RESOLVE_DELAY_SECONDS=2.0
CACHE_TTL_DAYS=30
MAX_FLOOD_WAIT_SECONDS=300
FORCE_RESOLVE=false
```

| variable | default | what it does |
|---|---|---|
| `RESOLVE_DELAY_SECONDS` | `2.0` | pause between username resolves (only for channels the cache can't answer for) |
| `CACHE_TTL_DAYS` | `30` | how long a cached resolve stays valid before being re-checked |
| `MAX_FLOOD_WAIT_SECONDS` | `300` | if Telegram asks for a longer wait than this, stop resolving this run and keep what we have |
| `FORCE_RESOLVE` | `false` | re-resolve everything, ignoring the cache - set after joining new channels |

> **Windows: не сохраняй `.env` с BOM.** Notepad and PowerShell's
> `Out-File`/`Set-Content` write a UTF-8 **BOM** by default. A BOM at the start
> of `.env` becomes part of the **first variable's name** - `TG_API_ID` is read
> as `﻿TG_API_ID` - so that one variable silently goes missing and the
> worker dies at startup with
> `ValidationError: tg_api_id Field required`, which looks like "I didn't fill
> it in" even though you did. All six workers now read `.env` as `utf-8-sig`,
> which tolerates the BOM, so this can't bite any more - but if you're editing
> by hand, save as "UTF-8" rather than "UTF-8 with BOM" anyway. (In PowerShell:
> `Set-Content -Encoding utf8NoBOM`, or just use an editor like VS Code.)

Install dependencies (a virtualenv is recommended):

```bash
python -m venv .venv
.venv/Scripts/activate   # or `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
```

### 3. First run: logging in

```bash
# from the repo root, with the Java core already running
python -m workers.telegram_monitor.main
```

The first time you run it, Telethon will ask for the phone number of the
account and then the login code Telegram sends you. After that it creates a
**`leadgen_session.session`** file (name comes from `TG_SESSION`) next to
this README - that file *is* your logged-in session, equivalent to a
password. **Never commit it.** It's already covered by the repo's
`.gitignore` (`*.session`), along with your `.env`.

### 4. Fill in `channels.yml`

The account must already be a **member** of every channel/chat you list here
- this worker never joins anything on its own. Replace the placeholder
entries with real ones:

```yaml
channels:
  - username: "some_order_channel"      # public channel, @ optional
  - username: "another_order_channel"
  # - id: -1001234567890                # private chat: use its numeric id
```

Entries may also carry `title`/`priority`/`category` for your own bookkeeping;
the loader ignores unknown keys, so annotate freely.

Where to find order-style channels: Telegram's own search, freelance/order
aggregator channels for your city or niche, "нужен сайт"-style tag channels,
etc. This is a manual, human decision (which communities to join) by
design - the worker only reads what's already visible to a member.

> **Если русские `title` выглядят кракозябрами** - это почти наверняка твой
> просмотрщик, а не файл. `channels.yml` is stored as plain UTF-8 (no BOM),
> and `config.py` reads it with an explicit `encoding="utf-8"`, so the worker
> is unaffected by the host's code page. A Russian-locale Windows console
> defaults to CP866/CP1251 and will render UTF-8 Cyrillic as `????`/`Ð...`
> garbage on `type`/`cat`. Check with `python -X utf8 -c "print(open(r'channels.yml',encoding='utf-8').read()[:400])"`,
> or open it in VS Code - if it reads correctly there, the file is fine. Don't
> "repair" it by re-saving from a mis-decoding editor; that's what would
> actually corrupt it.

You do **not** have to be a member of everything you list. Channels this
account isn't in are detected once, remembered, and skipped from then on -
see "Кэш каналов" below.

### 5. Tune `keywords.yml`

Five sections, all plain YAML lists - edit freely, no code changes needed.
`intent`/`domain`/`budget` also carry a `weight:` (used only for lead
priority scoring); `anti_hiring`/`order_signals` are unweighted, used only
for the accept/reject decision described below.

- `intent` (weight 3): direct order signals ("нужен сайт", "ищу разработчика", ...).
  A post matches on its own if any of these appear.
- `domain` (weight 2): topic words ("сайт", "бот", "crm", ...). Alone,
  these are just chatter - a domain hit only counts as a lead when paired
  with an `order_signals` hit.
- `budget` (weight 1): budget markers ("бюджет", "тз", "прайс", ...). Feeds
  scoring only: sets `raw.budgetMentioned = true`, which the core's scorer
  reads directly (+20 points - see the core's README).
- `anti_hiring`: hiring/vacancy markers ("в штат", "оклад", "график работы",
  "трудоустройство", ...). A post that *would* have matched gets **rejected**
  (treated as a vacancy, never sent to the core) if one of these fires and
  no `order_signals` keyword also fires.
- `order_signals`: one-off-order markers ("разовая задача", "под ключ", "по
  тз", "напишите в лс", ...). These **outweigh** `anti_hiring` - a post that
  hits both ("в штат" *and* "разовая задача") is kept, not rejected, since
  that combination reads more like a poorly-worded order than a real vacancy.
  Also usable on their own: `domain` + `order_signals` is enough to match
  even without an `intent` phrase.

Matching is whole-word/whole-phrase, case-insensitive, on whitespace-
collapsed text (so it won't, say, mistake "интеграции" for a hit on a
keyword "ии").

### Баланс: лучше лишний лид

The matcher is deliberately biased toward **catching too much rather than
too little**:

- `intent` alone (with zero anti-hiring signal) is enough to match - no
  second signal required.
- A single `order_signals` hit is enough to save a post from anti-hiring
  rejection even when several hiring markers are present.
- Anti-hiring rejection only fires when nothing else says "this is really
  an order" - it's a targeted filter for the obvious case (pure job-board
  noise), not a strict gate.

The reasoning: a manager skimming the lead list can dismiss a stray vacancy
post in a second. A missed order is gone for good - the poster moves on and
finds someone else. So when in doubt, the matcher lets it through and
leaves the final call to a human. If a specific channel turns out to be
mostly vacancy noise despite this, that's what `channel_rater.py` (below)
is for - drop the channel, not the keyword balance.

### 6. Run it

```bash
python -m workers.telegram_monitor.main
```

Every matched order is logged (`monitor.lead_matched`) along with the core's
response (`core_client.ingested`: created/merged/hotCount) so you can watch
leads flow in real time. Posts that got rejected as vacancies are logged at
DEBUG level only (`monitor.post_rejected`) and never sent to the core - set
`LOG_LEVEL=DEBUG` in `.env` if you want to see (and tune against) what's
being filtered out.

On startup it prints a one-line summary of what it's actually watching:

```
Мониторю 56 каналов | пропущено: 28 не подписан, 14 не найдено | из кэша: 98, резолв сейчас: 0
```

## Кэш каналов и FloodWait

### Зачем

Telegram doesn't hand out a channel's numeric id for free: turning
`@fordev` into something the client can listen to costs a
`ResolveUsernameRequest`. With 98 channels in `channels.yml`, a worker that
resolves on every start makes **98 of those calls in a burst, every single
restart** - and Telegram answers that the way it's supposed to, with an
escalating `FloodWait`: 3s, then 51s, then longer.

A channel's id and `access_hash` don't change. So the fix isn't to get around
the limit - it's to **stop making the calls**: resolve once, write the answer
to `channel_cache.json`, and read it from there forever after.

| | resolve calls | dialog calls |
|---|---|---|
| first run (cold cache) | 98 | 1 |
| **every restart after that** | **0** | **0** |
| after `FORCE_RESOLVE=true` | 98 | 1 |

The membership check ("am I actually subscribed?") is one `get_dialogs`
request that answers for all 98 channels at once, rather than a per-channel
probe.

### Честно про FloodWait

**`FloodWait` is Telegram working correctly, not an obstacle.** It is the
server saying "you're going too fast, wait N seconds". This worker's entire
strategy is to *stop needing to ask*, and when it does get a `FloodWait`, to
**wait exactly as long as it was told**:

- It logs the wait (`resolver.flood_wait`) and sleeps that long. It never
  shortens, skips, or retries through a wait.
- If the wait exceeds `MAX_FLOOD_WAIT_SECONDS` (default 300), it **stops
  resolving for that run**, keeps whatever it already resolved, and monitors
  those channels. The remainder is picked up on a later run. This is a
  ceiling on how long the worker blocks - not a way around the limit.
- Two `FloodWait`s in a row means the account is genuinely throttled, so it
  stops rather than poking harder.

There is deliberately **no proxy rotation, no second session, no parallel
resolving, and no ignoring of pauses**. Those would be attempts to evade a
rate limit, which is exactly what Telegram's terms prohibit. The only lever
pulled here is the legitimate one: making far fewer requests.

If you're still getting `FloodWait` on a first run with a cold cache, raise
`RESOLVE_DELAY_SECONDS`, or just run it twice - the second run resumes from
the cache and only resolves what's left.

### Где лежит и что внутри

`workers/telegram_monitor/channel_cache.json` (gitignored - it's
machine-local state tied to one account's session, not source):

```json
{
  "fordev": {
    "id": -1001234567890,
    "access_hash": 7712345678901234567,
    "title": "Вакансии Backend/Frontend (веб)",
    "resolved_at": "2026-08-29T17:29:11.482913+00:00",
    "status": "ok"
  }
}
```

`status` is one of:

| status | meaning | what to do |
|---|---|---|
| `ok` | resolved, and this account is a member | monitored |
| `not_joined` | the channel exists, but you're not in it | join it, or drop the line from `channels.yml` |
| `not_found` | no such username - renamed or deleted | drop the line from `channels.yml` |

**Negative results are cached on purpose.** Without that, every dead or
unsubscribed channel would burn a resolve call on every start forever - which
is a large part of what caused the original problem.

Entries older than `CACHE_TTL_DAYS` (default 30) get re-resolved on their
own, so renames and channels you joined later do eventually get picked up
without any manual step.

### Как сбросить

Two equivalent ways, both fine:

```bash
# 1) one-off: re-resolve everything on the next run
FORCE_RESOLVE=true python -m workers.telegram_monitor.main
```

```bash
# 2) permanent: just delete the file
rm workers/telegram_monitor/channel_cache.json
```

**Вступил в новые каналы → нужен `FORCE_RESOLVE`.** A channel cached as
`not_joined` is trusted until its TTL expires, so joining it in the Telegram
app won't be noticed until you either force a re-resolve or wait out
`CACHE_TTL_DAYS`. Set `FORCE_RESOLVE=true`, run once, then set it back to
`false` - leaving it on permanently re-creates the exact FloodWait problem
this cache exists to solve.

### Какие каналы мёртвые: `--report`

```bash
python -m workers.telegram_monitor.channel_cache --report
```

Reads the cache offline - no Telegram connection, no session needed - and
prints every channel worst-status-first, so the lines worth deleting from
`channels.yml` are at the top:

```
username     status      resolved_at          age    title
------------------------------------------------------------------
deadchan     not_found   2026-08-29T17:29:42  today
lurking      not_joined  2026-08-29T17:29:42  today  Не подписан
fordev       ok          2026-08-29T17:29:42  today  Вакансии Backend

Total 3 cached: 1 ok, 1 not_joined, 1 not_found
```

Use it to prune `channels.yml`: delete the `not_found` rows outright, and for
`not_joined` decide per channel whether to join it or drop it. Pair it with
`channel_rater.py` below, which judges the channels you *are* in on whether
they actually produce orders.

## Оценка каналов (`channel_rater.py`)

A standalone diagnostic tool, separate from the live monitor, that answers
"is this channel in `channels.yml` actually worth watching?" by reading its
**recent history** (not the live stream) and running every post through the
same matcher.

```bash
# from the repo root, using the same TG_API_ID/TG_API_HASH/TG_SESSION as
# the live monitor - it logs in the same way, no separate setup needed
python -m workers.telegram_monitor.channel_rater
```

For each channel in `channels.yml` it reads the last `RATE_SAMPLE` posts
(`.env`, default 50), classifies each as an order / a hiring post / noise,
and prints a ranked table plus writes `channel_report.csv` next to this
README, with columns:

| Column | Meaning |
|---|---|
| `username` | as configured in `channels.yml` |
| `подписан` | `да`/`нет` - `нет` means the account isn't a member, or the channel is otherwise unreachable (deleted, wrong username, access error) |
| `total` | posts sampled |
| `orders` | matched as a real order (`matched=true`) |
| `hiring` | rejected as a vacancy post |
| `noise` | didn't match anything |
| `order_rate%` | `orders / total` |
| `вердикт` | see below |

Verdict thresholds (on `order_rate`):

- **`ДЕРЖАТЬ`** (>= 15%) - clearly worth keeping.
- **`СЛАБО`** (5-15%) - marginal, keep an eye on it.
- **`ВЫКИНУТЬ`** (< 5%) - mostly noise/vacancies, consider dropping from `channels.yml`.
- **`НЕДОСТУПЕН`** - the account isn't subscribed, or zero posts could be
  read at all; there wasn't enough signal to judge the channel one way or
  the other (distinct from `ВЫКИНУТЬ`, which means "we checked, it's weak").

**Using it to clean up `channels.yml`**: run the rater, open
`channel_report.csv`, sort by `order_rate%`, and remove the `ВЫКИНУТЬ` (and
long-standing `НЕДОСТУПЕН`) entries from `channels.yml`. Re-run occasionally
as channels change character over time - this is a manual, human decision,
the same way choosing which channels to join in the first place is.

It handles `FloodWaitError` and per-channel access errors the same way the
live monitor does: log, skip that one channel (marked `НЕДОСТУПЕН`), and
keep going rather than aborting the whole run; it also pauses
`RATE_CHANNEL_DELAY_SECONDS` (`.env`, default 2s) between channels to stay
polite to Telegram's rate limits.

## What gets sent to the core

One `RawCompanyRequest` per matched post, POSTed as a single-element batch
to `/api/v1/companies/ingest` (see the core's own README for the full JSON
contract):

| Field       | Value |
|-------------|-------|
| `name`      | `"Заявка из @channelname"` (or the chat title/id if it has no public username) |
| `messenger` / `phone` / `email` | Whatever `extractor.py` found (post text first, sender username as fallback) |
| `city`      | always `null` - Telegram doesn't tell us this |
| `source`    | always `"TELEGRAM_ORDER"` |
| `sourceUrl` | link to the exact post (`t.me/channel/msg_id`, or `t.me/c/...` for private chats) |
| `hasSite`   | always `true` - we have no idea, and `false` would wrongly trigger the core's "+40 no site" rule |
| `raw`       | `{ text, category, budgetMentioned, channel, matched_weight, no_direct_contact, is_order, matched_order }` |

`raw.budgetMentioned` is the one field the core's scorer actually consumes
today (+20 points); the rest ride along for visibility/debugging. Every lead
that reaches the core is by definition an order (vacancies are rejected
before this point, see above) - `raw.is_order` is always `true` today, kept
explicit for downstream clarity and in case the match logic ever grows a
matched-but-not-an-order case. `raw.matched_order` lists which
`order_signals` keywords fired on the post, if any.

## Duplicate handling

Two independent layers:

- **This worker**, in-memory, per run: won't send the same `(channel, message_id)`
  twice, and won't send a second lead for a contact it already sent this
  session.
- **The core**, permanently: dedups by domain/phone/fuzzy-name across
  *all* sources, so even a restart re-scanning recent messages is harmless.

## Resilience

- **Channel resolution is cached** (`channel_cache.json`), so a restart costs
  zero `ResolveUsername` calls - see "Кэш каналов и FloodWait" above. This is
  the difference between a clean start and an escalating rate limit.
- `FloodWaitError` from Telegram is **obeyed, never worked around**: the
  worker logs how long it was told to wait, waits exactly that long, and
  continues. Beyond `MAX_FLOOD_WAIT_SECONDS` it stops resolving for the run
  and keeps what it already has, rather than blocking indefinitely.
- The same handling wraps message metadata lookups during live monitoring.
- `core_client.py` retries network errors and 5xx responses up to 3 times
  with exponential backoff (1s, 2s); 4xx responses (bad payload) are not
  retried.
- A small delay after each send avoids bursting the core when several
  matching posts arrive close together.

## Tests

```bash
pip install -r requirements.txt   # includes pytest
pytest workers/telegram_monitor/tests
# or, from inside this directory:
cd workers/telegram_monitor && pytest
```

Everything runs offline - no network, no Telegram session, no core:

- `test_matcher.py` / `test_extractor.py` - plain string fixtures.
- `test_channel_cache.py` - JSON round-trip across a simulated restart, TTL
  and expiry, corrupt/missing files, and the `--report` output.
- `test_resolver.py` - the rate-limit behaviour, asserted directly rather
  than assumed: that cached channels produce **zero** `get_entity` calls,
  that a `FloodWait` is **slept for its full duration**, that an
  over-ceiling wait stops the pass while keeping (and persisting) prior
  progress, and that `not_joined`/`not_found` channels are skipped without
  being re-resolved. The fake client is driven with real Telethon
  `Channel` objects and real `FloodWaitError`s.
- `test_channel_rater.py` - order-rate/verdict math, plus `rate_channel`
  reading history from an already-resolved peer (its fake client has no
  `get_entity` at all, so a regression back to per-run resolving would fail
  the test).

## Files

```
telegram_monitor/
├── __init__.py
├── config.py            # .env settings + channels.yml/keywords.yml loaders (pydantic)
├── keywords.yml         # editable keyword dictionary
├── channels.yml         # editable channel list (placeholders - fill in real ones)
├── matcher.py            # post text -> is this an order request, or a vacancy in disguise?
├── extractor.py          # post text -> contact info
├── channel_cache.py      # resolved-channel cache (pure JSON/TTL) + `--report` CLI
├── channel_cache.json    # the cache itself - machine-local, not committed
├── resolver.py            # channels.yml -> listenable peers, cache-first, FloodWait-respecting
├── core_client.py         # HTTP client to the Java core, with retries
├── monitor.py              # Telethon wiring: listen, match, extract, send
├── channel_rater.py         # standalone: rate channels.yml entries by history order_rate
├── channel_report.csv       # generated by channel_rater.py - not committed
├── main.py                  # entry point (live monitor)
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py
    ├── test_matcher.py
    ├── test_extractor.py
    ├── test_channel_cache.py
    ├── test_resolver.py
    └── test_channel_rater.py
```
