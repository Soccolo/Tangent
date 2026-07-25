# Tangent 🦉

**Learn the ring around your job.**

You already know your job. Tangent looks at what you actually worked on today and
teaches you the subjects sitting just outside it — the neighbouring discipline, the
counterparty's view, the regulation that shapes it, the technique one level past the
one you used.

Price a solicitors' PI claim all day and Tangent offers you limitation periods, the
underwriter's side of the same risk, or gradient boosting on sparse claims data.
You pick one. **Tangent picks the second — deliberately from somewhere else.**
Then it writes both as short interactive lessons: concept cards with diagrams, then
questions with real explanations. Streaks, XP and levels keep you coming back.

## Running it

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Put your Anthropic API key in `.env`, then:

```bash
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000, create an account, and describe your role in a sentence
or two — that description is how Tangent knows what counts as "already known."

### Trying it without an API key

```bash
python seed_demo.py you@example.com
```

Seeds a full day — activity log, six topics, and one hand-written lesson complete with
an SVG diagram — so you can walk the whole player, streak and scoring flow before
spending anything. The account's password is `demodemo` if it didn't already exist.
Tangent's own pick is left ungenerated on purpose: opening it is what exercises the
real Claude path once a key is in place.

## How it works

| Piece | What it does |
| --- | --- |
| `app/llm.py` | Both Claude calls. Structured outputs (`output_config.format`) so lessons come back schema-valid, streaming so large generations don't hit HTTP timeouts. |
| `app/routers/learn.py` | Activity log → daily digest → topic choice → lesson → grading, XP and streaks. |
| `app/models.py` | SQLite via SQLAlchemy. One file, no migrations, portable. |
| `web/` | Hand-rolled SPA — no build step, no framework, no CDN. |

**Lessons are generated on first open, not when you pick a topic.** The digest screen
stays instant and you only pay for lessons somebody actually starts.

### Cost

One digest is roughly 2–4k tokens; one lesson is roughly 6–10k. On Claude Opus 5
($5/$25 per million) that's about **2–5¢ per lesson**. To cut that by ~60%, set
`TANGENT_MODEL=claude-sonnet-5` in `.env` — worth A/B-ing on your own topics before
deciding, since lesson quality *is* the product.

`TANGENT_EFFORT` (`low`/`medium`/`high`/`xhigh`/`max`) is the other lever; it defaults
to `medium`, which is already strong on Opus 5.

### Safety notes

- Model-authored SVG is sanitised server-side (`sanitize_svg`) before it reaches the
  DOM — scripts, event handlers and external references are stripped.
- Lesson prose renders through a whitelist that allows `**bold**` and `*italic*` only;
  everything else is HTML-escaped.
- Passwords are PBKDF2-SHA256 with a per-user salt. Sessions are httpOnly cookies.
- Server-side refusal fallbacks are enabled by default (Opus 5 routes policy declines
  to a fallback model in the same call). If your SDK or account doesn't have the beta,
  the app logs a warning once and continues without it.

## Deploying to Render

`render.yaml` is a Blueprint: it declares the web service, a Postgres database, and
every environment variable except the API key.

1. **Push to GitHub** (Render deploys from a repo):

   ```bash
   git init && git add -A && git commit -m "Tangent v1"
   ```

   ```bash
   gh repo create tangent --private --source=. --push
   ```

2. **In Render:** New → Blueprint → pick the repo. It reads `render.yaml`.

3. **Paste your `ANTHROPIC_API_KEY`** when prompted (it's the one var marked
   `sync: false`, so it never lives in the repo). `TANGENT_SECRET` is generated for
   you — don't set it by hand, and don't change it later or you'll sign everyone out.

4. **Set a spend limit in the Anthropic Console** before you share the URL. The caps
   below are the application's guard; the Console limit is the one that can't be
   defeated by a bug in mine.

Deploys are automatic on push. `/healthz` is the health check.

### What the spend guards actually allow

Signup is open, so these are load-bearing:

| Variable | Default | Meaning |
| --- | --- | --- |
| `TANGENT_DAILY_LESSON_CAP` | 6 | Lessons one account can generate per day |
| `TANGENT_DAILY_DIGEST_CAP` | 4 | Topic-set generations per account per day |
| `TANGENT_GLOBAL_DAILY_CAP` | 300 | Generations across *all* users per day |

Worst case per account is roughly **$0.38/day**; the global cap holds the whole site to
around **$12/day** on Opus 5. Re-tune once you've seen real usage — but keep the global
cap, it's what stops a hundred well-behaved strangers adding up to a bad month.

**Failed generations consume quota deliberately.** Otherwise a prompt that reliably
fails becomes a free infinite retry loop.

### Things worth knowing before you share the link

- **Free-tier services sleep after ~15 minutes idle.** The first request then takes
  30–60s to wake — on top of the 30–60s lesson generation. If testers are strangers,
  pay for the always-on tier or warn them. (Render's plan names and prices change;
  check the dashboard rather than trusting `render.yaml`'s comments.)
- **The schema is created with `create_all()`, not migrations.** Adding a column to
  `models.py` won't alter a live table — Postgres will keep serving the old shape and
  queries will fail. For v1 that means: change the schema, then drop and recreate the
  database. Add Alembic before you have users whose data you'd mind losing.
- **Lesson generation runs as a background task in the web process.** Fine at this
  scale; if it gets busy, that work belongs in a worker with a real queue.

### Deploying somewhere else

It's one process serving both API and frontend, so any Python host works:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set `ANTHROPIC_API_KEY`, `TANGENT_SECRET`, `TANGENT_ENV=production`, and either
`DATABASE_URL` (Postgres) or `TANGENT_DB`. `TANGENT_ENV=production` is what turns on
`Secure` session cookies — set it anywhere you're served over HTTPS.

SQLite is fine for a handful of testers **only if the disk persists**. On most
platforms the container filesystem is wiped every deploy, which silently erases every
account and streak. Use Postgres, or mount a real volume.

## What's deliberately not built yet

- **Automatic activity capture.** v1 is a manual log. The browser-extension and
  desktop-agent versions write to the same `POST /api/activities` endpoint, so they
  bolt on without touching anything else.
- Lesson caching across users (every lesson is generated fresh per person).
- Spaced repetition — the history exists, the review scheduling doesn't.
