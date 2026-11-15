"""One collection run: mobile check-in, coin tasks, daily quiz and the PC check-in.

Everything runs in a real (headful, on Xvfb) Chromium with a persistent profile, so
the AliExpress pages sign their own API calls. The bot only taps what a person would
tap and confirms each step with the API answer the page gets back.

Learned from the projects this is based on:
  - The mobile check-in and the PC ("website") check-in are separate rewards. The
    mobile one only shows with phone emulation; a desktop browser is redirected to
    coin-pc-index (mouad73/AliexpressCollecteCoins issue #2, mmzanes/ha-aliexpress-coins).
  - Tasks only count when they are started by tapping them on the coins page; opening
    their URL directly earns nothing (1khvled/aliexpress-coin-bot).
  - Tasks with ads (sponsored items, surprise items) are never automated: a bot
    viewing or tapping ads is fake ad traffic that advertisers pay for.
"""
import asyncio
import collections
import datetime
import json
import os
import random
import re
import time

from playwright.async_api import Error as PlaywrightError

import config

# Whole-label matches only: the page is full of banners like "Collect, shop and save".
BUTTON_RE = re.compile(r"^\s*(check.?in|collect|claim|verzamel(en)?|ophalen|inchecken)\s*!?\s*$", re.I)
EARN_MORE_RE = re.compile(r"^\s*(earn more|meer verdienen|verdien meer)", re.I)
MOBILE_DONE_RE = re.compile(r"today\s*✓|check-in coins tomorrow|coins tomorrow|vandaag\s*✓", re.I)
PC_DONE_RE = re.compile(r"checked in today|already checked|come back tomorrow|coins tomorrow|today\s*✓|"
                        r"collected today|vandaag\s*✓|morgen terug", re.I)
PILL_RE = re.compile(r"^\s*\+\s*\d+\s*$")
PILL_XPATH = "starts-with(normalize-space(text()), '+')"

# Task list (mtop...task.delivery.query): type 0 opens a page (often "browse for N
# seconds"), type 15 is the check-in bonus. Games, orders and app-only tasks use others.
SAFE_TASK_TYPES = {0, 15} | config.EXTRA_TASK_TYPES
AD_RE = re.compile(r"sponsor|adclick|adpost|recommends\.html|surprise items|ad-product", re.I)
QUIZ_RE = re.compile(r"quiz|coinquest", re.I)
GAME_RE = re.compile(r"merge|farm|prize land|\$0\.1|game order|place an order|purchase|widget|download the app", re.I)
BLOCK_RE = re.compile(config.TASK_BLOCKLIST, re.I) if config.TASK_BLOCKLIST else None
QUIZ_OK_RE = re.compile(r"correct|congratulations|mission complete|well done|you got it|goed zo|juist", re.I)
QUIZ_WRONG_RE = re.compile(r"wrong|incorrect|try again tomorrow|not quite|helaas|onjuist", re.I)
BUILTIN_QUIZ = {"how can you access the coins page?": "all of the above"}


def now():
    return datetime.datetime.now().astimezone()


def norm(text):
    return " ".join(str(text or "").split()).strip().lower()


def human_pause(lo=1.5, hi=4.0):
    return asyncio.sleep(random.uniform(lo, hi))


def parse_mtop(body):
    """mtop answers are JSON, sometimes wrapped as JSONP."""
    if not body:
        return None
    body = body.strip()
    if not body.startswith("{"):
        m = re.match(r"^[\w$.]+\((.*)\)\s*;?\s*$", body, re.S)
        if not m:
            return None
        body = m.group(1)
    try:
        return json.loads(body)
    except ValueError:
        return None


def find_key(obj, key):
    """First value of `key` anywhere in a JSON structure."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            found = find_key(v, key)
            if found is not None:
                return found
    return None


class Probe:
    """Listens to the AliExpress API answers the pages receive."""

    def __init__(self):
        self.log = []
        self.tasks = None
        self.tasks_at = 0.0
        self.completes = []
        self.signs = []
        self.balance_first = None
        self.balance = None
        self.session_expired = False

    async def on_response(self, r):
        url = r.url
        if "/h5/mtop." not in url:
            return
        api = url.split("/h5/")[1].split("/")[0]
        if "stay.time.report" in api:  # heartbeat every few seconds while browsing
            return
        try:
            body = await r.text()
        except Exception:
            body = None
        data = parse_mtop(body) or {}
        ret = "; ".join(data.get("ret") or [])
        self.log.append({"t": now().isoformat(timespec="seconds"), "api": api, "status": r.status,
                         "ret": ret[:200], "body": (body or "")[:2500]})
        try:
            if "SESSION_EXPIRED" in ret and "coin" in api:
                self.session_expired = True
            if "task.delivery.query" in api and isinstance(find_key(data, "result"), list):
                self.tasks = data["data"]["result"]
                self.tasks_at = time.monotonic()
            elif "task.process.complete" in api:
                res = (data.get("data") or {}).get("result") or {}
                ok = bool(res.get("completeSuccess"))
                coins = sum(int(i.get("interestNum") or 0)
                            for i in (res.get("interestIssueResultMap") or {}).values()) if ok else 0
                self.completes.append({"ok": ok, "coins": coins,
                                       "msg": str(res.get("errorMsg") or res.get("errorCode") or ret)})
            elif re.search(r"sign\.execute|\.pc\.sign$|\.pc\.sign\.|coin\..*\.sign$", api) and "list" not in api:
                success = ret.startswith("SUCCESS")
                ok = success and find_key(data, "issuePrizeSuccess") is not False \
                    and find_key(data, "success") is not False
                self.signs.append({"api": api, "ok": ok, "ret": ret, "at": time.monotonic()})
            if "coin" in api:
                n = find_key(data, "userCoinsNum")
                if isinstance(n, (int, float)) or (isinstance(n, str) and n.isdigit()):
                    n = int(n)
                    if self.balance_first is None:
                        self.balance_first = n
                    self.balance = n
        except (ValueError, KeyError, TypeError, AttributeError):
            pass

    def signed_since(self, t):
        return [s for s in self.signs if s["at"] >= t]


# ---------------------------------------------------------------- browser


def clear_profile_locks():
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        path = f"{config.PROFILE}/{name}"
        if os.path.lexists(path):
            os.remove(path)


async def launch(pw, mobile):
    """The persistent profile, as a phone (Pixel 7) or as a desktop browser."""
    clear_profile_locks()
    if mobile:
        dev = dict(pw.devices["Pixel 7"])
        dev.pop("default_browser_type", None)
    else:
        dev = {"viewport": {"width": 1280, "height": 860}}
    ctx = await pw.chromium.launch_persistent_context(
        config.PROFILE,
        headless=False,  # headful on Xvfb: closer to a real browser than headless
        locale=config.BROWSER_LOCALE,
        timezone_id=config.TZ,
        args=["--no-first-run", "--log-level=3", "--disable-background-networking",
              "--enable-unsafe-swiftshader", f"--lang={config.BROWSER_LOCALE}"],
        env={**os.environ, "DISPLAY": config.DISPLAY},
        **dev,
    )
    await prepare_cookies(ctx)
    return ctx


async def prepare_cookies(ctx):
    """Language (and optionally ship-to region) cookies, for this browser only."""
    if not (config.FORCE_ENGLISH or config.COIN_REGION):
        return
    cookies = await ctx.cookies("https://www.aliexpress.com")
    current = next((c for c in cookies if c["name"] == "aep_usuc_f"), None)
    parts = {}
    if current:
        for p in current["value"].split("&"):
            k, eq, v = p.partition("=")
            if eq:
                parts[k] = v
    parts.setdefault("site", "glo")
    if config.FORCE_ENGLISH:
        parts["b_locale"] = "en_US"
    if config.COIN_REGION:
        parts["region"] = config.COIN_REGION
    exp = time.time() + 365 * 24 * 3600
    new = [{"name": "aep_usuc_f", "value": "&".join(f"{k}={v}" for k, v in parts.items()),
            "domain": ".aliexpress.com", "path": "/", "expires": exp}]
    if config.FORCE_ENGLISH:
        new += [{"name": "intl_locale", "value": "en_US", "domain": ".aliexpress.com", "path": "/", "expires": exp},
                {"name": "aeep_hng", "value": "en_US", "domain": ".aliexpress.com", "path": "/", "expires": exp}]
    await ctx.add_cookies(new)


async def page_text(page, timeout=60000):
    try:
        return await page.inner_text("body", timeout=timeout)
    except PlaywrightError:
        return None


def is_login_url(url):
    return any(s in url for s in ("login.aliexpress", "ug-login-page", "/login.htm", "passport.aliexpress"))


async def login_button_visible(page):
    try:
        return await page.locator('[class*="aecoin-loginButton"]').filter(visible=True).count() > 0
    except PlaywrightError:
        return False


async def goto(page, url, tries=2):
    # These pages keep loading ads; "load" can take minutes and networkidle never comes.
    for attempt in range(tries):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            return
        except PlaywrightError:
            if attempt == tries - 1:
                raise


async def shot(page, name):
    try:
        os.makedirs(config.RUNS, exist_ok=True)
        await page.screenshot(path=f"{config.RUNS}/{name}.png")
    except Exception:
        pass


async def tap(locator, timeout=30000):
    # force: the Collect button pulses (CSS animation), so the stability check never passes.
    try:
        await locator.click(timeout=timeout, force=True)
    except PlaywrightError:
        await locator.dispatch_event("click", timeout=timeout)


# ---------------------------------------------------------------- mobile check-in


async def sign_button(page):
    loc = page.locator("#signButton").filter(visible=True)
    try:
        if await loc.count():
            return loc.first, norm(await loc.first.inner_text(timeout=10000))
    except PlaywrightError:
        pass
    return None, ""


async def read_streak(page):
    try:
        return await page.evaluate("""() => {
            const el = document.querySelector('[class*="aecoin-dayNumber"], [class*="dayNumber"]');
            const n = el ? parseInt(el.innerText.replace(/\\D/g, ''), 10) : NaN;
            return Number.isFinite(n) ? n : null;
        }""")
    except PlaywrightError:
        return None


async def mobile_checkin(page, probe):
    """Returns {"status": collected|already|session_expired|button_not_found|unconfirmed, ...}."""
    await goto(page, config.MOBILE_COIN_URL)
    # On a slow machine the renderer can stall while the page builds itself; wait
    # until the button (or the done text) is there and the page answers quickly.
    deadline, text = time.monotonic() + 240, ""
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        t = await page_text(page)
        settled = t is not None and time.monotonic() - t0 < 2
        text = t or text
        if probe.session_expired or is_login_url(page.url) or await login_button_visible(page):
            probe.session_expired = True
            await shot(page, "mobile")
            return {"status": "session_expired", "detail": "De coin-pagina vraagt om in te loggen"}
        btn, label = await sign_button(page)
        if EARN_MORE_RE.search(label) or MOBILE_DONE_RE.search(text):
            return {"status": "already", "detail": "Vandaag al ingecheckt", "streak": await read_streak(page)}
        if settled and (BUTTON_RE.search(label) or await page.get_by_text(BUTTON_RE).filter(visible=True).count()):
            break
        await page.wait_for_timeout(2000)
    else:
        await shot(page, "mobile")
        return {"status": "button_not_found", "detail": "Geen check-in-knop op de mobiele coin-pagina"}

    btn, label = await sign_button(page)
    target = btn if btn and BUTTON_RE.search(label) else page.get_by_text(BUTTON_RE).filter(visible=True).first
    label = label or norm(await target.inner_text(timeout=10000))
    t_click = time.monotonic()
    await human_pause(0.8, 2.5)
    await tap(target)
    await page.wait_for_timeout(8000)
    after = await page_text(page) or ""
    _, label_after = await sign_button(page)
    signed = [s for s in probe.signed_since(t_click) if s["ok"]]
    await shot(page, "mobile")
    if signed or MOBILE_DONE_RE.search(after) or EARN_MORE_RE.search(label_after):
        return {"status": "collected", "detail": f"Op '{label}' getikt", "streak": await read_streak(page)}
    return {"status": "unconfirmed", "detail": f"Op '{label}' getikt, maar geen bevestiging van AliExpress"}


# ---------------------------------------------------------------- tasks


def task_title(m):
    return " ".join(str(m.get("mainTitle") or m.get("secondTitle") or "Taak").split())


def task_id(m):
    return str(m.get("taskId"))


def task_coins(m):
    interests = m.get("interests") or [{}]
    try:
        return int((interests[0] or {}).get("interestNum") or m.get("interestNum") or 0)
    except (TypeError, ValueError):
        return 0


def task_done(m):
    return m.get("instanceStatus") == 2 or (m.get("timesJoined") or 0) >= (m.get("timesLimit") or 1)


def task_kind(m):
    """("browse"|"quiz", None) when the bot may do it, else (None, reason)."""
    text = " ".join(str(m.get(k) or "") for k in ("mainTitle", "secondTitle", "materialUrl", "behaviorConfig"))
    if AD_RE.search(text):
        return None, "advertentietaak (nooit geautomatiseerd)"
    if BLOCK_RE and BLOCK_RE.search(task_title(m)):
        return None, "staat in TASK_BLOCKLIST"
    if QUIZ_RE.search(text):
        return ("quiz", None) if config.DO_QUIZ else (None, "quiz staat uit")
    if GAME_RE.search(text):
        return None, "spel, bestelling of alleen in de app"
    if m.get("type") not in SAFE_TASK_TYPES:
        return None, f"type {m.get('type')}: spel, bestelling of alleen in de app"
    if m.get("type") == 0 and not m.get("materialUrl"):
        return None, "geen pagina om te openen"
    return "browse", None


async def open_task_drawer(page):
    for loc in (page.get_by_text(EARN_MORE_RE).filter(visible=True), page.locator("#signButton").filter(visible=True)):
        try:
            if await loc.count():
                await tap(loc.first, timeout=10000)
                await page.wait_for_timeout(2500)
                return True
        except PlaywrightError:
            continue
    return False


async def load_task_list(page, probe):
    """Reload the coins page and return its tasks (None if the list never arrives)."""
    for _ in range(2):
        t0 = time.monotonic()
        try:
            await goto(page, config.MOBILE_COIN_URL, tries=1)
        except PlaywrightError:
            continue
        for i in range(70):
            if probe.tasks is not None and probe.tasks_at >= t0:
                return [m for g in probe.tasks for m in (g.get("materials") or [])]
            if i == 25:  # some page versions only fetch the list when the drawer opens
                await open_task_drawer(page)
            await page.wait_for_timeout(1000)
    return None


async def tap_task(page, m):
    loc = page.get_by_text(task_title(m))
    shown = loc.filter(visible=True)
    try:
        await shown.first.wait_for(timeout=30000)
    except PlaywrightError:
        await open_task_drawer(page)
        try:
            await shown.first.wait_for(timeout=20000)
        except PlaywrightError:
            pass
    try:
        await shown.first.scroll_into_view_if_needed(timeout=20000)
        # Each row ends in a "+5" pill: that starts the task, not the title. Only use a
        # pill on the title's own line, or we could tap another task's pill.
        row = shown.first.locator(f"xpath=ancestor::*[.//*[{PILL_XPATH}]][1]")
        pill = row.get_by_text(PILL_RE).filter(visible=True)
        target = shown
        if await pill.count() == 1:
            tb, pb = await shown.first.bounding_box(), await pill.first.bounding_box()
            if tb and pb and abs((tb["y"] + tb["height"] / 2) - (pb["y"] + pb["height"] / 2)) \
                    < max(tb["height"], pb["height"]):
                target = pill
        await human_pause(0.6, 1.8)
        await target.first.click(force=True, timeout=15000)
    except PlaywrightError:
        await loc.first.dispatch_event("click", timeout=10000)


async def browse(ctx, page, m):
    try:
        secs = int(json.loads(m.get("behaviorConfig") or "{}").get("time") or 15)
    except (ValueError, TypeError, AttributeError):
        secs = 15
    await page.wait_for_timeout(4000)
    target = ctx.pages[-1]  # some tasks open a new tab
    end = time.monotonic() + secs + random.uniform(6, 12)
    while time.monotonic() < end:
        try:
            await target.mouse.wheel(0, random.randint(250, 600))
        except PlaywrightError:
            pass
        await page.wait_for_timeout(random.randint(2200, 3800))


def load_quiz_answers():
    answers = dict(BUILTIN_QUIZ)
    try:
        with open(config.QUIZ_FILE) as f:
            answers.update(json.load(f))
    except (FileNotFoundError, ValueError):
        pass
    return answers


def save_quiz_answer(question, answer, correct):
    try:
        with open(config.QUIZ_FILE) as f:
            known = json.load(f)
    except (FileNotFoundError, ValueError):
        known = {}
    entry = known.get(question)
    if correct:
        known[question] = answer
    else:
        wrong = set(entry.get("wrong", [])) if isinstance(entry, dict) else set()
        known[question] = {"wrong": sorted(wrong | {answer})}
    with open(config.QUIZ_FILE, "w") as f:
        json.dump(known, f, ensure_ascii=False, indent=1)


async def do_quiz(ctx, page):
    """Answer the daily quiz: a known answer, else the best guess, and remember the outcome."""
    await page.wait_for_timeout(5000)
    quiz_page = next((p for p in reversed(ctx.pages) if "coinquest" in p.url), ctx.pages[-1])
    q_loc = quiz_page.locator('[class*="questionText"]').filter(visible=True)
    try:
        await q_loc.first.wait_for(timeout=20000)
    except PlaywrightError:
        return "Quizvraag niet gevonden"
    question = re.sub(r"^q\s*:\s*", "", norm(await q_loc.first.inner_text()))
    options = quiz_page.locator('[class*="optionButton"]').filter(visible=True)
    texts = [norm(t) for t in await options.all_inner_texts()]
    if not texts:
        return "Geen antwoordopties gevonden"
    known = load_quiz_answers().get(question)
    if isinstance(known, str) and known in texts:
        answer = known
    else:
        wrong = set(known.get("wrong", [])) if isinstance(known, dict) else set()
        left = [t for t in texts if t not in wrong] or texts
        answer = next((t for t in left if "all of the above" in t), random.choice(left))
    await human_pause(2, 5)
    await tap(options.nth(texts.index(answer)), timeout=15000)
    await quiz_page.wait_for_timeout(4000)
    result = await page_text(quiz_page, timeout=20000) or ""
    if QUIZ_OK_RE.search(result):
        save_quiz_answer(question, answer, True)
        return None
    if QUIZ_WRONG_RE.search(result):
        save_quiz_answer(question, answer, False)
        return f"Fout antwoord '{answer}' op '{question}' (onthouden voor de volgende keer)"
    return None  # unclear: the task list reload tells whether it counted


async def close_extra_pages(ctx, keep):
    for p in list(ctx.pages):
        if p is not keep:
            try:
                await p.close()
            except PlaywrightError:
                pass


def load_seen():
    try:
        with open(config.SEEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


async def do_tasks(ctx, page, probe):
    """Do every allowed task until none is left; finishing one often unlocks the next."""
    seen = load_seen()
    first_run = seen is None
    seen = seen or {}
    fails = collections.Counter()
    pending = None  # (task id, timesJoined before the attempt, attempt reason)
    reasons, new, log = {}, [], []
    deadline = time.monotonic() + config.TASK_BUDGET_MINUTES * 60
    tasks = []
    while True:
        tasks = await load_task_list(page, probe)
        if tasks is None:
            log.append("De takenlijst laadde niet")
            tasks = []
            break
        for m in tasks:
            if task_id(m) not in seen:
                seen[task_id(m)] = task_title(m)
                if not first_run:
                    new.append(task_title(m))
        if pending:
            tid, joined, why = pending
            m = next((t for t in tasks if task_id(t) == tid), None)
            if m is not None and not task_done(m) and (m.get("timesJoined") or 0) <= joined:
                fails[tid] += 1
                reasons[tid] = why or "Geopend, maar AliExpress telde het niet (waarschijnlijk alleen in de app)"
            else:
                reasons.pop(tid, None)
            pending = None
        todo = [m for m in tasks if not task_done(m) and task_kind(m)[0] and fails[task_id(m)] < 2]
        if not todo or time.monotonic() > deadline:
            if todo:
                log.append(f"Tijdslimiet van {config.TASK_BUDGET_MINUTES} minuten bereikt")
            break
        m = todo[0]
        kind, _ = task_kind(m)
        n = len(probe.completes)
        why = None
        try:
            await tap_task(page, m)
            if kind == "quiz":
                why = await do_quiz(ctx, page)
            else:
                await browse(ctx, page, m)
        except PlaywrightError as e:
            why = f"Kon de taak niet vinden of aantikken ({type(e).__name__})"
        got = probe.completes[n:]
        if got and not any(c["ok"] for c in got):
            why = f"AliExpress weigerde: {got[-1]['msg'][:150]}"
        pending = (task_id(m), m.get("timesJoined") or 0, why)
        await close_extra_pages(ctx, page)
        await human_pause(2, 6)
    with open(config.SEEN_FILE, "w") as f:
        json.dump(seen, f, ensure_ascii=False)
    items = []
    for m in tasks:
        kind, skip = task_kind(m)
        state = "done" if task_done(m) else "failed" if kind else "skipped"
        items.append({"id": task_id(m), "title": task_title(m), "coins": task_coins(m),
                      "progress": f"{m.get('timesJoined') or 0}/{m.get('timesLimit') or 1}", "state": state,
                      "reason": skip if state == "skipped" else reasons.get(task_id(m)) if state == "failed" else None})
    return {"items": items, "earned": sum(c["coins"] for c in probe.completes if c["ok"]),
            "new": new, "log": log}


# ---------------------------------------------------------------- PC check-in


async def pc_checkin(page, probe):
    await goto(page, config.PC_COIN_URL)
    deadline, text, target = time.monotonic() + 120, "", None
    while time.monotonic() < deadline:
        t = await page_text(page)
        text = t or text
        if probe.session_expired or is_login_url(page.url):
            probe.session_expired = True
            await shot(page, "pc")
            return {"status": "session_expired", "detail": "De PC coin-pagina vraagt om in te loggen"}
        if PC_DONE_RE.search(text):
            return {"status": "already", "detail": "Vandaag al ingecheckt op de website"}
        for loc in (page.get_by_role("button", name=BUTTON_RE), page.get_by_text(BUTTON_RE)):
            loc = loc.filter(visible=True)
            if await loc.count():
                target = loc.first
                break
        if target is not None:
            break
        await page.wait_for_timeout(3000)
    if target is None:
        await shot(page, "pc")
        return {"status": "not_available",
                "detail": "Geen check-in-knop op de website (bij sommige accounts toont de PC-pagina alleen een QR-code)"}
    label = norm(await target.inner_text(timeout=10000))
    t_click = time.monotonic()
    await human_pause(0.8, 2.5)
    await tap(target)
    await page.wait_for_timeout(8000)
    after = await page_text(page) or ""
    signed = probe.signed_since(t_click)
    await shot(page, "pc")
    if any(s["ok"] for s in signed) or PC_DONE_RE.search(after):
        return {"status": "collected", "detail": f"Op '{label}' geklikt"}
    if signed:
        return {"status": "unconfirmed", "detail": f"AliExpress antwoordde: {signed[-1]['ret'][:150]}"}
    return {"status": "unconfirmed", "detail": f"Op '{label}' geklikt, maar geen bevestiging van AliExpress"}


# ---------------------------------------------------------------- whole run


async def run(pw, only=None):
    """One run. `only`: None (everything), "checkin" (both check-ins) or "tasks"."""
    probe = Probe()
    result = {"started": now().isoformat(timespec="seconds"), "mobile": None, "tasks": None, "pc": None}
    want_mobile = config.DO_MOBILE_CHECKIN and only in (None, "checkin")
    want_tasks = config.DO_TASKS and only in (None, "tasks")
    want_pc = config.DO_PC_CHECKIN and only in (None, "checkin")
    texts = {}

    if want_mobile or want_tasks:
        ctx = await launch(pw, mobile=True)
        ctx.on("response", probe.on_response)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            if want_mobile:
                result["mobile"] = await mobile_checkin(page, probe)
            if want_tasks and not probe.session_expired:
                try:
                    result["tasks"] = await do_tasks(ctx, page, probe)
                except Exception as e:  # the tasks must never spoil a good check-in
                    result["tasks"] = {"error": f"{type(e).__name__}: {e}"[:500], "items": [], "earned": 0}
                await shot(page, "tasks")
        except Exception as e:
            result["mobile"] = result["mobile"] or {"status": "error", "detail": f"{type(e).__name__}: {e}"[:500]}
            await shot(page, "mobile")
        finally:
            texts["mobile"] = (await page_text(page, timeout=10000) or "")[:5000]
            await ctx.close()

    if want_pc and not probe.session_expired:
        ctx = await launch(pw, mobile=False)
        ctx.on("response", probe.on_response)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            result["pc"] = await pc_checkin(page, probe)
        except Exception as e:
            result["pc"] = {"status": "error", "detail": f"{type(e).__name__}: {e}"[:500]}
            await shot(page, "pc")
        finally:
            texts["pc"] = (await page_text(page, timeout=10000) or "")[:5000]
            await ctx.close()

    if probe.session_expired:
        for k in ("mobile", "pc"):
            if result[k] and result[k]["status"] not in ("collected", "already"):
                result[k] = {"status": "session_expired", "detail": "Login verlopen"}
    result.update(finished=now().isoformat(timespec="seconds"), session_expired=probe.session_expired,
                  balance=probe.balance, balance_start=probe.balance_first)
    os.makedirs(config.RUNS, exist_ok=True)
    with open(f"{config.RUNS}/last_run.json", "w") as f:
        json.dump({**result, "page_text": texts, "api": probe.log[-400:]}, f, ensure_ascii=False, indent=1)
    return result
