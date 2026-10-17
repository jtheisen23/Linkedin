# netreach

Pull your LinkedIn network into a local database, slice it into segments, and have
Claude draft a personalized message for each person, which you review and send yourself.

There are two ways to get your connections in, and you can use both. Records for the
same person are merged by their profile URL.

| Source | Command | What you get | Risk to your account |
|---|---|---|---|
| LinkedIn's data export | `netreach import-csv` | name, profile URL, company, position, connection date, email (if they share it) | none |
| Live sync (unofficial) | `netreach sync` | name, profile URL, headline, connection date; optional shared email/phone | see below |

## Read this first: the live sync is unofficial

`netreach sync` uses the open-source [`linkedin-api`](https://github.com/tomquirk/linkedin-api)
library. It reuses your logged-in browser session to call LinkedIn's internal web API.
LinkedIn doesn't allow this (User Agreement §8.2), and accounts that make lots of
automated requests get restricted. The risk falls on **your** account. To keep it low,
the sync:

- **only reads.** It never sends messages, invites or anything else, and you send
  every message yourself.
- **paces itself:** 2–5 s inside the library plus `--delay` (default 3 s) with random
  jitter on top, per request.
- **stops on the first sign of trouble** (logged out, security check, HTTP 429)
  instead of retrying.
- **caps each run** with `--max-pages` (default 100 pages × 40 = 4,000 connections).

Good habits:

- Run it **on your own computer and home internet**, not a server, VPN or cloud
  machine. A login from a new location is the fastest way to trigger a security check.
- Set `LINKEDIN_USER_AGENT` to your real browser's user agent.
- Sync occasionally (weekly or monthly), not on a loop. Keep `--enrich` small
  (e.g. 25–50 a day).
- If LinkedIn ever shows a security check, use `import-csv` for a while instead.

LinkedIn changes its internal API without notice. If `sync` stops working, the CSV
export always works.

## Setup

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

### Get your session cookies

1. Log in to linkedin.com in your normal browser.
2. Open DevTools (F12). Go to **Application** (Chrome/Edge) or **Storage** (Firefox),
   then **Cookies → https://www.linkedin.com**.
3. Copy the values of `li_at` and `JSESSIONID` into `.env`.
4. Optional but recommended: in the DevTools **Console**, run `navigator.userAgent`
   and paste the result into `LINKEDIN_USER_AGENT`.

Treat `li_at` like a password: anyone who has it is logged in as you. `.env` is
gitignored. Logging out of LinkedIn in that browser invalidates the cookie, so copy
fresh ones when that happens.

### Or use the export (no cookies needed)

LinkedIn → **Me → Settings & Privacy → Data privacy → Get a copy of your data** → pick
**Connections**. You'll get an email when it's ready, usually within minutes. Both the
`Connections.csv` file and the full ZIP work:

```bash
netreach import-csv ~/Downloads/Connections.csv
```

## Usage

```bash
# 1. Pull your network in (either or both)
netreach import-csv ~/Downloads/Connections.csv
netreach sync                    # live, read-only
netreach sync --enrich 25        # ...and check 25 people for a shared email/phone

# 2. Look around
netreach stats
netreach list --title "founder,ceo,owner" --connected-after 2023-01-01
netreach list --company acme --has-email

# 3. Have Claude draft a campaign for a segment (preview a few first)
netreach draft spring-launch --brief my-brief.md --title "founder,ceo" --limit 5 --dry-run
netreach draft spring-launch --brief my-brief.md --title "founder,ceo"
#    ...or fill in a fixed template instead (free, no API key)
netreach draft spring-launch --template templates/example.txt --title "founder,ceo"

# 4. Work the queue by hand: open each profile, paste the message, send
netreach queue spring-launch
netreach mark 12 13 14 --status sent
netreach mark 13 --status replied

# 5. Take a segment elsewhere (CRM, email tool, ad audiences)
netreach export founders.csv --title "founder,ceo"
```

### Filters

`list`, `draft` and `export` take the same filters. Commas mean "any of".

| Flag | Matches |
|---|---|
| `--keyword` | name, headline, company, position or location |
| `--title` | position or headline |
| `--company` | company or headline |
| `--location` | location (filled by the live sync's search fallback) |
| `--connected-after` / `--connected-before` | connection date, `YYYY-MM-DD` |
| `--has-email` | only people who shared an email with their connections |
| `--limit` | at most N contacts |

### Claude-written drafts

With `--brief`, Claude writes each message from scratch using the person's role,
company, headline, location and how long you've been connected. It also rates how well
your offer fits them (`high`, `medium` or `low`) with a one-line reason. The queue
lists the best fits first.

1. Get an API key at [console.anthropic.com](https://console.anthropic.com) and add
   `ANTHROPIC_API_KEY=...` to `.env` (or run `ant auth login`).
2. Copy `templates/brief-example.md` and rewrite it for your campaign: who you are,
   what you're offering, who it's for (and who it isn't), the ask, and your tone.
   `<!-- comments -->` in the file are not sent.
3. Preview a handful with `--limit 5 --dry-run`. Adjust the brief until they sound like
   you, then run without `--dry-run`.

Things to know:

- **You review everything.** Claude only drafts; nothing is sent. It's told not to
  invent facts about people, but read each message before you send it.
- **What's sent to Anthropic:** each contact's name, headline, company, position,
  location and connection date, plus your brief. Emails and phone numbers are never sent.
- **Cost:** the default model is `claude-opus-5-5` ($4 / $20 per million input / output
  tokens). A draft is under 1,000 input tokens plus the message and Claude's thinking,
  so expect roughly 1–4 cents per draft at the default `--effort medium`. Use
  `--effort low` to make it cheaper, or `--model` for a different model. Check
  actual spend in the Console after a small run.
- **Reruns are cheap:** people already in the campaign are skipped *before* any API
  call, so rerunning the same command retries only the ones that failed.
- **Refusals:** requests opt in to the API's server-side fallback, so if a safety
  check declines one, the API retries it on Anthropic's recommended fallback model.
  If that also fails, that contact is skipped.
- `--workers` (default 4) sets how many drafts are written at once. Lower it if you
  hit rate limits.

### Templates

Placeholders are `{first_name}`, `{last_name}`, `{full_name}`, `{company}`,
`{position}`, `{headline}` and `{location}`. Add a fallback for blanks with `|`:
`{company|your team}`. A misspelled placeholder is an error, not a silent blank.
`draft` never adds the same person to the same campaign twice.

## Being a good sender

- **LinkedIn messages:** send them yourself, a few dozen a day at most, and make
  them personal. Mass-identical messages get reported, and reports restrict accounts too.
- **Email:** only use addresses people chose to share. In the US, CAN-SPAM requires
  an unsubscribe option and your physical address. For contacts in the EU/UK, GDPR
  requires a lawful basis for marketing email.
- Your database is other people's personal data. It lives in `data/` (gitignored), so
  keep it off shared drives.

## Development

```bash
pytest
```

The tests use a fake LinkedIn client and never touch the network.
