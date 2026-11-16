import os

import config
import main


def result(mobile="already", pc="not_available", items=()):
    return {"started": "2026-11-16T10:33:00+01:00", "finished": "2026-11-16T10:34:00+01:00",
            "mobile": {"status": mobile, "detail": ""}, "pc": {"status": pc, "detail": ""},
            "tasks": {"items": list(items), "earned": 0}, "balance": 474, "balance_start": 474}


def test_not_available_pc_counts_as_done_for_today(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(config, "PROFILE", str(tmp_path / "profile"))
    os.makedirs(tmp_path / "profile" / "Default")
    (tmp_path / "profile" / "Default" / "Cookies").write_text("")
    assert main.checkin_missing_today()
    main.record(result())
    assert not main.checkin_missing_today()  # no second run at every restart


def test_run_text_lists_failed_tasks():
    text = main.run_text(result(items=[
        {"title": "Recently viewed", "state": "done"},
        {"title": "Water to win", "state": "failed", "reason": "Waterknop niet gevonden"},
    ]))
    assert "Taken: 1 klaar" in text
    assert "Niet gelukt: Water to win – Waterknop niet gevonden" in text
