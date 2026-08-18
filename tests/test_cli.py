from __future__ import annotations

import pytest

from devbits.cli import main


def test_help() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_clearcache_dry_run(tmp_path) -> None:
    cache = tmp_path / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "a.pyc").write_bytes(b"test")
    assert main(["clearcache", str(tmp_path), "--dry-run"]) == 0


def test_standalone_wrapper_help() -> None:
    import pytest

    from devbits.scripts import _run

    with pytest.raises(SystemExit) as exc_info:
        _run("clearcache", ["--help"])
    assert exc_info.value.code == 0


def test_netscan_help() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["netscan", "--help"])
    assert exc.value.code == 0


def test_netscan_lists_devices(capsys, monkeypatch) -> None:
    import ipaddress

    from devbits.network import Device

    fake = [
        Device(ip="192.168.0.1", mac="06:f2:67:75:4d:e2", hostname=None, is_gateway=True),
        Device(ip="192.168.0.50", mac="fc:91:5d:76:58:04", hostname="phone.local"),
        Device(ip="192.168.0.203", mac="4e:cc:03:6c:47:f9", hostname=None, is_self=True),
    ]
    monkeypatch.setattr("devbits.cli.scan_network", lambda *a, **k: fake)
    monkeypatch.setattr("devbits.network.default_network", lambda *a, **k: ipaddress.ip_network("192.168.0.0/24"))

    assert main(["netscan", "--no-color", "--no-resolve"]) == 0
    out = capsys.readouterr().out
    assert "192.168.0.1" in out
    assert "06:f2:67:75:4d:e2" in out
    assert "gateway / router" in out
    assert "this device" in out
    assert "Found 3 device(s)." in out


def test_netscan_arp_parsing() -> None:
    from devbits.network import _MAC_RE, _normalize_mac

    line = "? (192.168.0.1) at 6:f2:67:75:4d:e2 on en0 ifscope [ethernet]"
    assert _normalize_mac(_MAC_RE.search(line).group()) == "06:f2:67:75:4d:e2"


def test_arp_table_windows_command(monkeypatch) -> None:
    # Windows arp has no -n flag; arp_table must use "arp -a" there.
    import devbits.network as net

    calls = []

    class _Result:
        returncode = 0
        stdout = "  192.168.0.1           aa-bb-cc-dd-ee-ff     dynamic"

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Result()

    monkeypatch.setattr(net.platform, "system", lambda: "Windows")
    monkeypatch.setattr(net.subprocess, "run", fake_run)
    table = net.arp_table()
    assert calls == [["arp", "-a"]]
    assert table == {"192.168.0.1": "aa:bb:cc:dd:ee:ff"}


def test_arp_table_linux_falls_back_to_ip_neigh(monkeypatch) -> None:
    # When net-tools `arp` is missing, arp_table must fall back to `ip neigh`.
    import devbits.network as net

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "arp":
            raise FileNotFoundError("arp not installed")

        class _Result:
            returncode = 0
            stdout = "192.168.0.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE"

        return _Result()

    monkeypatch.setattr(net.platform, "system", lambda: "Linux")
    monkeypatch.setattr(net.subprocess, "run", fake_run)
    table = net.arp_table()
    assert calls == [["arp", "-an"], ["ip", "neigh", "show"]]
    assert table == {"192.168.0.1": "aa:bb:cc:dd:ee:ff"}


def test_subprocess_output_survives_non_utf8_locale(monkeypatch) -> None:
    # A Traditional Chinese Windows prints cp950 bytes, which are not valid
    # UTF-8. Decoding must not raise, and ping must still detect the reply.
    import subprocess

    import devbits.network as net

    # "回覆自 192.168.50.138: 位元組=32 時間<1ms TTL=64" in cp950.
    cp950 = (
        "回覆自 192.168.50.138: 位元組=32 時間<1ms TTL=64".encode("cp950")
    )

    def fake_run(cmd, **kwargs):
        assert not kwargs.get("text"), "text=True decodes as UTF-8 and crashes here"
        return subprocess.CompletedProcess(cmd, 0, cp950, b"")

    monkeypatch.setattr(net.platform, "system", lambda: "Windows")
    monkeypatch.setattr(net.subprocess, "run", fake_run)
    net._console_encoding.cache_clear()
    monkeypatch.setattr(net, "_console_encoding", lambda: "cp950")

    assert net._ping("192.168.50.138") is True


def test_ping_tolerates_undecodable_bytes(monkeypatch) -> None:
    # Even if the code page guess is wrong, no byte sequence may raise.
    import subprocess

    import devbits.network as net

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, b"\xa4\xa8 TTL=64 \xff\xfe", b"")

    monkeypatch.setattr(net.platform, "system", lambda: "Windows")
    monkeypatch.setattr(net.subprocess, "run", fake_run)
    net._console_encoding.cache_clear()
    monkeypatch.setattr(net, "_console_encoding", lambda: "utf-8")

    assert net._ping("192.168.50.138") is True


def test_private_mac_detection() -> None:
    from devbits.network import is_private_mac

    assert is_private_mac("4e:cc:03:6c:47:f9")  # locally administered (0x02 bit set)
    assert is_private_mac("06:f2:67:75:4d:e2")
    assert not is_private_mac("fc:91:5d:76:58:04")  # globally unique OUI
    assert not is_private_mac("b8:27:eb:11:22:33")


def test_netscan_lookup_column(capsys, monkeypatch) -> None:
    import ipaddress

    import devbits.network as net
    from devbits.network import scan_network

    monkeypatch.setattr("devbits.cli.scan_network", scan_network)
    monkeypatch.setattr(net, "_ping", lambda ip, timeout=1.0: False)
    monkeypatch.setattr(net, "arp_table", lambda: {
        "192.168.0.1": "fc:91:5d:76:58:04",
        "192.168.0.203": "4e:cc:03:6c:47:f9",
    })
    monkeypatch.setattr(net, "local_ip", lambda: "192.168.0.203")
    monkeypatch.setattr(net, "gateway_ip", lambda: "192.168.0.1")
    monkeypatch.setattr(net, "default_network", lambda *a, **k: ipaddress.ip_network("192.168.0.0/24"))
    monkeypatch.setattr(net, "lookup_vendor", lambda mac, **k: "Google, Inc.")

    assert main(["netscan", "--no-color", "--no-resolve", "--lookup"]) == 0
    out = capsys.readouterr().out
    assert "VENDOR" in out
    assert "Google, Inc." in out
    assert "(private)" in out  # randomized MAC of the self device
