# JobWatch

Monitors a short list of company career pages and alerts you when a new role
matching your keywords appears. Runs on GitHub Actions twice on weekdays;
alerts go out as a tagged Gmail message and an ntfy push per tier.

## Fork and set up

1. **Fork** this repo, then open the fork's **Actions** tab and enable
   workflows (GitHub disables scheduled workflows on forks by default).
2. **Pick your companies and keywords** in `config.yaml`. The list shipped
   here is only an example; see [Adding a company](#adding-a-company).
3. **Add repository secrets** (Settings → Secrets and variables → Actions):

   | Secret | Required | What it is |
   |---|---|---|
   | `JOBWATCH_EMAIL_USER` | yes | Gmail address that sends (and by default receives) alerts |
   | `JOBWATCH_EMAIL_PASSWORD` | yes | A Gmail [app password](https://myaccount.google.com/apppasswords), not your login password |
   | `JOBWATCH_STATE_KEY` | yes | Any long random string; encrypts the saved job history (`openssl rand -hex 32`) |
   | `JOBWATCH_NTFY_TOPIC_TARGET` / `_FAANG` / `_OTHER` | no | [ntfy.sh](https://ntfy.sh) topic names for phone push per tier; pick hard-to-guess names |

   A public fork is fine: secrets never appear in logs, and the job history
   is saved encrypted.
4. **First run**: Actions → *JobWatch - Career Page Monitor* → *Run workflow*
   with `suppress_alerts` checked. It records the current openings without
   emailing all of them; later runs alert only on new postings.

Run locally instead:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && python -m playwright install chromium
cp .env.example .env          # fill in your values; .env is gitignored
python jobwatch.py run --lane fast --dry-run
```

## How it works

1. **Fetch.** Each company in `config.yaml` has an adapter (`ats:`) that
   pulls its job board: Greenhouse, Ashby, Lever, SmartRecruiters, Workday,
   Phenom, Oracle HCM, Netflix, Amazon, Eightfold (via Playwright) and a
   generic Playwright scraper. Sources run in parallel with a per-source
   time limit; a slow board returns what it has instead of nothing.
2. **Filter.** Titles must contain a keyword and must not match an exclusion
   (managers, interns, clearance, pre-sales/support titles). Locations must
   carry a US signal. Postings older than `filters.max_age_hours` are ignored.
3. **Remember.** Every posting is stored in `jobwatch.db`. A posting is
   alerted once; it is kept for `retention.job_days` after it disappears so
   reposts stay quiet. Jobs you mark applied/interview/offer are never deleted.
4. **Rank.** Freshness, keyword strength, preferred company, remote-friendly
   and H-1B sponsor status produce a score and a band: Top / Strong / Watch.
5. **Route.** Each company has a `tier:`. Each tier has its own email tag,
   ntfy topic and `email_bands` (which bands it alerts on). Roles that fail
   the band gate still land in the workflow inbox.

## Configuration (`config.yaml`)

| Key | What it does |
|---|---|
| `paused` | `true` stops fetching and alerting; state is preserved. |
| `keywords` | Global title substrings (case-insensitive). |
| `filters.exclude_new_grad` | Drop new-grad / early-career titles. |
| `filters.max_age_hours` | Ignore postings older than this. |
| `retention.job_days` | How long a vanished posting is remembered. |
| `runner.*` | Worker counts and per-source timeouts per lane. |
| `alerting.email_bands` | Default rank bands that trigger an alert. |
| `companies[]` | `name`, `tier`, `ats`, plus adapter keys (see below). |
| `companies[].keywords` | Optional per-company list that replaces the global one. |
| `companies[].allow_seniority_titles` | Keep VP / Director titles (banks use them for ICs). |
| `notification.tiers.<tier>` | `subject_tag`, `ntfy_priority`, `ntfy_topic_env`, `alert`, `email_bands` (list or `all`). |
| `ranking.preferred_companies` | +25 rank points. |

Adapter keys: `slug` (greenhouse, ashby, lever, smartrecruiters, playwright),
`tenant` + `site` (workday), `tenant` [+ `base_url`] (eightfold_pw),
`domain` (phenom, talentbrew), `host` + `site` (oracle).

### Adding a company

```bash
python discover.py "Company Name"                       # probes Greenhouse/Lever/Ashby/SmartRecruiters
python discover.py "Company Name" https://careers.example.com
```

Paste the printed entry into `companies:`, add a `tier:`, then run
`python -m pytest tests/test_config.py -q` — it rejects duplicates, unknown
adapters, missing keys and unconfigured tiers. Verify live with
`python jobwatch.py run --lane fast --dry-run`.

Some boards cannot be tracked: sites behind Akamai bot walls or CAPTCHA
challenges (Apple, Meta, Microsoft, Tesla, Bloomberg) return nothing to
automation. They are left commented out in the config with a note.

## Running

```bash
python jobwatch.py run --lane all            # fetch, alert, update state
python jobwatch.py run --lane fast --dry-run # fetch + rank only, no side effects
python jobwatch.py health                    # recent per-source results
python jobwatch.py health-digest             # email the weekly health summary
python jobwatch.py search "machine learning"
python jobwatch.py export -o jobs.csv
```

Lanes: `fast` = API adapters, `browser` = Playwright adapters, `all` = both.

Environment (GitHub secrets in CI, `.env` locally): `JOBWATCH_EMAIL_USER`,
`JOBWATCH_EMAIL_PASSWORD` (Gmail app password), `JOBWATCH_NTFY_TOPIC_*` per
tier, `JOBWATCH_STATE_KEY` (encrypts the state artifact).

## CI

`.github/workflows/jobwatch.yml` runs Mon-Fri at 11:23 and 20:23 UTC (GitHub
delays scheduled runs, so they land later) plus a Monday health digest. Each
run: tests, restore the newest encrypted `jobwatch.db`, build/cache the H-1B
sponsor database from DOL LCA data, run JobWatch, encrypt and save the state.

State is never stored in plaintext and is saved twice: as an Actions cache
entry and as an artifact (kept 14 days). Restore takes whichever is newer, so
an artifact-quota failure or a cache eviction alone cannot lose it. If the
restored state is missing or more than 5 days old, the run records roles
without alerting (`JOBWATCH_SUPPRESS_ALERTS=1`) rather than re-sending
everything on the boards. Trigger a run by hand with:

```bash
gh workflow run jobwatch.yml -f lane=fast
gh workflow run jobwatch.yml -f lane=all -f suppress_alerts=true   # record quietly
```

## Tests

```bash
python -m pytest tests/ -q
```

Unit tests cover filters, ranking, routing, store safety, delivery, adapters
(mocked), the health digest and the config file itself. No network access.
