"""Web panel, scheduler and login browser for the AliExpress coin collector."""
import asyncio
import base64
import datetime
import hmac
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from zoneinfo import ZoneInfo

from aiohttp import WSMsgType, web
from playwright.async_api import async_playwright

import collector
import config
import cookies as cookie_import
from version import __version__

HERE = os.path.dirname(os.path.abspath(__file__))
TZ = ZoneInfo(config.TZ)
lock = asyncio.Lock()  # one user of the browser profile at a time
login = {}  # chrome, vnc, timer while the login browser is open
background = set()
state = {"running": False, "phase": None, "next_run": None}


def now():
    return datetime.datetime.now(TZ)


def log(*args):
    print(now().strftime("%Y-%m-%d %H:%M:%S"), *args, flush=True)


# ---------------------------------------------------------------- status file


def load_status():
    try:
        with open(config.STATUS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"last_run": None, "history": [], "last_success": {}, "cookie_import": None}


def save_status(st):
    tmp = f"{config.STATUS_FILE}.tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, config.STATUS_FILE)


def login_expires():
    """When the login cookies in the profile expire (ISO 8601), or None."""
    path = f"{config.PROFILE}/Default/Cookies"
    try:
        with tempfile.NamedTemporaryFile() as tmp:
            shutil.copyfile(path, tmp.name)  # Chromium may hold the DB open
            with closing(sqlite3.connect(tmp.name)) as db:
                (us,) = db.execute(
                    "SELECT min(expires_utc) FROM cookies WHERE host_key LIKE '%aliexpress.com'"
                    " AND is_persistent AND name IN ('xman_t', 'xman_us_t')").fetchone()
    except (OSError, sqlite3.Error):
        return None
    if not us:
        return None
    epoch = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)  # Chromium's cookie epoch
    return (epoch + datetime.timedelta(microseconds=us)).astimezone(TZ).isoformat(timespec="seconds")


def summarize(result):
    """Overall state of a run: ok, partial, session_expired or error."""
    if result.get("session_expired"):
        return "session_expired"
    steps = [result[k]["status"] for k in ("mobile", "pc") if result.get(k)]
    good = {"collected", "already", "not_available"}
    if steps and all(s in good for s in steps) and not (result.get("tasks") or {}).get("error"):
        return "ok"
    if any(s in good for s in steps):
        return "partial"
    return "ok" if not steps and result.get("tasks") and not result["tasks"].get("error") else "error"


def record(result):
    st = load_status()
    today = now().date().isoformat()
    result["state"] = summarize(result)
    for k in ("mobile", "pc"):
        if (result.get(k) or {}).get("status") in ("collected", "already"):
            st.setdefault("last_success", {})[k] = today
    earned = (result.get("tasks") or {}).get("earned", 0)
    day = st.get("today") or {}
    if day.get("date") != today:
        day = {"date": today, "task_coins": 0}
    day["task_coins"] += earned
    if result.get("balance") is not None:
        day.setdefault("balance_start", result.get("balance_start") or result["balance"])
        day["balance"] = result["balance"]
    st["today"] = day
    st["last_run"] = result
    st["history"] = ([{"at": result["finished"], "state": result["state"],
                       "mobile": (result.get("mobile") or {}).get("status"),
                       "pc": (result.get("pc") or {}).get("status"),
                       "task_coins": earned, "balance": result.get("balance")}]
                     + st.get("history", []))[:60]
    save_status(st)
    return st


# ---------------------------------------------------------------- notifications


def notify(title, body):
    if not config.NOTIFY_URLS or config.NOTIFY_LEVEL == "none":
        return
    try:
        import apprise
        a = apprise.Apprise()
        for url in config.NOTIFY_URLS:
            a.add(url)
        a.notify(title=title, body=body)
    except Exception as e:
        log("Melding versturen mislukt:", e)


STEP_NL = {"collected": "opgehaald", "already": "was al gedaan", "session_expired": "login verlopen",
           "button_not_found": "knop niet gevonden", "unconfirmed": "niet bevestigd", "not_available": "niet beschikbaar",
           "error": "fout"}


def run_text(result):
    lines = []
    for k, name in (("mobile", "Mobiele check-in"), ("pc", "Website check-in")):
        if result.get(k):
            lines.append(f"{name}: {STEP_NL.get(result[k]['status'], result[k]['status'])} – {result[k].get('detail', '')}")
    tk = result.get("tasks")
    if tk:
        done = sum(1 for i in tk.get("items", []) if i["state"] == "done")
        lines.append(f"Taken: {done} klaar, +{tk.get('earned', 0)} coins deze run" + (f" ({tk['error']})" if tk.get("error") else ""))
    if result.get("balance") is not None:
        lines.append(f"Saldo: {result['balance']} coins")
    return "\n".join(lines)


async def notify_result(result, manual):
    st = result["state"]
    if st == "session_expired":
        await asyncio.to_thread(notify, "AliExpress: opnieuw inloggen nodig",
                                "De login van de coin collector is verlopen. Open het paneel en log opnieuw in.")
    elif st in ("error", "partial") and not manual:
        await asyncio.to_thread(notify, "AliExpress coins: probleem", run_text(result))
    elif config.NOTIFY_LEVEL == "summary" and not manual:
        await asyncio.to_thread(notify, "AliExpress coins", run_text(result))


# ---------------------------------------------------------------- runs


async def do_run(only=None, manual=False):
    if lock.locked() and login:
        await stop_login()
    async with lock:
        state.update(running=True, phase=only or "all")
        log(f"Run gestart ({only or 'alles'})")
        try:
            async with async_playwright() as pw:
                result = await collector.run(pw, only)
        except Exception as e:
            result = {"started": now().isoformat(timespec="seconds"), "finished": now().isoformat(timespec="seconds"),
                      "mobile": {"status": "error", "detail": f"{type(e).__name__}: {e}"[:500]}}
        finally:
            state.update(running=False, phase=None)
        record(result)
        log(f"Run klaar: {result['state']}\n" + run_text(result))
    await notify_result(result, manual)
    return result


def start_run(only=None, manual=True):
    if lock.locked() and not login:
        return False
    task = asyncio.create_task(do_run(only, manual))
    background.add(task)
    task.add_done_callback(background.discard)
    return True


def next_run_time():
    t = now()
    candidates = []
    for hm in config.SCHEDULE:
        h, m = map(int, hm.split(":"))
        for days in (0, 1):
            c = (t + datetime.timedelta(days=days)).replace(hour=h % 24, minute=m % 60, second=0, microsecond=0)
            if c > t:
                candidates.append(c)
    if not candidates:
        return None
    return min(candidates) + datetime.timedelta(seconds=random.randint(0, max(0, config.JITTER_MINUTES) * 60))


async def scheduler():
    if config.RUN_ON_START:
        st = load_status()
        today = now().date().isoformat()
        wanted = [k for k, on in (("mobile", config.DO_MOBILE_CHECKIN), ("pc", config.DO_PC_CHECKIN)) if on]
        has_login = os.path.exists(f"{config.PROFILE}/Default/Cookies")  # nothing to do before the first login
        if has_login and any(st.get("last_success", {}).get(k) != today for k in wanted):
            await asyncio.sleep(60)
            log("Vandaag nog niet (volledig) ingecheckt: run bij het opstarten")
            await do_run()
    while True:
        nxt = next_run_time()
        state["next_run"] = nxt.isoformat(timespec="seconds") if nxt else None
        if not nxt:
            log("Geen geldige SCHEDULE; automatische runs staan uit")
            return
        log("Volgende run:", nxt.strftime("%Y-%m-%d %H:%M"))
        while (delay := (nxt - now()).total_seconds()) > 0:
            await asyncio.sleep(min(delay, 300))
        while login:  # someone is logging in; wait for them
            await asyncio.sleep(30)
        await do_run()


# ---------------------------------------------------------------- login browser (noVNC)


async def stop_login():
    if not login:
        return
    if (t := login.get("timer")) and t is not asyncio.current_task():
        t.cancel()
    try:
        login["chrome"].terminate()
        try:
            await asyncio.to_thread(login["chrome"].wait, 15)
        except subprocess.TimeoutExpired:
            login["chrome"].kill()
    finally:
        login["vnc"].terminate()
        try:
            await asyncio.to_thread(login["vnc"].wait, 5)
        except subprocess.TimeoutExpired:
            login["vnc"].kill()
        login.clear()
        lock.release()
        log("Loginbrowser gesloten")


async def login_timeout():
    await asyncio.sleep(config.LOGIN_TIMEOUT_MINUTES * 60)
    await stop_login()


async def api_login_start(request):
    if not login:
        if lock.locked():
            raise web.HTTPConflict(text="Er loopt een run, probeer het over een minuut opnieuw")
        await lock.acquire()
        try:
            vnc = subprocess.Popen(["x11vnc", "-display", config.DISPLAY, "-forever", "-shared", "-nopw",
                                    "-localhost", "-rfbport", "5900", "-quiet"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # A plain Chromium, not driven by Playwright: AliExpress's login captcha
            # rejects automation-controlled browsers.
            collector.clear_profile_locks()
            chrome = subprocess.Popen(
                [config.chrome_path(), f"--user-data-dir={config.PROFILE}", "--no-sandbox", "--test-type",
                 "--no-first-run", "--no-default-browser-check",
                 "--log-level=3", "--disable-background-networking", "--enable-unsafe-swiftshader",
                 f"--window-size={config.SCREEN_W},{config.SCREEN_H}", "--window-position=0,0",
                 f"--lang={config.BROWSER_LOCALE}", config.LOGIN_URL],
                env={**os.environ, "DISPLAY": config.DISPLAY, "GOOGLE_API_KEY": "no",  # no "API keys missing" bar
                     "GOOGLE_DEFAULT_CLIENT_ID": "no", "GOOGLE_DEFAULT_CLIENT_SECRET": "no"},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            lock.release()
            raise
        login.update(chrome=chrome, vnc=vnc, timer=asyncio.create_task(login_timeout()))
        log("Loginbrowser geopend")
        await asyncio.sleep(3)
    return web.json_response({"url": "novnc/vnc.html?autoconnect=1&resize=scale&reconnect=1&path=websockify"})


async def api_login_stop(request):
    await stop_login()
    started = start_run("checkin") if request.query.get("check") == "1" else False
    return web.json_response({"ok": True, "run_started": started})


async def websockify(request):
    ws = web.WebSocketResponse(protocols=("binary",))
    await ws.prepare(request)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 5900)
    except OSError:
        await ws.close()
        return ws

    async def vnc_to_ws():
        while data := await reader.read(65536):
            await ws.send_bytes(data)
        await ws.close()

    pump = asyncio.create_task(vnc_to_ws())
    async for msg in ws:
        if msg.type == WSMsgType.BINARY:
            writer.write(msg.data)
            await writer.drain()
    pump.cancel()
    writer.close()
    return ws


# ---------------------------------------------------------------- cookie import


async def api_cookies(request):
    form = await request.post()
    try:
        parsed = cookie_import.parse(form.get("cookies") or "")
    except ValueError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    if lock.locked() and not login:
        return web.json_response({"ok": False, "error": "Er loopt een run, probeer het over een minuut opnieuw"},
                                 status=409)
    if login:
        await stop_login()
    async with lock:
        async with async_playwright() as pw:
            ctx = await collector.launch(pw, mobile=True)
            try:
                await ctx.clear_cookies(domain=re.compile("aliexpress", re.I))
                await ctx.add_cookies(parsed)
                await collector.prepare_cookies(ctx)
            finally:
                await ctx.close()  # a clean shutdown writes the cookies to disk
    st = load_status()
    st["cookie_import"] = {"at": now().isoformat(timespec="seconds"), "n": len(parsed)}
    save_status(st)
    log(f"{len(parsed)} cookies geïmporteerd")
    return web.json_response({"ok": True, "n": len(parsed), "run_started": start_run("checkin")})


# ---------------------------------------------------------------- HTTP


@web.middleware
async def auth(request, handler):
    if config.PANEL_PASSWORD and request.path != "/health":
        header = request.headers.get("Authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = hmac.compare_digest(user, config.PANEL_USER) and hmac.compare_digest(pw, config.PANEL_PASSWORD)
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="AliExpress coins"'})
    return await handler(request)


async def index(request):
    return web.FileResponse(os.path.join(HERE, "panel.html"), headers={"Cache-Control": "no-store"})


async def api_status(request):
    st = load_status()
    last = st.get("last_run") or {}
    today = st.get("today") or {}
    is_today = today.get("date") == now().date().isoformat()
    return web.json_response({
        "version": __version__,
        "state": "running" if state["running"] else (last.get("state") or "never_run"),
        "running": state["running"], "phase": state["phase"],
        "login_open": bool(login), "login_expires": login_expires(),
        "next_run": state["next_run"],
        "balance": last.get("balance"),
        "task_coins_today": today.get("task_coins", 0) if is_today else 0,
        "balance_change_today": (today["balance"] - today["balance_start"])
        if is_today and today.get("balance") is not None and today.get("balance_start") is not None else None,
        "streak": (last.get("mobile") or {}).get("streak"),
        "last_success": st.get("last_success", {}),
        "last_run": last,
        "history": st.get("history", [])[:30],
        "cookie_import": st.get("cookie_import"),
        "settings": {"mobile_checkin": config.DO_MOBILE_CHECKIN, "pc_checkin": config.DO_PC_CHECKIN,
                     "tasks": config.DO_TASKS, "quiz": config.DO_QUIZ, "schedule": config.SCHEDULE,
                     "region": config.COIN_REGION or None, "notify": bool(config.NOTIFY_URLS)},
    })


async def api_run(request):
    only = request.query.get("only")
    if only not in (None, "checkin", "tasks"):
        raise web.HTTPBadRequest(text="only moet checkin of tasks zijn")
    if not start_run(only):
        return web.json_response({"ok": False, "error": "Er loopt al een run"}, status=409)
    return web.json_response({"ok": True}, status=202)


async def screenshot(request):
    name = request.match_info["name"]
    path = f"{config.RUNS}/{name}.png"
    if name not in ("mobile", "tasks", "pc") or not os.path.exists(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "no-store"})


async def last_run_json(request):
    path = f"{config.RUNS}/last_run.json"
    if not os.path.exists(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": "application/json"})


async def health(request):
    return web.Response(text="ok")


async def on_startup(app):
    os.makedirs(config.PROFILE, exist_ok=True)
    os.makedirs(config.RUNS, exist_ok=True)
    app["xvfb"] = subprocess.Popen(
        ["Xvfb", config.DISPLAY, "-screen", "0", f"{config.SCREEN_W}x{config.SCREEN_H}x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(1)
    if not config.PANEL_PASSWORD:
        log("LET OP: PANEL_PASSWORD is niet gezet. Iedereen die poort", config.PORT,
            "kan bereiken, kan je AliExpress-sessie gebruiken.")
    t = asyncio.create_task(scheduler())
    background.add(t)


async def on_cleanup(app):
    await stop_login()
    app["xvfb"].terminate()


def main():
    app = web.Application(middlewares=[auth], client_max_size=4 * 1024 * 1024)
    app.add_routes([
        web.get("/", index),
        web.get("/health", health),
        web.get("/api/status", api_status),
        web.post("/api/run", api_run),
        web.post("/api/login/start", api_login_start),
        web.post("/api/login/stop", api_login_stop),
        web.post("/api/cookies", api_cookies),
        web.get("/api/last_run.json", last_run_json),
        web.get("/screenshot/{name}.png", screenshot),
        web.get("/websockify", websockify),
        web.static("/novnc", "/usr/share/novnc"),
    ])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    log(f"AliExpress Coins v{__version__} – paneel op poort {config.PORT}, schema {', '.join(config.SCHEDULE) or '-'} ({config.TZ})")
    web.run_app(app, port=config.PORT, print=None)


if __name__ == "__main__":
    main()
