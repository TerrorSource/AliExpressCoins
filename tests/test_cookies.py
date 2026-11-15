import json

import pytest

import cookies

NOW = 1_800_000_000


def names(result):
    return sorted(c["name"] for c in result)


def test_cookie_editor_json():
    data = [
        {"name": "xman_t", "value": "abc", "domain": ".aliexpress.com", "path": "/",
         "expirationDate": NOW + 1000, "httpOnly": True, "secure": True, "sameSite": "no_restriction"},
        {"name": "aep_usuc_f", "value": "x", "domain": ".aliexpress.com", "session": True},
        {"name": "other", "value": "y", "domain": ".example.com"},
    ]
    result = cookies.parse(json.dumps(data), now=NOW)
    assert names(result) == ["aep_usuc_f", "xman_t"]
    xman = next(c for c in result if c["name"] == "xman_t")
    assert xman["httpOnly"] and xman["sameSite"] == "None" and xman["expires"] == NOW + 1000
    session = next(c for c in result if c["name"] == "aep_usuc_f")
    assert session["expires"] == NOW + cookies.YEAR


def test_playwright_storage_state_and_milliseconds():
    state = {"cookies": [{"name": "xman_t", "value": "a", "domain": "www.aliexpress.com",
                          "expires": (NOW + 50) * 1000}]}
    [c] = cookies.parse(json.dumps(state), now=NOW)
    assert c["expires"] == NOW + 50


def test_netscape_cookies_txt():
    text = "\n".join([
        "# Netscape HTTP Cookie File",
        f"#HttpOnly_.aliexpress.com\tTRUE\t/\tTRUE\t{NOW + 99}\txman_t\tv1",
        f".aliexpress.com\tTRUE\t/\tFALSE\t{NOW + 99}\tintl_locale\ten_US",
    ])
    result = cookies.parse(text, now=NOW)
    assert names(result) == ["intl_locale", "xman_t"]
    assert next(c for c in result if c["name"] == "xman_t")["httpOnly"]


def test_cookie_header():
    result = cookies.parse("Cookie: xman_t=abc; xman_us_t=def; aep_usuc_f=site=glo", now=NOW)
    assert names(result) == ["aep_usuc_f", "xman_t", "xman_us_t"]
    assert next(c for c in result if c["name"] == "aep_usuc_f")["value"] == "site=glo"


def test_expired_cookies_are_dropped():
    data = [{"name": "xman_t", "value": "a", "domain": ".aliexpress.com", "expirationDate": NOW - 1}]
    with pytest.raises(ValueError, match="Geen AliExpress-cookies"):
        cookies.parse(json.dumps(data), now=NOW)


@pytest.mark.parametrize("text, message", [
    ("", "niets geplakt"),
    ("{broken", "JSON kon niet"),
    ('{"foo": 1}', "geen lijst"),
    ("a=b; c=d", "xman_t ontbreekt"),
])
def test_errors(text, message):
    with pytest.raises(ValueError, match=message):
        cookies.parse(text, now=NOW)
