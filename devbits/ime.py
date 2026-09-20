"""Windows input-method (IME) control: stop games from switching input mode.

Playing with Shift, Alt and Space bound to game actions constantly trips the
Windows input-method switches, dropping you into Chinese/Japanese/Korean
composition mid-fight. Three independent mechanisms cause it, and each has to
be shut off in a different place:

* ``Alt+Shift`` / ``Ctrl+Shift`` switch the input *language* — a hotkey stored
  under ``HKCU\\Keyboard Layout\\Toggle``.
* ``Ctrl+Space`` / ``Shift+Space`` toggle the IME and full-width mode — system
  hotkeys under ``HKCU\\Control Panel\\Input Method\\Hot Keys``.
* A bare ``Shift`` toggles Chinese/English *inside* the IME. That is the IME's
  own conversion mode, not a registry hotkey, so nothing in the registry can
  disable it.

:func:`disable_hotkeys` handles the first two by editing the registry, which is
permanent but only takes effect at the next sign-in: the settings are read by
``ctfmon.exe``, which is a protected process that cannot be restarted.

:func:`start` handles all three immediately, by running a background watcher
that forces the foreground window back to the English keyboard layout. A window
whose layout carries no IME has no conversion mode to toggle, so the bare-Shift
problem disappears along with the rest. The watcher only uses ``PostMessage``
and ``SendMessageTimeout`` against another process's windows — no DLL
injection, no keyboard hooks, nothing an anti-cheat driver objects to.

For the cases where even that is not enough (exclusive-fullscreen games that
ignore posted messages), ``strict`` mode drops every non-English language from
the user's input list for the duration, which no keystroke can undo.
"""

from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "GuardState",
    "ImeError",
    "Status",
    "UnsupportedPlatform",
    "disable_hotkeys",
    "hotkey_report",
    "is_running",
    "languages",
    "log_path",
    "restore_hotkeys",
    "restore_languages",
    "start",
    "state_dir",
    "status",
    "stop",
]

IS_WINDOWS = platform.system() == "Windows"

#: Language identifier forced onto the foreground window. 0x0409 is en-US,
#: the layout every Windows install can fall back to.
DEFAULT_LAYOUT = 0x0409

_NOT_WINDOWS = (
    "Input-method control is only implemented on Windows. The switches it "
    "disables (Alt+Shift, Ctrl+Space, and the IME's own Shift toggle) are "
    "Windows input-stack features with no equivalent elsewhere."
)

#: Attempts on one window before it is treated as unreachable. Windows' UIPI
#: silently drops messages posted to a higher-integrity window, so a game
#: running as Administrator can never be switched by a watcher that isn't --
#: without this the loop would retry it forever at every interval.
_GIVE_UP_AFTER = 5

#: How long an unreachable window is left alone before being tried again.
_RETRY_BLOCKED_AFTER = 60.0

_TOGGLE_KEY = r"Keyboard Layout\Toggle"
_HOTKEY_ROOT = r"Control Panel\Input Method\Hot Keys"

#: The three ``Toggle`` values, and the "unassigned" setting Windows uses.
_TOGGLE_NAMES = ("Hotkey", "Language Hotkey", "Layout Hotkey")
_TOGGLE_OFF = "3"

#: Virtual-key and modifier codes, for rendering a hotkey as text.
_VK_NAMES = {0x20: "Space", 0xBC: ",", 0xBE: ".", 0x30: "0",
             0x47: "G", 0x4B: "K", 0x4C: "L", 0x56: "V"}
_MOD_NAMES = {1: "Alt", 2: "Ctrl", 3: "Alt+Ctrl", 4: "Shift", 6: "Ctrl+Shift"}


class ImeError(RuntimeError):
    """An input-method operation failed or isn't supported here."""


class UnsupportedPlatform(ImeError):
    """This machine is not running Windows."""


def _require_windows() -> None:
    if not IS_WINDOWS:
        raise UnsupportedPlatform(_NOT_WINDOWS)


# ---------------------------------------------------------------------------
# State files
# ---------------------------------------------------------------------------

def state_dir() -> Path:
    """Where the watcher's state, backups and log live."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "devbits" / "ime"


def _state_file() -> Path:
    return state_dir() / "state.json"


def _runtime_file() -> Path:
    return state_dir() / "runtime.json"


def _hotkey_backup() -> Path:
    return state_dir() / "hotkeys.json"


def _language_backup() -> Path:
    return state_dir() / "languages.json"


def log_path() -> Path:
    """The watcher's log file."""
    return state_dir() / "guard.log"


def _read_json(path: Path) -> dict | None:
    # utf-8-sig, not utf-8: PowerShell's "Set-Content -Encoding UTF8" writes a
    # BOM, and a leading ﻿ makes json.loads fail.
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Win32 bindings
# ---------------------------------------------------------------------------

_WM_INPUTLANGCHANGEREQUEST = 0x0050
_WM_IME_CONTROL = 0x0283
_IMC_SETCONVERSIONMODE = 0x0002
_IMC_SETOPENSTATUS = 0x0006
_SMTO_ABORTIFHUNG = 0x0002
_KLF_SUBSTITUTE_OK = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _Win32:
    """Lazily bound user32/imm32/kernel32 entry points.

    Bound on first use rather than at import so that this module still imports
    (and the CLI can print a clean "Windows only" error) on macOS and Linux.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        imm32 = ctypes.WinDLL("imm32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # HWND and HKL are pointer-sized; declaring them keeps 64-bit handles
        # from being truncated to 32 bits.
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
        user32.GetKeyboardLayout.restype = wintypes.HKL
        user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(wintypes.HKL)]
        user32.GetKeyboardLayoutList.restype = ctypes.c_int
        user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
        user32.LoadKeyboardLayoutW.restype = wintypes.HKL
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
        ]
        imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
        imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)
        ] * 4
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        self.user32 = user32
        self.imm32 = imm32
        self.kernel32 = kernel32


_win32: _Win32 | None = None


def _w() -> _Win32:
    global _win32
    _require_windows()
    if _win32 is None:
        _win32 = _Win32()
    return _win32


def _english_hkl(langid: int = DEFAULT_LAYOUT) -> int:
    """The HKL for ``langid``, loading the layout if it isn't active yet."""
    w = _w()
    buf = (w.wintypes.HKL * 64)()
    count = w.user32.GetKeyboardLayoutList(64, buf)
    for i in range(count):
        hkl = buf[i] or 0
        if (hkl & 0xFFFF) == langid:
            return hkl
    hkl = w.user32.LoadKeyboardLayoutW(f"{langid:08x}", _KLF_SUBSTITUTE_OK)
    if not hkl:
        raise ImeError(f"No keyboard layout for language id 0x{langid:04X} could be loaded.")
    return hkl


def _process_name(pid: int) -> str | None:
    """Executable name for ``pid``, without the ``.exe`` suffix."""
    w = _w()
    handle = w.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = w.wintypes.DWORD(260)
        buf = w.ctypes.create_unicode_buffer(size.value)
        if not w.kernel32.QueryFullProcessImageNameW(handle, 0, buf, w.ctypes.byref(size)):
            return None
        return Path(buf.value).stem.lower()
    finally:
        w.kernel32.CloseHandle(handle)


def _process_started_at(pid: int) -> int | None:
    """Creation time of ``pid`` as a FILETIME integer, or ``None`` if gone.

    Recorded alongside the pid so that :func:`stop` cannot kill an unrelated
    process that happened to be handed the same pid after the watcher exited.
    """
    w = _w()
    handle = w.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = w.wintypes.FILETIME()
        rest = [w.wintypes.FILETIME() for _ in range(3)]
        ok = w.kernel32.GetProcessTimes(
            handle, w.ctypes.byref(creation),
            *(w.ctypes.byref(f) for f in rest),
        )
        if not ok:
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        w.kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# PowerShell bridge (input language list)
# ---------------------------------------------------------------------------

def _powershell(script: str) -> None:
    """Run ``script`` under Windows PowerShell, raising on failure.

    Output is never parsed from the console: a Traditional Chinese Windows
    prints cp950, so anything structured is exchanged through a UTF-8 temp
    file instead.
    """
    _require_windows()
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or "no output"
        raise ImeError(f"PowerShell call failed: {detail}")


def languages() -> list[dict]:
    """The user's input languages, each as ``{"tag": str, "tips": [str, ...]}``.

    ``tips`` are TSF input-method profile ids ("0404:{GUID}{GUID}"), which is
    what ``Set-WinUserLanguageList`` needs to restore a language *with* its
    input methods rather than just the bare keyboard layout.
    """
    _require_windows()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "langs.json"
        # Note: piping Get-WinUserLanguageList directly hands the whole List
        # to the next stage as one object, so assign it before iterating.
        _powershell(
            "$langs = Get-WinUserLanguageList; $out = @(); "
            "foreach ($l in $langs) { $out += ,@{ tag = $l.LanguageTag; "
            "tips = @($l.InputMethodTips) } }; "
            f"@{{ languages = $out }} | ConvertTo-Json -Depth 5 | "
            f"Set-Content -LiteralPath '{out}' -Encoding UTF8"
        )
        data = _read_json(out) or {}
    entries = data.get("languages") or []
    if isinstance(entries, dict):  # ConvertTo-Json collapses a single entry
        entries = [entries]
    return [{"tag": e.get("tag"), "tips": list(e.get("tips") or [])} for e in entries]


def _set_languages(entries: list[dict]) -> None:
    """Replace the input language list with ``entries``."""
    if not entries or not entries[0].get("tag"):
        raise ImeError("Refusing to apply an empty input language list.")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "langs.json"
        src.write_text(json.dumps({"languages": entries}), encoding="utf-8")
        # Calling a cmdlet first forces the International module to load;
        # without it the WinUserLanguage type does not exist yet and building
        # the list fails with TypeNotFound.
        _powershell(
            f"$data = @((Get-Content -LiteralPath '{src}' -Raw -Encoding UTF8 | "
            "ConvertFrom-Json).languages); "
            "$null = Get-WinUserLanguageList; "
            "$list = New-WinUserLanguageList -Language $data[0].tag; "
            "for ($i = 1; $i -lt $data.Count; $i++) "
            "{ $list.Add((New-WinUserLanguageList -Language $data[$i].tag)[0]) }; "
            "for ($i = 0; $i -lt $data.Count; $i++) { $list[$i].InputMethodTips.Clear(); "
            "foreach ($t in @($data[$i].tips)) { [void]$list[$i].InputMethodTips.Add($t) } }; "
            "Set-WinUserLanguageList -LanguageList $list -Force"
        )


def restore_languages() -> list[str]:
    """Put back the input languages saved by the last ``strict`` start."""
    backup = _read_json(_language_backup())
    entries = (backup or {}).get("languages") or []
    if not entries:
        raise ImeError(
            f"No input language backup to restore from ({_language_backup()})."
        )
    _set_languages(entries)
    return [e["tag"] for e in entries]


def _enter_english_only() -> None:
    saved = languages()
    if not saved:
        raise ImeError("Could not read the current input language list.")
    _write_json(_language_backup(), {"languages": saved})
    _set_languages([{"tag": "en-US", "tips": ["0409:00000409"]}])


# ---------------------------------------------------------------------------
# Registry hotkeys
# ---------------------------------------------------------------------------

def _hotkey_subkeys() -> list[str]:
    import winreg

    names = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HOTKEY_ROOT) as key:
            i = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, i))
                except OSError:
                    break
                i += 1
    except FileNotFoundError:
        pass
    return names


def _key_values(path: str) -> dict[str, tuple]:
    import winreg

    values: dict[str, tuple] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            i = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, i)
                except OSError:
                    break
                values[name] = (value, kind)
                i += 1
    except FileNotFoundError:
        pass
    return values


def _encode(value, kind: int):
    import winreg

    return value.hex() if kind == winreg.REG_BINARY else value


def _decode(value, kind: int):
    import winreg

    return bytes.fromhex(value) if kind == winreg.REG_BINARY else value


def _backup_hotkeys() -> None:
    """Snapshot the hotkey registry, unless a snapshot already exists.

    Never overwritten: a second ``disable-hotkeys`` must not capture the
    already-disabled state as if it were the user's original settings.
    """
    if _hotkey_backup().exists():
        return
    toggle = {n: [_encode(v, k), k] for n, (v, k) in _key_values(_TOGGLE_KEY).items()}
    hotkeys = {}
    for sub in _hotkey_subkeys():
        values = _key_values(f"{_HOTKEY_ROOT}\\{sub}")
        hotkeys[sub] = {n: [_encode(v, k), k] for n, (v, k) in values.items()}
    _write_json(_hotkey_backup(), {"toggle": toggle, "hotkeys": hotkeys})


@dataclass
class HotkeyReport:
    """What the input-switching hotkeys are currently set to."""

    toggle: dict[str, str] = field(default_factory=dict)
    toggle_disabled: bool = False
    live: list[str] = field(default_factory=list)
    total: int = 0
    backed_up: bool = False


def hotkey_report() -> HotkeyReport:
    """Read back the current hotkey configuration."""
    _require_windows()
    toggle = {n: str(v) for n, (v, _k) in _key_values(_TOGGLE_KEY).items()}
    # With no values at all, Windows falls back to its default, which has
    # Alt+Shift switching languages -- that is "enabled", not "unset".
    disabled = bool(toggle) and all(
        str(toggle.get(n, "")) == _TOGGLE_OFF for n in _TOGGLE_NAMES
    )

    live, total = [], 0
    for sub in _hotkey_subkeys():
        total += 1
        values = _key_values(f"{_HOTKEY_ROOT}\\{sub}")
        vk = values.get("Virtual Key", (b"", 0))[0]
        mod = values.get("Key Modifiers", (b"", 0))[0]
        if not isinstance(vk, bytes) or not vk or vk[0] == 0:
            continue
        key = _VK_NAMES.get(vk[0], f"VK{vk[0]}")
        name = _MOD_NAMES.get(mod[0] if isinstance(mod, bytes) and mod else 0, "")
        live.append(f"{name}+{key}" if name else key)

    return HotkeyReport(
        toggle=toggle,
        toggle_disabled=disabled,
        live=live,
        total=total,
        backed_up=_hotkey_backup().exists(),
    )


def disable_hotkeys() -> int:
    """Turn off every registry-level input-switching hotkey.

    Returns the number of IME hotkey entries cleared. The change is permanent
    but only applies at the next sign-in, because ``ctfmon.exe`` caches these
    settings and is a protected process that cannot be restarted.
    """
    _require_windows()
    import winreg

    _backup_hotkeys()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _TOGGLE_KEY) as key:
        for name in _TOGGLE_NAMES:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, _TOGGLE_OFF)

    cleared = 0
    zero = bytes(4)
    for sub in _hotkey_subkeys():
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{_HOTKEY_ROOT}\\{sub}",
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "Virtual Key", 0, winreg.REG_BINARY, zero)
            winreg.SetValueEx(key, "Key Modifiers", 0, winreg.REG_BINARY, zero)
        cleared += 1
    return cleared


def restore_hotkeys() -> int:
    """Put the hotkey registry back exactly as :func:`disable_hotkeys` found it."""
    _require_windows()
    import winreg

    data = _read_json(_hotkey_backup())
    if not data:
        raise ImeError(f"No hotkey backup to restore from ({_hotkey_backup()}).")

    # Recreate rather than merge: the original may have had no values at all,
    # and leaving "3" behind would silently keep the hotkeys disabled.
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _TOGGLE_KEY)
    except FileNotFoundError:
        pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _TOGGLE_KEY) as key:
        for name, (value, kind) in data.get("toggle", {}).items():
            winreg.SetValueEx(key, name, 0, kind, _decode(value, kind))

    restored = 0
    for sub, values in data.get("hotkeys", {}).items():
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{_HOTKEY_ROOT}\\{sub}") as key:
            for name, (value, kind) in values.items():
                winreg.SetValueEx(key, name, 0, kind, _decode(value, kind))
        restored += 1
    return restored


# ---------------------------------------------------------------------------
# The watcher
# ---------------------------------------------------------------------------

@dataclass
class GuardState:
    """The running watcher, as recorded when it was started."""

    pid: int
    started_at: float
    strict: bool = False
    apps: list[str] = field(default_factory=list)
    interval_ms: int = 300
    layout: int = DEFAULT_LAYOUT
    created: int | None = None

    @property
    def uptime(self) -> float:
        return max(0.0, time.time() - self.started_at)


def _load_state() -> GuardState | None:
    data = _read_json(_state_file())
    if not data:
        return None
    try:
        return GuardState(**data)
    except TypeError:
        return None


def is_running() -> GuardState | None:
    """The live watcher, or ``None`` if it isn't running."""
    if not IS_WINDOWS:
        return None
    state = _load_state()
    if state is None:
        return None
    # A matching creation time proves this is still our process and not a
    # stranger that inherited the pid.
    if state.created is not None and _process_started_at(state.pid) != state.created:
        return None
    if state.created is None and _process_started_at(state.pid) is None:
        return None
    return state


def is_elevated() -> bool:
    """Whether this process is running with administrator rights.

    Matters because Windows' UIPI stops a normal process from posting messages
    to a window owned by an elevated one, which is how most anti-cheat games
    run. The watcher has to match or exceed the game's integrity level.
    """
    if not IS_WINDOWS:
        return False
    try:
        import ctypes

        return bool(ctypes.WinDLL("shell32").IsUserAnAdmin())
    except OSError:
        return False


@dataclass
class Status:
    """Everything ``devbits ime status`` reports."""

    guard: GuardState | None
    fixes: int = 0
    last_fix: str | None = None
    last_process: str | None = None
    blocked: list[str] = field(default_factory=list)
    languages: list[dict] = field(default_factory=list)
    hotkeys: HotkeyReport = field(default_factory=HotkeyReport)
    elevated: bool = False


def status() -> Status:
    """Collect the watcher, language and hotkey state in one shot."""
    _require_windows()
    runtime = _read_json(_runtime_file()) or {}
    return Status(
        guard=is_running(),
        fixes=int(runtime.get("fixes") or 0),
        last_fix=runtime.get("last_fix"),
        last_process=runtime.get("last_process"),
        blocked=list(runtime.get("blocked") or []),
        languages=languages(),
        hotkeys=hotkey_report(),
        elevated=is_elevated(),
    )


def _daemon_command(interval_ms: int, apps: list[str], layout: int) -> list[str]:
    """Interpreter command line that runs this module as the watcher."""
    exe = Path(sys.executable)
    # pythonw.exe runs without a console window at all; plain python.exe with
    # CREATE_NO_WINDOW is the fallback when it isn't installed beside it.
    windowless = exe.with_name("pythonw.exe")
    interpreter = windowless if windowless.exists() else exe
    cmd = [str(interpreter), "-m", "devbits.ime", "--daemon",
           "--interval", str(interval_ms), "--layout", f"{layout:04x}"]
    if apps:
        cmd += ["--apps", ",".join(apps)]
    return cmd


def start(strict: bool = False, apps: list[str] | None = None,
          interval_ms: int = 300, layout: int = DEFAULT_LAYOUT) -> GuardState:
    """Launch the background watcher. Raises if one is already running."""
    _require_windows()
    running = is_running()
    if running is not None:
        raise ImeError(
            f"The input-method watcher is already running (pid {running.pid}). "
            "Use 'devbits ime restart' to apply different options."
        )

    apps = [a.strip().lower().removesuffix(".exe") for a in (apps or []) if a.strip()]
    _english_hkl(layout)  # fail here, in the foreground, if the layout is missing

    if strict:
        _enter_english_only()

    _write_json(_runtime_file(), {"fixes": 0, "last_fix": None, "last_process": None})
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        _daemon_command(interval_ms, apps, layout),
        creationflags=flags,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    time.sleep(0.4)
    if proc.poll() is not None:
        if strict:
            restore_languages()
        raise ImeError(f"The watcher exited immediately. See {log_path()}.")

    state = GuardState(
        pid=proc.pid,
        started_at=time.time(),
        strict=strict,
        apps=apps,
        interval_ms=interval_ms,
        layout=layout,
        created=_process_started_at(proc.pid),
    )
    _write_json(_state_file(), state.__dict__)
    return state


def stop() -> tuple[GuardState | None, list[str]]:
    """Stop the watcher and undo anything ``start`` changed.

    Returns the watcher that was stopped (``None`` if it wasn't running) and
    the input languages put back, if ``strict`` mode was in effect.
    """
    _require_windows()
    running = is_running()
    recorded = _load_state()

    if running is not None:
        try:
            os.kill(running.pid, signal.SIGTERM)
        except OSError as exc:
            raise ImeError(f"Could not stop the watcher (pid {running.pid}): {exc}") from exc

    # The watcher is terminated, not asked to exit, so nothing in it can run
    # cleanup -- undoing strict mode is this side's job.
    restored: list[str] = []
    if recorded is not None and recorded.strict:
        restored = restore_languages()

    _state_file().unlink(missing_ok=True)
    return running, restored


def _guard_loop(interval_ms: int, apps: list[str], layout: int) -> None:
    """Force every foreground window onto an IME-free keyboard layout."""
    w = _w()
    hkl = _english_hkl(layout)
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)

    def note(message: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")

    note(f"watcher started, pid {os.getpid()}, hkl 0x{hkl:X}, "
         f"interval {interval_ms}ms, apps {apps or 'all'}")

    interval = interval_ms / 1000.0
    fixes = 0
    attempt_hwnd = None
    attempts = 0
    blocked: dict[int, float] = {}
    blocked_names: set[str] = set()

    def publish(name: str | None) -> None:
        try:
            _write_json(_runtime_file(), {
                "fixes": fixes,
                "last_fix": time.strftime("%H:%M:%S"),
                "last_process": name,
                "blocked": sorted(blocked_names),
            })
        except OSError:
            pass

    while True:
        time.sleep(interval)
        hwnd = w.user32.GetForegroundWindow()
        if not hwnd:
            continue

        pid = w.wintypes.DWORD()
        tid = w.user32.GetWindowThreadProcessId(hwnd, w.ctypes.byref(pid))
        if not tid:
            continue

        # None means OpenProcess was refused, which by itself says the window
        # belongs to a more privileged process than this watcher.
        name = _process_name(pid.value)
        if apps and (name is None or name not in apps):
            continue

        current = w.user32.GetKeyboardLayout(tid) or 0
        if (current & 0xFFFF) == layout:
            if hwnd == attempt_hwnd:
                attempt_hwnd, attempts = None, 0
            continue

        give_up_at = blocked.get(hwnd)
        if give_up_at is not None:
            if time.monotonic() - give_up_at < _RETRY_BLOCKED_AFTER:
                continue
            del blocked[hwnd]  # periodically give it another chance

        if hwnd == attempt_hwnd:
            attempts += 1
        else:
            attempt_hwnd, attempts = hwnd, 1

        # Posted, not sent: a game that is not pumping messages must not be
        # able to block the watcher.
        w.user32.PostMessageW(hwnd, _WM_INPUTLANGCHANGEREQUEST, 0, hkl)
        ime_wnd = w.imm32.ImmGetDefaultIMEWnd(hwnd)
        if ime_wnd:
            result = w.ctypes.c_size_t()
            for sub_message in (_IMC_SETOPENSTATUS, _IMC_SETCONVERSIONMODE):
                w.user32.SendMessageTimeoutW(
                    ime_wnd, _WM_IME_CONTROL, sub_message, 0,
                    _SMTO_ABORTIFHUNG, 200, w.ctypes.byref(result),
                )

        fixes += 1
        label = name or f"pid {pid.value}"
        if attempts >= _GIVE_UP_AFTER:
            blocked[hwnd] = time.monotonic()
            blocked_names.add(label)
            attempt_hwnd, attempts = None, 0
            note(f"{label} did not switch after {_GIVE_UP_AFTER} attempts; "
                 "it is almost certainly running elevated, which makes Windows "
                 "drop our messages. Re-run from an Administrator terminal, or "
                 "use --strict. Backing off.")
            publish(name)
            continue

        publish(name)
        if fixes <= 50 or fixes % 50 == 0:
            note(f"reset {label}, {fixes} total")


def _daemon_main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="devbits.ime", add_help=False)
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--layout", default=f"{DEFAULT_LAYOUT:04x}")
    parser.add_argument("--apps", default="")
    args = parser.parse_args(argv)

    apps = [a.strip().lower() for a in args.apps.split(",") if a.strip()]
    try:
        _guard_loop(args.interval, apps, int(args.layout, 16))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # the watcher has no console; leave a trace
        try:
            with log_path().open("a", encoding="utf-8") as handle:
                handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] crashed: {exc!r}\n")
        except OSError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_daemon_main())
