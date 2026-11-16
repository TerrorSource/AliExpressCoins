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


# Real materials from a 2026-11-16 task list (shortened).
FARM = "https://m.aliexpress.com/p/aefarm/index.html?_immersiveMode=true&from=d11task&adc_manifest=aefarm_index"
SHORTCUT = "https://m.aliexpress.com/p/coin-index/index.html?_immersiveMode=true&from=shortcut&user_growth_type=shortcut"
WATER = task(taskId=3324001, mainTitle="Water to win a US $0.10 prize", type=30, materialUrl=FARM,
             secondTitle="Water your tree in Prize Land now!",
             behaviorConfig=json.dumps({"taskTagId": "watering2.0", "url": FARM}))
SHORTCUT_TASK = task(taskId=3298003, mainTitle="Visit the page via coins shortcut ", type=29, materialUrl=None,
                     behaviorConfig=json.dumps({"shortcutUrl": SHORTCUT, "shortcutId": "coinsPromotion"}),
                     shortcutMaterial=json.dumps({"shortcutUrl": SHORTCUT}))
SURPRISE = task(taskId=3302001, mainTitle="Browse surprise items", type=26, materialUrl=None,
                behaviorConfig=json.dumps({"url": "https://m.aliexpress.com/p/coin-index/adclick.html?adpost=true"}))


@pytest.mark.parametrize("m, kind", [
    (WATER, "water"),
    (SHORTCUT_TASK, "shortcut"),
    (task(type=29, materialUrl=None, behaviorConfig=json.dumps({"shortcutUrl": "https://evil.example/x"})), None),
    (SURPRISE, None),
    (task(mainTitle="Share coins with friends", type=22, materialUrl=None), None),
    (task(mainTitle="Add & start using our widget", type=38, materialUrl=None), None),
    (task(mainTitle="Turn on notifications", type=43, materialUrl=None), None),
    (task(mainTitle="Items at $0.1 to get", materialUrl="https://m.aliexpress.com/ssr/300000949/farmpc"), None),
])
def test_task_kind_new_types(m, kind, monkeypatch):
    monkeypatch.setattr(collector.config, "DO_WATER", True)
    monkeypatch.setattr(collector.config, "DO_SHORTCUT", True)
    assert collector.task_kind(m)[0] == kind


def test_water_and_shortcut_are_off_by_default(monkeypatch):
    # Tested 2026-11-16: Prize Land only shows "Install App" on the website, and the
    # shortcut link is not counted when a browser opens it.
    monkeypatch.setattr(collector.config, "DO_WATER", False)
    monkeypatch.setattr(collector.config, "DO_SHORTCUT", False)
    assert collector.task_kind(WATER) == (None, "Prize Land werkt alleen in de app")
    assert collector.task_kind(SHORTCUT_TASK) == (None, "telt alleen via de snelkoppeling in de app")


def test_shortcut_url_falls_back_to_shortcut_material():
    m = dict(SHORTCUT_TASK, behaviorConfig="{}")
    assert collector.shortcut_url(m) == SHORTCUT


@pytest.mark.parametrize("label, water", [
    ("Water", True), ("Water now", True), ("Tap to water", True),
    ("Watermelon phone case", False), ("Buy for $0.10", False),
])
def test_water_button_regex(label, water):
    assert bool(collector.WATER_RE.match(label)) == water


def test_task_items_keep_tasks_that_left_the_list():
    super_d = task(taskId=3320004, mainTitle="View Super discounts", timesJoined=0, timesLimit=1)
    checkin = task(taskId=3296002, mainTitle="Daily check-in", type=15, timesJoined=0, timesLimit=1)
    gone = task(taskId=999, mainTitle="Old task", timesJoined=0, timesLimit=1)
    venue = task(taskId=3336005, mainTitle="View the venue", timesJoined=1, timesLimit=1)
    latest = {collector.task_id(m): m for m in (super_d, checkin, gone, venue, SURPRISE)}
    completes = [{"ok": True, "coins": 5, "task": "3320004"}, {"ok": True, "coins": 1, "task": "3296002"},
                 {"ok": False, "coins": 0, "task": "999"}]
    items = {i["id"]: i for i in collector.task_items(latest, [venue, SURPRISE], completes, {})}
    assert items["3320004"]["state"] == "done" and items["3320004"]["progress"] == "1/1"
    assert items["3296002"]["state"] == "done"
    assert "999" not in items
    assert items["3336005"]["state"] == "done"
    assert items["3302001"]["state"] == "skipped"


def test_probe_reads_task_id_of_a_completion():
    p = collector.Probe()
    issue = json.dumps({"extraData": {"bizExtend": json.dumps({"behaviorType": 0, "taskId": 3304001})}})
    feed(p, "mtop.aliexpress.interactive.task.process.complete",
         {"ret": ["SUCCESS::ok"], "data": {"result": {"completeSuccess": True, "interestIssueResultMap": {
             "947": {"interestNum": 5, "successIssueResult": issue}}}}})
    assert p.completes[-1]["task"] == "3304001" and p.completes[-1]["coins"] == 5


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
    assert p.completes == [{"ok": True, "coins": 5, "task": None, "msg": "SUCCESS::ok"}]
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
