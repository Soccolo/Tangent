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

## Screen capture

Press **Record while you work** and Tangent watches your screen, writes your log for
you, and asks you to confirm each line before it counts.

**No video is ever recorded.** The display stream is sampled into a canvas every 20
seconds; each frame is downscaled to 1024px, compared against the last kept one, and
dropped if the screen hasn't meaningfully changed. Kept frames are batched (max 6) and
sent for extraction every five minutes, then discarded — before the network call
returns, so they're never resent. Nothing is written to disk, nothing is stored
server-side, and closing the tab loses everything in flight. There is no video file to
delete because one is never created.

**Nothing reaches your log without your yes.** Extraction produces *proposals*, shown
with the model's confidence and what it thought it saw. Each is an editable text box:
correct it, accept it, or bin it. "Discard all of these" throws away everything
outstanding.

### What this sends to Anthropic, and what to think about first

Frames of your screen go to the Claude API. On a working machine those frames contain
**other people's personal data** — claimant names, case numbers, medical and financial
detail — belonging to people who never agreed to it. That's a real consideration under
UK GDPR, and it doesn't go away because the output is only a one-line summary.

What the app does about it:

- The extraction prompt forbids reproducing any name, identifier, number, address or
  medical or financial detail, and tells the model to return nothing at all rather than
  describe something personal. **This is a mitigation, not a guarantee** — it's an
  instruction to a model, not an enforced filter.
- Only derived text is stored. Frames are never persisted anywhere.
- You confirm every line, so nothing sensitive enters the log without you seeing it.

What you should do:

- **Share one window, not your whole screen.** The browser's picker lets you choose;
  sharing just the tool you're working in dramatically narrows what's captured.
- **Don't record while client-identifying material is on screen.** Stop is one click,
  and the browser's own sharing bar stops it too.
- If this is ever more than personal use, that's a DPIA conversation and an Anthropic
  Zero Data Retention discussion before anyone else's screen is involved.

### Cost

Images are the expensive input. Defaults are tuned for it: a 20s sample interval, a
1024px downscale, change-detection that skips an unchanged screen entirely, and at most
six frames per five-minute call. **An idle screen costs nothing** — verified: an
unchanged screen increments the skip counter and sends no frame.

`TANGENT_VISION_MODEL` is the biggest lever in the app. It defaults to the main model,
which is *not* the cheap choice: extraction is a "name the task" job that far smaller
models do well, and it runs many times an hour. Watch the `generation ok … in=… out=…`
log lines for a real session before deciding — dropping this to a smaller model is the
single highest-value change if capture becomes your main way of logging.

`TANGENT_DAILY_CAPTURE_CAP` (default 60) bounds extraction calls per user per day —
roughly five hours of continuous recording.

## Sharing

Any finished lesson can be shared from the finish screen or the Progress list. That
mints an unguessable token at `/s/<token>`:

- **Anyone with the link can read it, signed in or not** — that's what makes it
  shareable, and it's worth knowing before you paste one into a public channel. Sharing
  is opt-in per lesson and revocable (`DELETE /api/lessons/{id}/share`).
- A signed-in recipient can **add it to their own Tangent**, which copies the lesson so
  they can answer the questions and earn XP. The copy credits the original author.
- **Adding a shared lesson costs nothing** — no generation, so no quota is claimed. The
  second reader of a lesson is free, which makes sharing the cheapest way to grow use.

## Profile

Display name, bio, "what do you do", an accent colour, and a photo. Avatars are
centre-cropped and resized to 256px **in the browser** and stored as a data URL on the
user row — the free hosting tier has no persistent disk, so a file written to disk
would vanish on the next deploy. Server-side validation accepts PNG/JPEG/WebP only:
SVG is rejected because an SVG avatar is markup the page would then render.

## Schema changes

`ensure_schema()` runs at startup and adds columns that exist on the models but not yet
in the database. `create_all()` alone will not do this — it creates missing tables and
ignores existing ones, so a new column means every query selecting it fails against a
live database.

It only ever **adds**. Renames, type changes, and drops still need doing by hand, and a
NOT NULL column with no server default is skipped with a warning rather than failing
the boot. Move to Alembic before the data is worth real care.

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

**Measure, don't estimate.** Every generation logs its real token usage:

```
generation ok model=claude-opus-5 in=1240 out=118xx cache_read=0
```

Watch those lines in the Render log after your first few lessons and set the caps from
what you actually see.

For rough planning: a lesson is 6–8 cards with a diagram on most of them, so expect
output in the low tens of thousands of tokens once thinking is included. At Opus 5's
$25/million output that lands somewhere around **20–40¢ per lesson**, not the couple of
pennies a shorter lesson would cost. Digests are far cheaper — a few cents at most.

Three levers, cheapest first:

| Lever | Effect |
| --- | --- |
| `TANGENT_EFFORT=medium` (or `low`) | Cuts thinking tokens noticeably; `high` is the default because depth is the point |
| `TANGENT_MODEL=claude-sonnet-5` | Roughly 40% of Opus pricing — A/B it on your own topics first, lesson quality *is* the product |
| Lower `TANGENT_DAILY_LESSON_CAP` | Fewer generations per person per day |

**Set a monthly spend limit in the Anthropic Console.** The in-app caps are the first
line; the Console limit is the one that holds when an estimate here turns out wrong.

### Safety notes

- Model-authored SVG is sanitised server-side (`sanitize_svg`) before it reaches the
  DOM — scripts, event handlers and external references are stripped.
- Lesson prose renders through a whitelist that allows `**bold**` and `*italic*` only;
  everything else is HTML-escaped.
- Passwords are PBKDF2-SHA256 with a per-user salt. Sessions are httpOnly cookies.
- Server-side refusal fallbacks are enabled by default (Opus 5 routes policy declines
  to a fallback model in the same call). If your SDK or account doesn't have the beta,
  the app logs a warning once and continues without it.

## Deploying for free (Render + Neon)

Render's free web service is fine. Its *free database* is the problem: it expires, and
free instances have no persistent disk, so SQLite gets wiped on every deploy. Putting
Postgres somewhere else fixes that at no cost.

1. **Create a free Postgres** at [neon.tech](https://neon.tech) (or Supabase). Copy the
   connection string. No code change needed — `config.py` reads `DATABASE_URL` and
   normalises the scheme for SQLAlchemy.

2. **In Render:** New → Blueprint → pick this repo. It reads `render.yaml`.

3. **Paste two values** when prompted — `ANTHROPIC_API_KEY` and the `DATABASE_URL`
   from step 1. Both are marked `sync: false`, so neither lives in the repo.
   `TANGENT_SECRET` is generated for you; don't set it by hand, and don't change it
   later or you'll sign everyone out.

4. **Set a spend limit in the Anthropic Console** before sharing the URL. The caps
   below are application logic; the Console limit is the one a bug in the app can't
   defeat.

Deploys are automatic on push. `/healthz` is the health check.

**The free trade-off:** the service sleeps after ~15 minutes idle, so the first request
after a quiet spell takes 30–60s to wake — *before* the 30–60s of lesson generation.
Your own use won't notice much; strangers might think it's broken. The paid always-on
plan is the only real fix (see the commented block in `render.yaml`).

Ongoing generation isn't affected by sleep: the client polls every 2.5s while a lesson
is being written, which keeps the service awake for the duration.

### Why Render, and when not to

Render is the least fiddly path from a GitHub repo to an HTTPS URL — that's the whole
recommendation, not a claim it's technically better. Reasonable alternatives:

| Option | Free? | Worth it when |
| --- | --- | --- |
| **Render + Neon** | Yes | You want a link today and can live with cold starts |
| **Fly.io** | Small usage is very cheap | You want no sleep, real volumes, `fly deploy` from your machine, no GitHub round-trip |
| **Hugging Face Spaces** | Yes | Handing to testers matters more than durability — storage is ephemeral unless you pay |
| **Oracle Cloud free tier** | Yes, genuinely, forever | You don't mind being the sysadmin: a VM, nginx, systemd, TLS certs |
| **Vercel / Netlify** | — | **Don't.** Serverless kills the process after the response, which breaks background lesson generation outright |

Anything that runs a long-lived Python process works. The app is one service with no
build step, so porting it is a start command and two env vars.

### What the spend guards actually allow

Signup is open, so these are load-bearing:

| Variable | Default | Meaning |
| --- | --- | --- |
| `TANGENT_DAILY_LESSON_CAP` | 6 | Lessons one account can generate per day |
| `TANGENT_DAILY_DIGEST_CAP` | 4 | Topic-set generations per account per day |
| `TANGENT_GLOBAL_DAILY_CAP` | 300 | Generations across *all* users per day |

These are counted in *generations*, so the money depends on lesson size — and lessons
got substantially bigger when diagrams and depth went up. On the current settings a
single account maxing out is order-of-magnitude **$2/day**, and the global cap is the
only thing bounding the total. Set `TANGENT_GLOBAL_DAILY_CAP` from the token numbers in
your log plus the monthly budget you're willing to lose, and back it with a hard limit
in the Anthropic Console.

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
