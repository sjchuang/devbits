from __future__ import annotations

import json

import pytest

from devbits import ime
from devbits.cli import main


def test_help() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["ime", "--help"])
    assert exc.value.code == 0


@pytest.mark.parametrize(
    "action",
    ["status", "start", "stop", "restart", "disable-hotkeys", "restore-hotkeys",
     "restore-languages"],
)
def test_every_action_has_help(action: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["ime", action, "--help"])
    assert exc.value.code == 0


def test_standalone_wrapper_help() -> None:
    from devbits.scripts import _run

    with pytest.raises(SystemExit) as exc:
        _run("ime", ["--help"])
    assert exc.value.code == 0


def test_action_is_required() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["ime"])
    assert exc.value.code != 0


def test_non_windows_reports_cleanly(capsys, monkeypatch) -> None:
    # The module must import anywhere; only using it is Windows-only.
    monkeypatch.setattr(ime, "IS_WINDOWS", False)
    assert main(["ime", "status", "--no-color"]) == 1
    assert "only implemented on Windows" in capsys.readouterr().err


def test_is_running_is_none_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(ime, "IS_WINDOWS", False)
    assert ime.is_running() is None


def test_state_dir_follows_localappdata(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert ime.state_dir() == tmp_path / "devbits" / "ime"
    assert ime.log_path().parent == ime.state_dir()


def test_apps_are_normalized(monkeypatch, tmp_path) -> None:
    # "Valorant.EXE" and "valorant" must both match the process name the
    # watcher reads back, which is lowercase and suffix-free.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    monkeypatch.setattr(ime, "is_running", lambda: None)
    monkeypatch.setattr(ime, "_english_hkl", lambda layout=ime.DEFAULT_LAYOUT: 0x4090409)
    monkeypatch.setattr(ime, "_process_started_at", lambda pid: 123)

    class _Proc:
        pid = 4321

        def poll(self):
            return None

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(ime.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ime.time, "sleep", lambda s: None)

    state = ime.start(apps=["Valorant.EXE", " cs2 ", ""])
    assert state.apps == ["valorant", "cs2"]
    assert "--apps" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--apps") + 1] == "valorant,cs2"

    saved = json.loads((ime.state_dir() / "state.json").read_text(encoding="utf-8"))
    assert saved["pid"] == 4321
    assert saved["created"] == 123


def test_start_refuses_when_already_running(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    monkeypatch.setattr(
        ime, "is_running",
        lambda: ime.GuardState(pid=999, started_at=0.0),
    )
    with pytest.raises(ime.ImeError, match="already running"):
        ime.start()


def test_stop_restores_languages_even_though_it_kills(monkeypatch, tmp_path) -> None:
    # The watcher is terminated, so it can never run its own cleanup --
    # stop() has to undo strict mode itself.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    state = ime.GuardState(pid=555, started_at=0.0, strict=True, created=7)
    ime._write_json(ime._state_file(), state.__dict__)
    monkeypatch.setattr(ime, "is_running", lambda: state)
    monkeypatch.setattr(ime.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(ime, "restore_languages", lambda: ["zh-Hant-TW", "en-US"])

    stopped, restored = ime.stop()
    assert stopped is state
    assert restored == ["zh-Hant-TW", "en-US"]
    assert not ime._state_file().exists()


def test_stop_restores_languages_when_process_already_gone(monkeypatch, tmp_path) -> None:
    # A crashed or manually killed watcher must not strand the user with
    # their input languages removed.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    state = ime.GuardState(pid=556, started_at=0.0, strict=True, created=7)
    ime._write_json(ime._state_file(), state.__dict__)
    monkeypatch.setattr(ime, "is_running", lambda: None)
    monkeypatch.setattr(ime, "restore_languages", lambda: ["zh-Hant-TW"])

    stopped, restored = ime.stop()
    assert stopped is None
    assert restored == ["zh-Hant-TW"]


def test_set_languages_refuses_empty_list(monkeypatch) -> None:
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    with pytest.raises(ime.ImeError, match="empty input language list"):
        ime._set_languages([])


def test_restore_languages_without_backup(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with pytest.raises(ime.ImeError, match="No input language backup"):
        ime.restore_languages()


def test_restore_hotkeys_without_backup(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    with pytest.raises(ime.ImeError, match="No hotkey backup"):
        ime.restore_hotkeys()


def test_hotkey_backup_is_not_overwritten(monkeypatch, tmp_path) -> None:
    # Running disable-hotkeys twice must not capture the disabled state as
    # if it were the user's original settings.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    original = {"toggle": {"Hotkey": ["1", 1]}, "hotkeys": {}}
    ime._write_json(ime._hotkey_backup(), original)
    monkeypatch.setattr(ime, "_key_values", lambda path: {"Hotkey": ("3", 1)})
    monkeypatch.setattr(ime, "_hotkey_subkeys", lambda: [])

    ime._backup_hotkeys()
    assert json.loads(ime._hotkey_backup().read_text(encoding="utf-8")) == original


def test_unset_toggle_counts_as_enabled(monkeypatch) -> None:
    # No values at all means Windows uses its default, where Alt+Shift still
    # switches -- reporting that as "disabled" would be wrong.
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    monkeypatch.setattr(ime, "_key_values", lambda path: {})
    monkeypatch.setattr(ime, "_hotkey_subkeys", lambda: [])
    assert ime.hotkey_report().toggle_disabled is False


def test_toggle_disabled_needs_all_three(monkeypatch) -> None:
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    monkeypatch.setattr(ime, "_hotkey_subkeys", lambda: [])

    monkeypatch.setattr(ime, "_key_values", lambda path: {
        "Hotkey": ("3", 1), "Language Hotkey": ("3", 1), "Layout Hotkey": ("3", 1),
    })
    assert ime.hotkey_report().toggle_disabled is True

    monkeypatch.setattr(ime, "_key_values", lambda path: {
        "Hotkey": ("3", 1), "Language Hotkey": ("2", 1), "Layout Hotkey": ("3", 1),
    })
    assert ime.hotkey_report().toggle_disabled is False


def test_hotkey_report_renders_live_hotkeys(monkeypatch) -> None:
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    monkeypatch.setattr(ime, "_hotkey_subkeys", lambda: ["00000010", "00000070"])

    def fake_values(path: str):
        if path.endswith("00000010"):  # Ctrl+Space, live
            return {"Virtual Key": (b"\x20\x00\x00\x00", 3),
                    "Key Modifiers": (b"\x02\xc0\x00\x00", 3)}
        if path.endswith("00000070"):  # cleared by disable-hotkeys
            return {"Virtual Key": (bytes(4), 3), "Key Modifiers": (bytes(4), 3)}
        return {}

    monkeypatch.setattr(ime, "_key_values", fake_values)
    report = ime.hotkey_report()
    assert report.total == 2
    assert report.live == ["Ctrl+Space"]


def test_languages_reads_a_single_entry(monkeypatch, tmp_path) -> None:
    # ConvertTo-Json emits an object, not a list, when there is one language.
    monkeypatch.setattr(ime, "IS_WINDOWS", True)

    def fake_powershell(script: str) -> None:
        path = script.split("-LiteralPath '")[1].split("'")[0]
        payload = {"languages": {"tag": "en-US", "tips": ["0409:00000409"]}}
        Pathlike = type(tmp_path)
        Pathlike(path).write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(ime, "_powershell", fake_powershell)
    assert ime.languages() == [{"tag": "en-US", "tips": ["0409:00000409"]}]


def test_is_elevated_is_false_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(ime, "IS_WINDOWS", False)
    assert ime.is_elevated() is False


def test_status_reports_blocked_windows(monkeypatch, tmp_path, capsys) -> None:
    # A window the watcher could not switch has to reach the user, with the
    # reason -- otherwise "running, 900 resets" looks like it is working.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    guard = ime.GuardState(pid=42, started_at=0.0)
    monkeypatch.setattr(ime, "is_running", lambda: guard)
    monkeypatch.setattr(ime, "languages", lambda: [])
    monkeypatch.setattr(ime, "hotkey_report", ime.HotkeyReport)
    monkeypatch.setattr(ime, "is_elevated", lambda: False)
    ime._write_json(ime._runtime_file(), {
        "fixes": 5, "last_fix": "10:00:00", "last_process": None,
        "blocked": ["deskflow-core"],
    })

    assert main(["ime", "status", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "could not switch: deskflow-core" in out
    assert "Administrator" in out


def test_read_json_strips_powershell_bom(tmp_path) -> None:
    # Set-Content -Encoding UTF8 writes a BOM; plain utf-8 decoding leaves
    # ﻿ in front and json.loads fails.
    path = tmp_path / "langs.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"languages": []}')
    assert ime._read_json(path) == {"languages": []}


def test_watcher_backs_off_on_a_window_it_cannot_switch(monkeypatch, tmp_path) -> None:
    # A window owned by an elevated process never changes layout, because
    # UIPI drops the posted message. Without a back-off the loop would retry
    # it at every interval forever.
    from types import SimpleNamespace

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    monkeypatch.setattr(ime, "_english_hkl", lambda layout=ime.DEFAULT_LAYOUT: 0x4090409)
    monkeypatch.setattr(ime, "_process_name", lambda pid: None)  # OpenProcess refused

    class _DWORD:
        def __init__(self, value: int = 0) -> None:
            self.value = value

    class _Stop(Exception):
        pass

    ticks = {"n": 0}

    def fake_sleep(seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] > 30:
            raise _Stop

    monkeypatch.setattr(ime.time, "sleep", fake_sleep)

    posts = []

    def thread_and_pid(hwnd, ref):
        ref.value = 6180
        return 777

    fake = SimpleNamespace(
        ctypes=SimpleNamespace(byref=lambda x: x, c_size_t=lambda: 0),
        wintypes=SimpleNamespace(DWORD=_DWORD),
        user32=SimpleNamespace(
            GetForegroundWindow=lambda: 4242,
            GetWindowThreadProcessId=thread_and_pid,
            GetKeyboardLayout=lambda tid: 0x04040404,  # stays Chinese, always
            PostMessageW=lambda *a: (posts.append(a), True)[1],
            SendMessageTimeoutW=lambda *a: 1,
        ),
        imm32=SimpleNamespace(ImmGetDefaultIMEWnd=lambda hwnd: 0),
    )
    monkeypatch.setattr(ime, "_w", lambda: fake)

    with pytest.raises(_Stop):
        ime._guard_loop(300, [], 0x0409)

    # 30 ticks, but it stops trying after _GIVE_UP_AFTER.
    assert len(posts) == ime._GIVE_UP_AFTER
    assert "did not switch" in ime.log_path().read_text(encoding="utf-8")
    runtime = json.loads(ime._runtime_file().read_text(encoding="utf-8"))
    assert runtime["blocked"] == ["pid 6180"]


def test_watcher_keeps_working_after_a_window_switches(monkeypatch, tmp_path) -> None:
    # The back-off must be per-window, not a global latch.
    from types import SimpleNamespace

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ime, "IS_WINDOWS", True)
    monkeypatch.setattr(ime, "_english_hkl", lambda layout=ime.DEFAULT_LAYOUT: 0x4090409)
    monkeypatch.setattr(ime, "_process_name", lambda pid: "game")

    class _DWORD:
        def __init__(self, value: int = 0) -> None:
            self.value = value

    class _Stop(Exception):
        pass

    state = {"tick": 0, "layout": 0x04040404}

    def fake_sleep(seconds: float) -> None:
        state["tick"] += 1
        if state["tick"] > 12:
            raise _Stop

    monkeypatch.setattr(ime.time, "sleep", fake_sleep)

    posts = []

    def post(hwnd, msg, wparam, lparam):
        posts.append(hwnd)
        state["layout"] = 0x04090409  # the game honors it, as a normal app does
        return True

    fake = SimpleNamespace(
        ctypes=SimpleNamespace(byref=lambda x: x, c_size_t=lambda: 0),
        wintypes=SimpleNamespace(DWORD=_DWORD),
        user32=SimpleNamespace(
            GetForegroundWindow=lambda: 99,
            GetWindowThreadProcessId=lambda hwnd, ref: 1,
            GetKeyboardLayout=lambda tid: state["layout"],
            PostMessageW=post,
            SendMessageTimeoutW=lambda *a: 1,
        ),
        imm32=SimpleNamespace(ImmGetDefaultIMEWnd=lambda hwnd: 0),
    )
    monkeypatch.setattr(ime, "_w", lambda: fake)

    with pytest.raises(_Stop):
        ime._guard_loop(300, [], 0x0409)

    assert len(posts) == 1  # one reset, then nothing to do
    runtime = json.loads(ime._runtime_file().read_text(encoding="utf-8"))
    assert runtime["fixes"] == 1
    assert runtime["blocked"] == []
