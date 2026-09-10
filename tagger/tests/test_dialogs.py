import json
import subprocess

from framelog_tagger import dialogs


def test_pick_rejects_unknown_kind():
    assert "error" in dialogs.pick("window")


def test_pick_parses_helper_output(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["req"] = json.loads(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, stdout='{"path": "C:/rolls/12"}\n', stderr="")

    monkeypatch.setattr(dialogs.subprocess, "run", fake_run)
    assert dialogs.pick("folder", title="Pick") == {"path": "C:/rolls/12"}
    assert seen["req"]["kind"] == "folder" and seen["req"]["title"] == "Pick"
    assert seen["req"]["initial"] is None  # no initial dir given


def test_pick_reports_helper_failure(monkeypatch):
    monkeypatch.setattr(dialogs.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Traceback\nboom"))
    assert dialogs.pick("file") == {"error": "boom"}


def test_pick_times_out(monkeypatch):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    monkeypatch.setattr(dialogs.subprocess, "run", slow)
    assert dialogs.pick("file") == {"error": "picker timed out"}
