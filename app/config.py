import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env loader so there's no extra dependency."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _database_url() -> str:
    """Render injects DATABASE_URL for its Postgres; everything else falls back
    to a local SQLite file."""
    url = os.environ.get("DATABASE_URL") or os.environ.get("TANGENT_DB")
    if not url:
        return f"sqlite:///{BASE_DIR / 'tangent.db'}"
    # SQLAlchemy 2 needs an explicit driver; Render hands out the bare scheme.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = _database_url()
SECRET = os.environ.get("TANGENT_SECRET", "dev-secret-change-me")

ENV = os.environ.get("TANGENT_ENV", "development")
# RENDER is set automatically on Render; treat that as production even if the
# operator forgets TANGENT_ENV. Drives Secure cookies.
IS_PRODUCTION = ENV == "production" or bool(os.environ.get("RENDER"))

# Claude Opus 5 is the default: lesson quality is the product, and a lesson is
# a single generation (~$0.02-0.05). Set TANGENT_MODEL=claude-sonnet-5 to trade
# some quality for roughly 40% of the cost.
MODEL = os.environ.get("TANGENT_MODEL", "claude-opus-5")
EFFORT = os.environ.get("TANGENT_EFFORT", "high")

# --- Spend guards -----------------------------------------------------------
# Signup is open, so these are the only thing between a public URL and a large
# Anthropic bill. Per-user caps stop one account running away; the global cap is
# the backstop against a hundred accounts each behaving "reasonably".
# Screen-capture extraction is a different job from writing a lesson: it reads
# a handful of downscaled frames and names the task. It runs many times an hour,
# which makes it the biggest cost lever in the app — and every line it produces
# is confirmed by the user before it counts, so a cheaper model's mistakes are
# caught by design rather than shipped. Raise to claude-sonnet-5 if the
# proposals need too much correcting.
VISION_MODEL = os.environ.get("TANGENT_VISION_MODEL", "claude-haiku-4-5")
# Extraction calls per user per day. At one call per ~5 minutes of recording,
# 60 covers a five-hour day.
DAILY_CAPTURE_CAP = int(os.environ.get("TANGENT_DAILY_CAPTURE_CAP", "60"))

DAILY_LESSON_CAP = int(os.environ.get("TANGENT_DAILY_LESSON_CAP", "6"))
DAILY_DIGEST_CAP = int(os.environ.get("TANGENT_DAILY_DIGEST_CAP", "4"))
GLOBAL_DAILY_CAP = int(os.environ.get("TANGENT_GLOBAL_DAILY_CAP", "300"))

# --- Sessions & email --------------------------------------------------------
SESSION_DAYS = int(os.environ.get("TANGENT_SESSION_DAYS", "90"))
RESET_TTL_MINUTES = int(os.environ.get("TANGENT_RESET_TTL_MINUTES", "60"))

# Public origin, used to build password-reset links. Render exposes its own
# hostname; fall back to that so this works with no configuration.
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
BASE_URL = os.environ.get(
    "TANGENT_BASE_URL",
    f"https://{_render_host}" if _render_host else "http://localhost:8000",
).rstrip("/")

SMTP_HOST = os.environ.get("TANGENT_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("TANGENT_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("TANGENT_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("TANGENT_SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("TANGENT_MAIL_FROM", "Tangent <no-reply@localhost>")

# --- Library -----------------------------------------------------------------
# Reuse an existing lesson when someone picks a topic already written. This is
# the difference between cost scaling with users and cost scaling with topics.
LIBRARY_REUSE = os.environ.get("TANGENT_LIBRARY_REUSE", "1") not in ("0", "false", "False")

WEB_DIR = BASE_DIR / "web"
