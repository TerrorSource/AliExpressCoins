import asyncio
import json

import pytest

import collector


def test_parse_mtop_json_and_jsonp():
    assert collector.parse_mtop('{"a": 1}') == {"a": 1}
    assert collector.parse_mtop('mtopjsonp3({"a": 2});') == {"a": 2}
    assert collector.parse_mtop("not json") is None
    assert collector.parse_mtop("") is None


def test_find_key_nested():
    data = {"data": {"list": [{"x": 1}, {"userCoinsNum": 42}]}}
    assert collector.find_key(data, "userCoinsNum") == 42
    assert collector.find_key(data, "missing") is None


@pytest.mark.parametrize("label, matches", [
    ("Check in", True), ("Collect", True), ("Claim!", True), ("Inchecken", True),
    ("Collect, shop and save", False), ("Check in to earn coins every day", False),
])
def test_button_regex_matches_whole_labels_only(label, matches):
    assert bool(collector.BUTTON_RE.match(label)) is matches


def task(**kw):
    m = {"taskId": 1, "mainTitle": "Browse Super discounts", "type": 0,
         "materialUrl": "https://m.aliexpress.com/p/superdeals"}
    m.update(kw)
    return m


@pytest.mark.parametrize("m, kind", [
    (task(), "browse"),
    (task(type=15, materialUrl=""), "browse"),
    (task(mainTitle="Explore sponsored items"), None),
    (task(materialUrl="https://s.click.aliexpress.com/adclick?x"), None),
    (task(mainTitle="Browse surprise items"), None),
    (task(mainTitle="Play Merge Boss"), None),
    (task(mainTitle="Place an order"), None),
    (task(type=7), None),
    (task(materialUrl=""), None),
    (task(mainTitle="Daily quiz", materialUrl="https://m.aliexpress.com/coinquest.html"), "quiz"),
])
def test_task_kind(m, kind):
    assert collector.task_kind(m)[0] == kind


def test_task_done_and_coins():
    assert collector.task_done({"instanceStatus": 2})
    assert collector.task_done({"timesJoined": 3, "timesLimit": 3})
    assert not collector.task_done({"timesJoined": 1, "timesLimit": 3})
    assert collector.task_coins({"interests": [{"interestNum": "5"}]}) == 5
    assert collector.task_coins({}) == 0


class FakeResponse:
    def __init__(self, api, body, status=200):
        self.url = f"https://acs.aliexpress.com/h5/{api}/1.0/?jsv=2"
        self.status = status
        self._body = json.dumps(body)

    async def text(self):
        return self._body


def feed(probe, api, body):
    asyncio.run(probe.on_response(FakeResponse(api, body)))


def test_probe_sign_complete_balance_and_session():
    p = collector.Probe()
    feed(p, "mtop.aliexpress.coin.channel.sign.execute",
         {"ret": ["SUCCESS::ok"], "data": {"issuePrizeSuccess": True, "userCoinsNum": 120}})
    feed(p, "mtop.aliexpress.coin.pc.sign", {"ret": ["SUCCESS::ok"], "data": {"success": False}})
    feed(p, "mtop.aliexpress.interactive.task.process.complete",
         {"ret": ["SUCCESS::ok"], "data": {"result": {"completeSuccess": True,
                                                      "interestIssueResultMap": {"a": {"interestNum": 5}}}}})
    feed(p, "mtop.aliexpress.interactive.task.delivery.query",
         {"ret": ["SUCCESS::ok"], "data": {"result": [{"materials": []}]}})
    feed(p, "mtop.aliexpress.coin.boost.home.query", {"ret": ["SUCCESS::ok"], "data": {"userCoinsNum": "130"}})
    assert [s["ok"] for s in p.signs] == [True, False]
    assert p.completes == [{"ok": True, "coins": 5, "msg": "SUCCESS::ok"}]
    assert p.tasks == [{"materials": []}]
    assert (p.balance_first, p.balance) == (120, 130)
    assert not p.session_expired
    feed(p, "mtop.aliexpress.coin.channel.init", {"ret": ["FAIL_SYS_SESSION_EXPIRED::Session expired"]})
    assert p.session_expired


def sign_list(*days):
    """days: (calendarDayDistance, signSuccess, sequenceNumber), as in coin.channel.sign.list."""
    nodes = [{"calendarDayDistance": d, "signResultList": [{"signSuccess": ok, "sequenceNumber": seq}]}
             for d, ok, seq in days]
    return {"ret": ["SUCCESS::ok"], "data": {"data": {"signQuerySequenceNodeList": [{"dailySignNodeList": nodes}]}}}


@pytest.mark.parametrize("days, signed_today, streak", [
    ([(-1, False, 1), (0, True, 1), (1, False, 2)], True, 1),    # real answer from 2026-11-16
    ([(-1, True, 3), (0, False, 4), (1, False, 5)], False, 3),   # today still open, streak of 3 continues
    ([(-1, True, 3), (0, True, 4)], True, 4),
    ([(-1, False, 1), (0, False, 1)], False, 0),                 # streak broken
])
def test_probe_sign_list(days, signed_today, streak):
    p = collector.Probe()
    feed(p, "mtop.aliexpress.coin.channel.sign.list", sign_list(*days))
    assert (p.signed_today, p.streak) == (signed_today, streak)


def test_sign_list_body_is_kept_whole():
    p = collector.Probe()
    body = sign_list(*[(d, False, 1) for d in range(-2, 300)])
    feed(p, "mtop.aliexpress.coin.channel.sign.list", body)
    assert len(p.log[0]["body"]) > 2500


@pytest.mark.parametrize("text, n", [("1\n1\ndays streak", 1), ("12 days streak", 12), ("Daily coins", None)])
def test_streak_text(text, n):
    m = collector.STREAK_RE.search(text)
    assert (int(m.group(1)) if m else None) == n


def test_pc_app_only_page():
    text = "Coins\nCollect, shop and save\nEarn more coins on app\n1. Download and open the AliExpress app"
    assert collector.PC_APP_ONLY_RE.search(text)
    assert not collector.BUTTON_RE.match("Collect, shop and save")
