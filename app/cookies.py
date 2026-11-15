"""Turn cookies pasted from the user's own browser into Playwright cookies.

Accepted formats:
  - JSON from a cookie extension (Cookie-Editor, EditThisCookie) or a Playwright
    storage state ({"cookies": [...]})
  - Netscape cookies.txt
  - A Cookie request header copied from DevTools ("name=value; name2=value2")

Only AliExpress cookies are kept, and the login ticket (xman_t) must be present.
"""
import json
import re
import time

YEAR = 365 * 24 * 3600
LOGIN_COOKIES = ("xman_t", "xman_us_t")
SAMESITE = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None"}


def _expires(value, now):
    # Session cookies (and everything from a Cookie header) get a year: the container's
    # browser drops session cookies on every restart.
    try:
        value = float(value)
    except (TypeError, ValueError):
        return now + YEAR
    if value > 1e11:  # milliseconds
        value /= 1000
    return value if value > 0 else now + YEAR


def _cookie(name, value, domain, path="/", expires=None, http_only=False, secure=True, same_site=None, now=0):
    if not name or value is None or not domain:
        return None
    c = {"name": str(name), "value": str(value), "domain": str(domain), "path": path or "/",
         "expires": _expires(expires, now), "httpOnly": bool(http_only), "secure": bool(secure)}
    same_site = SAMESITE.get(str(same_site or "").lower())
    if same_site and not (same_site == "None" and not c["secure"]):
        c["sameSite"] = same_site
    return c


def _from_json(data, now):
    if isinstance(data, dict):
        data = data.get("cookies")
    if not isinstance(data, list):
        raise ValueError("De JSON is geen lijst met cookies")
    out = []
    for c in data:
        if isinstance(c, dict):
            expires = None if c.get("session") else c.get("expirationDate", c.get("expires"))
            out.append(_cookie(c.get("name"), c.get("value"), c.get("domain") or c.get("host"), c.get("path"),
                               expires, c.get("httpOnly"), c.get("secure", True), c.get("sameSite"), now))
    return out


def _from_netscape(text, now):
    out = []
    for line in text.splitlines():
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\r\n").split("\t")
        if len(f) == 7:
            domain, _, path, secure, expires, name, value = f
            out.append(_cookie(name, value, domain, path, expires, http_only, secure.upper() == "TRUE", None, now))
    return out


def _from_header(text, now):
    text = re.sub(r"^\s*cookie\s*:\s*", "", text, flags=re.I)
    out = []
    for part in text.split(";"):
        name, eq, value = part.strip().partition("=")
        if eq:
            out.append(_cookie(name.strip(), value.strip(), ".aliexpress.com", now=now))
    return out


def parse(text, now=None):
    """Playwright cookies from pasted text; raises ValueError with a message for the user."""
    now = time.time() if now is None else now
    text = (text or "").strip()
    if not text:
        raise ValueError("Er is niets geplakt")
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ValueError(f"De JSON kon niet gelezen worden ({e})") from None
        cookies = _from_json(data, now)
    elif re.search(r"^[^\t\n]*(\t[^\t\n]*){6}$", text, re.M):
        cookies = _from_netscape(text, now)
    else:
        cookies = _from_header(text, now)
    by_key = {}
    for c in cookies:
        if c and "aliexpress" in c["domain"].lower() and c["expires"] > now:
            by_key[c["name"], c["domain"], c["path"]] = c
    cookies = list(by_key.values())
    if not cookies:
        raise ValueError("Geen AliExpress-cookies gevonden. Kopieer ze terwijl een aliexpress.com-pagina open is")
    if not any(c["name"] == "xman_t" for c in cookies):
        raise ValueError("Deze cookies bevatten geen login (xman_t ontbreekt). "
                         "Log eerst in op AliExpress en kopieer ze dan opnieuw")
    return cookies
