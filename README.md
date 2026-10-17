# netreach

Pull your LinkedIn network into a local database, search, tag and take notes on it in
your browser, and have Claude draft a personalized message for each person, which you
review and send yourself.

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
**Connections**. LinkedIn emails you when it's ready. Picking just Connections is usually
quick, but the full archive can take up to 24 hours. Both the `Connections.csv` file
and the full ZIP work:

```bash
netreach import-csv ~/Downloads/Connections.csv
```

Don't want to wait? Run `netreach sync` now and import the export whenever it arrives.
The two merge, so the export simply fills in company, position and shared emails,
which the live sync doesn't get.

## Usage

```bash
# 1. Pull your network in (either or both)
netreach import-csv ~/Downloads/Connections.csv
netreach sync                    # live, read-only
netreach sync --enrich 25        # ...and check 25 people for a shared email/phone

# 2. Look around (in your browser, or in the terminal)
netreach web
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

### The web page

`netreach web` opens a page in your browser at http://127.0.0.1:8765. On it you can:

- **Search** names, headlines, companies, positions, locations and your notes, then
  narrow with filters (title, company, location, connection date, shared email).
- **Tag** people. Tick rows, or tick the header box and then "Select all N matching"
  to tag a whole search at once. Click a tag in the sidebar to filter by it, or use
  **Edit** to rename (renaming onto an existing tag merges them) or delete one.
- **Take notes.** Click anyone to open their details. Notes save as you type and are
  included in search.
- **See outreach** for each person, and **export** the current view to CSV, with tags
  and notes included.
- **Run campaigns** on the **Campaigns** tab (below).

### Campaigns on the web page

1. Filter by a tag on the People tab and click **Write messages to "…"**, or go to
   **Campaigns → + New campaign**.
2. Name the campaign, choose who to write to (a tag, optionally narrowed by title) and
   how many people this time, and write the brief. The page shows how many people
   that is and a rough cost before you start.
3. Claude writes the drafts in the background. They're saved as they arrive, and you
   can stop at any time.
4. Work through **To send**, best fits first. Edit any draft (changes save as you
   type), click **Copy & open LinkedIn**, paste it into a message there, send it, then
   click **Mark sent**. Later, use **Got a reply** on the **Sent** tab.
5. **Write more drafts** continues the same campaign with people who aren't in it yet,
   so failed drafts get retried and nobody is written to twice.

The sidebar counts what you've marked sent today. About 20–30 a day is a safe pace.

Writing drafts needs `ANTHROPIC_API_KEY` in your `.env` file. If you add it while
`netreach web` is running, stop it (Control+C) and start it again.

Tags work in the terminal too: `--tag "warm lead,investor"` (any of) or `--untagged`
on `list`, `export` and `draft`. For example, to have Claude draft only for people
you tagged:

```bash
netreach draft q1-followup --brief my-brief.md --tag "warm lead"
```

The page only listens on your own computer (127.0.0.1), refuses requests addressed to
any other host name, and only accepts changes from itself, so other websites open in
your browser can't read or change your data. Press Ctrl+C in the terminal to stop it.
Use `--port` if 8765 is taken, or `--no-browser` to skip opening a tab.

### Filters

`list`, `draft` and `export` take the same filters. Commas mean "any of".

| Flag | Matches |
|---|---|
| `--keyword` | name, headline, company, position, location or your notes |
| `--title` | position or headline |
| `--company` | company or headline |
| `--location` | location (filled by the live sync's search fallback) |
| `--connected-after` / `--connected-before` | connection date, `YYYY-MM-DD` |
| `--has-email` | only people who shared an email with their connections |
| `--tag` / `--untagged` | has any of these tags / has no tags yet |
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
