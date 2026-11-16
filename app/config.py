"""Settings, read once from environment variables (see .env.example)."""
import glob
import os
import re


def env(name, default=""):
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def env_bool(name, default):
    return env(name, str(default)).lower() in ("1", "true", "yes", "on", "ja")


def env_int(name, default):
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


DATA = env("DATA_DIR", "/data")
PROFILE = f"{DATA}/profile"
RUNS = f"{DATA}/runs"
STATUS_FILE = f"{DATA}/status.json"
QUIZ_FILE = f"{DATA}/quiz_answers.json"
SEEN_FILE = f"{DATA}/seen_tasks.json"

PORT = env_int("PORT", 8080)
DISPLAY = env("DISPLAY", ":99")
SCREEN_W, SCREEN_H = 1280, 900

TZ = env("TZ", "Europe/Amsterdam")
# Times of day (local, HH:MM) for the automatic runs. Each run is idempotent, so a
# second run later in the day only picks up what the first one missed.
SCHEDULE = [t for t in re.split(r"[,\s]+", env("SCHEDULE", "10:30,20:30")) if re.fullmatch(r"\d{1,2}:\d{2}", t)]
JITTER_MINUTES = env_int("JITTER_MINUTES", 20)
RUN_ON_START = env_bool("RUN_ON_START", True)

DO_MOBILE_CHECKIN = env_bool("MOBILE_CHECKIN", True)
DO_PC_CHECKIN = env_bool("PC_CHECKIN", True)
DO_TASKS = env_bool("TASKS", True)
DO_QUIZ = env_bool("QUIZ", True)
# "Water to win" (Prize Land): give the tree its daily water. Never buys or picks a prize.
# Off by default: on the mobile website Prize Land only shows "Install App" (2026-11).
DO_WATER = env_bool("PRIZE_LAND_WATER", False)
# "Visit the page via coins shortcut": open the link the app's home-screen shortcut opens.
# Off by default: opening that link in a browser is not counted (2026-11).
DO_SHORTCUT = env_bool("SHORTCUT_TASK", False)
TASK_BUDGET_MINUTES = env_int("TASK_BUDGET_MINUTES", 30)
# Extra task types (numbers from the task list) to treat as "browse a page" tasks.
EXTRA_TASK_TYPES = {int(t) for t in re.findall(r"\d+", env("EXTRA_TASK_TYPES", ""))}
# Regex: tasks whose title matches are never done.
TASK_BLOCKLIST = env("TASK_BLOCKLIST", "")

# Two-letter ship-to country for the container's browser only (e.g. KR). Empty: keep
# whatever the account uses.
COIN_REGION = env("COIN_REGION", "").upper()
if not re.fullmatch(r"[A-Z]{2}", COIN_REGION):
    COIN_REGION = ""
# Show the AliExpress pages in English so the button texts are recognised reliably.
FORCE_ENGLISH = env_bool("FORCE_ENGLISH", True)
BROWSER_LOCALE = env("BROWSER_LOCALE", "en-US")

# Apprise URLs, comma separated (ntfy://, tgram://, discord://, mailto://, hassio://, ...).
NOTIFY_URLS = [u for u in re.split(r"[,\s]+", env("NOTIFY_URLS", "")) if u]
# errors: only problems (login expired, failures) | summary: also a daily summary | none
NOTIFY_LEVEL = env("NOTIFY_LEVEL", "errors").lower()

PANEL_USER = env("PANEL_USER", "admin")
PANEL_PASSWORD = env("PANEL_PASSWORD", "")

LOGIN_TIMEOUT_MINUTES = env_int("LOGIN_TIMEOUT_MINUTES", 15)

MOBILE_COIN_URL = "https://m.aliexpress.com/p/coin-index/index.html"
PC_COIN_URL = "https://www.aliexpress.com/p/coin-pc-index/index.html"
LOGIN_URL = "https://www.aliexpress.com/p/ug-login-page/login.html"


def chrome_path():
    """The full Chromium that Playwright installed (not the headless shell)."""
    found = sorted(glob.glob("/ms-playwright/chromium-*/chrome-linux*/chrome"))
    return found[-1] if found else None
