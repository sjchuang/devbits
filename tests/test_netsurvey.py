from __future__ import annotations

import ipaddress

import pytest

from devbits import network as net
from devbits.cli import main


# ---------------------------------------------------------------------------
# netmask / address helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("255.255.255.0", 24),
        ("0xffffff00", 24),  # BSD ifconfig prints hex
        ("255.255.0.0", 16),
        ("255.255.255.255", 32),
        ("24", 24),
        ("192.168.0.10", None),  # a host address is not a contiguous mask
        ("255.0.255.0", None),
        ("nonsense", None),
    ],
)
def test_mask_prefix(text, expected) -> None:
    assert net._mask_prefix(text) == expected


def test_host_count_point_to_point() -> None:
    # A /32 VPN interface holds one host, not "num_addresses - 2" = -1.
    assert net.host_count(ipaddress.ip_network("10.2.0.5/32")) == 1
    assert net.host_count(ipaddress.ip_network("10.2.0.4/31")) == 2
    assert net.host_count(ipaddress.ip_network("192.168.0.0/24")) == 254


def test_address_and_mac_filters() -> None:
    assert net.is_host_address("192.168.0.10")
    for noise in ("224.0.0.251", "239.255.255.250", "255.255.255.255", "127.0.0.1", "0.0.0.0", "junk"):
        assert not net.is_host_address(noise)
    assert net.is_hardware_mac("4e:cc:03:6c:47:f9")
    assert not net.is_hardware_mac("ff:ff:ff:ff:ff:ff")
    assert not net.is_hardware_mac("01:00:5e:00:00:fb")


# ---------------------------------------------------------------------------
# interface parsing, per platform
# ---------------------------------------------------------------------------

def test_parse_ifconfig_macos() -> None:
    out = (
        "lo0: flags=8049<UP,LOOPBACK> mtu 16384\n"
        "\tinet 127.0.0.1 netmask 0xff000000\n"
        "en0: flags=8863<UP,BROADCAST,SMART,RUNNING> mtu 1500\n"
        "\tinet 192.168.0.122 netmask 0xffffff00 broadcast 192.168.0.255\n"
        "utun6: flags=8051<UP,POINTOPOINT,RUNNING> mtu 1400\n"
        "\tinet 10.2.244.44 --> 10.2.244.44 netmask 0xffffffff\n"
    )
    assert [(i.name, i.ip, i.prefix) for i in net._parse_ifconfig(out)] == [
        ("en0", "192.168.0.122", 24),
        ("utun6", "10.2.244.44", 32),
    ]


def test_parse_ifconfig_linux_net_tools() -> None:
    out = (
        "eth0      Link encap:Ethernet  HWaddr aa:bb:cc:dd:ee:ff\n"
        "          inet addr:10.1.2.3  Mask:255.255.254.0\n"
    )
    assert [(i.name, i.ip, i.prefix) for i in net._parse_ifconfig(out)] == [("eth0", "10.1.2.3", 23)]


def test_parse_ip_addr_linux() -> None:
    out = (
        "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n"
        "2: eth0    inet 192.168.4.20/22 brd 192.168.7.255 scope global eth0\n"
    )
    assert [(i.name, i.ip, i.prefix) for i in net._parse_ip_addr(out)] == [("eth0", "192.168.4.20", 22)]


def test_parse_ipconfig_windows_localized() -> None:
    # Field labels are localized (here: Traditional Chinese), so the parser must
    # rely on the address/mask ordering, not on the label text.
    out = (
        "Windows IP 設定\n"
        "\n"
        "以太網路訂卡 以太網路:\n"
        "\n"
        "   IPv4 位址 . . . . . . . . . . . . : 192.168.0.50\n"
        "   子網路遽罩 . . . . . . . . . . : 255.255.255.0\n"
        "   預設閘道 . . . . . . . . . . . : 192.168.0.1\n"
        "\n"
        "未知适接器 Tailscale:\n"
        "\n"
        "   IPv4 位址 . . . . . . . . . . . . : 100.64.1.2\n"
        "   子網路遽罩 . . . . . . . . . . : 255.255.255.255\n"
    )
    parsed = [(i.ip, i.prefix) for i in net._parse_ipconfig(out)]
    assert parsed == [("192.168.0.50", 24), ("100.64.1.2", 32)]


# ---------------------------------------------------------------------------
# ARP entries keep duplicates (that is the collision we report)
# ---------------------------------------------------------------------------

def test_arp_entries_keeps_duplicate_ip(monkeypatch) -> None:
    out = (
        "? (192.168.0.1) at 6:f2:67:75:4d:e2 on en0 ifscope [ethernet]\n"
        "? (192.168.0.50) at aa:bb:cc:dd:ee:01 on en0 ifscope [ethernet]\n"
        "? (192.168.0.50) at aa:bb:cc:dd:ee:02 on en1 ifscope [ethernet]\n"
        "? (192.168.0.1) at 6:f2:67:75:4d:e2 on en1 ifscope [ethernet]\n"  # same pair, other iface
    )
    monkeypatch.setattr(net.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(net, "_run_text", lambda cmd, timeout: (0, out))
    entries = net.arp_entries()
    assert entries == [
        ("192.168.0.1", "06:f2:67:75:4d:e2"),
        ("192.168.0.50", "aa:bb:cc:dd:ee:01"),
        ("192.168.0.50", "aa:bb:cc:dd:ee:02"),
    ]
    assert net.arp_table()["192.168.0.1"] == "06:f2:67:75:4d:e2"


# ---------------------------------------------------------------------------
# the survey itself
# ---------------------------------------------------------------------------

@pytest.fixture
def venue(monkeypatch):
    """A shared-venue link: our /24, a squatting foreign /24, a duplicate IP."""
    interfaces = [
        net.Interface("en0", "192.168.0.122", 24),
        net.Interface("en5", "169.254.7.7", 16),
    ]
    arp = [
        ("192.168.0.1", "06:f2:67:75:4d:e2"),
        ("192.168.0.50", "aa:bb:cc:dd:ee:01"),
        ("192.168.0.50", "aa:bb:cc:dd:ee:02"),  # duplicate address
        ("192.168.0.255", "ff:ff:ff:ff:ff:ff"),  # broadcast: must be dropped
        ("224.0.0.251", "01:00:5e:00:00:fb"),  # multicast: must be dropped
        ("10.77.0.9", "de:ad:be:ef:00:01"),  # somebody else's subnet
        ("10.77.0.1", "06:f2:67:75:4d:e2"),  # ... reached through our gateway's MAC
    ]
    monkeypatch.setattr(net, "local_interfaces", lambda: interfaces)
    monkeypatch.setattr(net, "local_ip", lambda: "192.168.0.122")
    monkeypatch.setattr(net, "gateway_ip", lambda: "192.168.0.1")
    monkeypatch.setattr(net, "arp_entries", lambda: arp)
    monkeypatch.setattr(net, "discover_passive", lambda *a, **k: {"10.77.0.9": {"ssdp"}})
    monkeypatch.setattr(net, "_prime_arp_cache", lambda *a, **k: None)
    monkeypatch.setattr(net, "ping_sweep", lambda hosts, **k: set())
    return interfaces


def test_survey_groups_segments_and_drops_noise(venue) -> None:
    survey, _ = net.survey_network(passive=True)
    networks = [str(segment.network) for segment in survey.segments]
    # Local segments first, then foreign ones bucketed by /24.
    assert networks == ["169.254.0.0/16", "192.168.0.0/24", "10.77.0.0/24"]
    local = next(s for s in survey.segments if str(s.network) == "192.168.0.0/24")
    assert local.is_local and local.interfaces == ["en0"]
    assert [d.ip for d in local.devices] == ["192.168.0.1", "192.168.0.50", "192.168.0.122"]
    assert not local.swept
    foreign = next(s for s in survey.segments if str(s.network) == "10.77.0.0/24")
    assert not foreign.is_local
    assert {d.ip for d in foreign.devices} == {"10.77.0.1", "10.77.0.9"}
    # 3 in our /24 + 2 foreign + en5's own self-assigned address.
    assert survey.device_count == 6
    stranded = next(s for s in survey.segments if str(s.network) == "169.254.0.0/16")
    assert [d.ip for d in stranded.devices] == ["169.254.7.7"]


def test_survey_marks_self_and_gateway(venue) -> None:
    survey, _ = net.survey_network()
    local = next(s for s in survey.segments if str(s.network) == "192.168.0.0/24")
    by_ip = {device.ip: device for device in local.devices}
    assert by_ip["192.168.0.1"].is_gateway
    assert by_ip["192.168.0.122"].is_self
    assert "ssdp" in next(
        d for s in survey.segments for d in s.devices if d.ip == "10.77.0.9"
    ).sources


def test_survey_reports_conflicts(venue) -> None:
    survey, _ = net.survey_network()
    kinds = [conflict.kind for conflict in survey.conflicts]
    assert "duplicate-ip" in kinds  # 192.168.0.50 from two MACs
    assert "no-dhcp" in kinds  # en5 stuck on 169.254.7.7
    assert "router-bridge" in kinds  # gateway MAC answers in two segments
    assert "shared-l2" in kinds  # a foreign segment shares the link
    duplicate = next(c for c in survey.conflicts if c.kind == "duplicate-ip")
    assert "192.168.0.50" in duplicate.message


def test_survey_reports_overlap_with_foreign_range(monkeypatch) -> None:
    monkeypatch.setattr(net, "local_interfaces", lambda: [net.Interface("en0", "192.168.0.10", 24)])
    monkeypatch.setattr(net, "local_ip", lambda: "192.168.0.10")
    monkeypatch.setattr(net, "gateway_ip", lambda: None)
    monkeypatch.setattr(net, "arp_entries", lambda: [("192.168.0.77", "aa:bb:cc:dd:ee:01")])
    monkeypatch.setattr(net, "discover_passive", lambda *a, **k: {})
    monkeypatch.setattr(net, "_prime_arp_cache", lambda *a, **k: None)
    # Grouping at /25 puts the neighbour in a range that overlaps our own /24.
    survey, _ = net.survey_network(group_prefix=25)
    assert [c.kind for c in survey.conflicts].count("overlap") == 0  # it lands inside our own segment

    monkeypatch.setattr(net, "local_interfaces", lambda: [
        net.Interface("en0", "192.168.0.10", 24),
        net.Interface("utun3", "192.168.0.200", 24),  # VPN on the same range
    ])
    survey, _ = net.survey_network()
    overlap = next(c for c in survey.conflicts if c.kind == "overlap")
    assert "en0" in overlap.message and "utun3" in overlap.message


def test_survey_sweeps_included_ranges(monkeypatch) -> None:
    swept: list[list[str]] = []
    monkeypatch.setattr(net, "local_interfaces", lambda: [net.Interface("en0", "192.168.0.10", 24)])
    monkeypatch.setattr(net, "local_ip", lambda: "192.168.0.10")
    monkeypatch.setattr(net, "gateway_ip", lambda: None)
    monkeypatch.setattr(net, "arp_entries", lambda: [])
    monkeypatch.setattr(net, "discover_passive", lambda *a, **k: {})
    monkeypatch.setattr(net, "_prime_arp_cache", lambda *a, **k: None)

    def fake_sweep(hosts, **kwargs):
        swept.append(hosts)
        return {hosts[0]}

    monkeypatch.setattr(net, "ping_sweep", fake_sweep)
    survey, notes = net.survey_network(include=["10.9.9.0/29"], passive=False)
    assert swept == [[f"10.9.9.{n}" for n in range(1, 7)]]
    assert notes == []
    segment = next(s for s in survey.segments if str(s.network) == "10.9.9.0/24")
    assert segment.swept is False  # only the /29 inside it was enumerated
    assert [d.ip for d in segment.devices] == ["10.9.9.1"]
    assert "ping" in segment.devices[0].sources


def test_survey_refuses_oversized_sweep(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(net, "local_interfaces", lambda: [net.Interface("en0", "10.0.5.7", 8)])
    monkeypatch.setattr(net, "local_ip", lambda: "10.0.5.7")
    monkeypatch.setattr(net, "gateway_ip", lambda: None)
    monkeypatch.setattr(net, "arp_entries", lambda: [])
    monkeypatch.setattr(net, "discover_passive", lambda *a, **k: {})
    monkeypatch.setattr(net, "_prime_arp_cache", lambda *a, **k: None)
    monkeypatch.setattr(net, "ping_sweep", lambda hosts, **k: calls.append(hosts) or set())

    survey, notes = net.survey_network(sweep=True, passive=False, include=["172.16.0.0/16"])
    # A /8 is narrowed to the /24 around our own address; an unrelated /16 is skipped.
    assert len(calls) == 1 and calls[0][0] == "10.0.5.1"
    assert any("too large" in note and "10.0.5.0/24" in note for note in notes)
    assert any("skipped" in note and "172.16.0.0/16" in note for note in notes)
    assert survey.interfaces[0].network == ipaddress.ip_network("10.0.0.0/8")


def test_mdns_query_is_a_valid_dns_sd_ptr_question() -> None:
    query = net._mdns_query()
    assert query[:12] == b"\x00\x00\x00\x00\x00\x01" + b"\x00\x00" * 3
    assert b"_services\x07_dns-sd\x04_udp\x05local\x00" in query
    assert query.endswith(b"\x00\x0c\x80\x01")  # PTR, IN + unicast-response bit


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_netsurvey_help() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["netsurvey", "--help"])
    assert exc.value.code == 0


def _fake_survey() -> tuple[net.Survey, list[str]]:
    mine = net.Segment(
        network=ipaddress.ip_network("192.168.0.0/24"),
        interfaces=["en0"],
        sources={"interface", "arp"},
        swept=True,
        devices=[
            net.Device(ip="192.168.0.1", mac="06:f2:67:75:4d:e2", is_gateway=True, sources={"arp"}),
            net.Device(ip="192.168.0.122", mac="4e:cc:03:6c:47:f9", is_self=True, sources={"self", "arp"}),
        ],
    )
    theirs = net.Segment(
        network=ipaddress.ip_network("10.77.0.0/24"),
        sources={"ssdp"},
        devices=[net.Device(ip="10.77.0.9", mac="de:ad:be:ef:00:01", sources={"ssdp"})],
    )
    survey = net.Survey(
        interfaces=[net.Interface("en0", "192.168.0.122", 24)],
        segments=[mine, theirs],
        conflicts=[net.Conflict("duplicate-ip", "192.168.0.50 answers from 2 MAC addresses")],
        gateway="192.168.0.1",
        self_ip="192.168.0.122",
    )
    return survey, ["10.0.0.0/8 is too large to sweep"]


def test_netsurvey_renders_segments_and_conflicts(capsys, monkeypatch) -> None:
    monkeypatch.setattr("devbits.network.survey_network", lambda *a, **k: _fake_survey())
    assert main(["netsurvey", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "en0" in out and "192.168.0.122/24" in out
    assert "192.168.0.0/24" in out and "local (en0)" in out
    assert "10.77.0.0/24" in out and "foreign" in out
    assert "gateway / router" in out and "this device" in out
    assert "1 host(s)" in out
    assert "[duplicate-ip]" in out


def test_netsurvey_summary_hides_addresses(capsys, monkeypatch) -> None:
    monkeypatch.setattr("devbits.network.survey_network", lambda *a, **k: _fake_survey())
    assert main(["netsurvey", "--no-color", "--summary"]) == 0
    out = capsys.readouterr().out
    assert "10.77.0.0/24" in out
    assert "10.77.0.9" not in out


def test_netsurvey_passes_flags_through(monkeypatch) -> None:
    seen = {}

    def fake_survey(**kwargs):
        seen.update(kwargs)
        return _fake_survey()

    monkeypatch.setattr("devbits.network.survey_network", fake_survey)
    assert main([
        "netsurvey", "--no-color", "--sweep", "--include", "10.1.0.0/24",
        "--group-prefix", "16", "--timeout", "0.4", "--workers", "32",
        "--no-passive", "--resolve", "--max-sweep", "1024",
    ]) == 0
    assert seen["sweep"] is True
    assert seen["include"] == ["10.1.0.0/24"]
    assert seen["group_prefix"] == 16
    assert seen["timeout"] == 0.4
    assert seen["workers"] == 32
    assert seen["passive"] is False
    assert seen["resolve"] is True
    assert seen["max_sweep_hosts"] == 1024


def test_netsurvey_rejects_bad_group_prefix(capsys, monkeypatch) -> None:
    monkeypatch.setattr("devbits.network.survey_network", lambda *a, **k: _fake_survey())
    assert main(["netsurvey", "--group-prefix", "4"]) == 1
    assert "--group-prefix" in capsys.readouterr().err
