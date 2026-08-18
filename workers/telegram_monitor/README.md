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
CORE_URL=http://localhost:8080
LOG_LEVEL=INFO
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

Three categories, each just a weight and a list of phrases - edit freely,
no code changes needed:

- `intent` (weight 3): direct order signals ("нужен сайт", "ищу разработчика", ...).
  A post matches on its own if any of these appear.
- `domain` (weight 2): topic words ("сайт", "бот", "crm", ...). Alone,
  these are just chatter - a domain hit only counts as a lead when paired
  with a `budget` hit.
- `budget` (weight 1): budget markers ("бюджет", "тз", "прайс", ...). Also
  sets `raw.budgetMentioned = true`, which the core's scorer reads directly
  (+20 points - see the core's README).

Matching is whole-word/whole-phrase, case-insensitive, on whitespace-
collapsed text (so it won't, say, mistake "интеграции" for a hit on a
keyword "ии").

### 6. Run it

```bash
python -m workers.telegram_monitor.main
```

Every matched post is logged (`monitor.lead_matched`) along with the core's
response (`core_client.ingested`: created/merged/hotCount) so you can watch
leads flow in real time.

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
| `raw`       | `{ text, category, budgetMentioned, channel, matched_weight, no_direct_contact }` |

`raw.budgetMentioned` is the one field the core's scorer actually consumes
today (+20 points); the rest ride along for visibility/debugging.

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
no network, no Telegram, no core required.

## Files

```
telegram_monitor/
├── __init__.py
├── config.py        # .env settings + channels.yml/keywords.yml loaders (pydantic)
├── keywords.yml      # editable keyword dictionary
├── channels.yml       # editable channel list (placeholders - fill in real ones)
├── matcher.py         # post text -> is this an order request?
├── extractor.py       # post text -> contact info
├── core_client.py     # HTTP client to the Java core, with retries
├── monitor.py          # Telethon wiring: listen, match, extract, send
├── main.py             # entry point
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py
    ├── test_matcher.py
    └── test_extractor.py
```
