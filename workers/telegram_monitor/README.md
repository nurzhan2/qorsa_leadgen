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
```

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

Where to find order-style channels: Telegram's own search, freelance/order
aggregator channels for your city or niche, "нужен сайт"-style tag channels,
etc. This is a manual, human decision (which communities to join) by
design - the worker only reads what's already visible to a member.

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

- `FloodWaitError` from Telegram (rate limiting) is caught around entity
  resolution and message metadata lookups - the worker sleeps for the
  requested duration and continues, instead of crashing.
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

`test_matcher.py` and `test_extractor.py` run entirely on string fixtures -
no network, no Telegram, no core required. `test_channel_rater.py` covers
the order-rate/verdict math directly, plus `rate_channel`/`rate_all` against
a small in-memory fake standing in for the Telethon client (async tests, via
`pytest-asyncio`) - also no real network or Telegram session required.

## Files

```
telegram_monitor/
├── __init__.py
├── config.py           # .env settings + channels.yml/keywords.yml loaders (pydantic)
├── keywords.yml        # editable keyword dictionary
├── channels.yml        # editable channel list (placeholders - fill in real ones)
├── matcher.py           # post text -> is this an order request, or a vacancy in disguise?
├── extractor.py         # post text -> contact info
├── core_client.py       # HTTP client to the Java core, with retries
├── monitor.py            # Telethon wiring: listen, match, extract, send
├── channel_rater.py       # standalone: rate channels.yml entries by history order_rate
├── channel_report.csv     # generated by channel_rater.py - not committed
├── main.py                # entry point (live monitor)
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py
    ├── test_matcher.py
    ├── test_extractor.py
    └── test_channel_rater.py
```
