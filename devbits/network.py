from __future__ import annotations

import codecs
import concurrent.futures
import functools
import ipaddress
import locale
import platform
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

_MAC_RE = re.compile(r"(?:[0-9a-fA-F]{1,2}[:-]){5}[0-9a-fA-F]{1,2}")
_IP_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")

#: Label used for locally-administered (randomized / private) MAC addresses,
#: which carry no manufacturer information.
PRIVATE_MAC_LABEL = "(private)"

_vendor_cache: dict[str, str | None] = {}


@dataclass
class Device:
    """A single host discovered on the local network."""

    ip: str
    mac: str | None = None
    hostname: str | None = None
    vendor: str | None = None
    is_self: bool = False
    is_gateway: bool = False
    #: How this host was observed — any of ``arp``, ``ping``, ``ssdp``, ``mdns``,
    #: ``self``, ``gateway``. Only populated by :func:`survey_network`.
    sources: set[str] = field(default_factory=set)


@functools.lru_cache(maxsize=1)
def _console_encoding() -> str:
    """The encoding the OS command-line tools print in.

    On Windows this is the console output code page (e.g. ``cp950`` on a
    Traditional Chinese install), *not* UTF-8. Elsewhere the locale's preferred
    encoding is right.
    """
    if platform.system().lower() == "windows":
        try:
            import ctypes

            codepage = ctypes.windll.kernel32.GetConsoleOutputCP() or ctypes.windll.kernel32.GetOEMCP()
            codecs.lookup(f"cp{codepage}")  # reject code pages Python can't handle
            return f"cp{codepage}"
        except Exception:
            pass
    try:
        return codecs.lookup(locale.getpreferredencoding(False)).name
    except Exception:
        return "utf-8"


def _run_text(cmd: list[str], timeout: float) -> tuple[int, str]:
    """Run ``cmd`` and return ``(returncode, stdout)``, decoded defensively.

    Deliberately *not* ``text=True``: that decodes with UTF-8 strictly, so on a
    non-English Windows the UnicodeDecodeError is raised inside subprocess'
    reader threads, which leaves ``stdout`` as ``None`` and produces a confusing
    ``'NoneType' object has no attribute ...`` far from the real cause. Decoding
    the bytes here with ``errors="replace"`` cannot fail, and everything we parse
    out of these tools (IPs, MACs, ``TTL=``) is ASCII anyway.
    """
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    raw = proc.stdout
    if raw is None:
        return proc.returncode, ""
    if isinstance(raw, str):  # a text-mode fake / caller override
        return proc.returncode, raw
    return proc.returncode, raw.decode(_console_encoding(), "replace")


def local_ip() -> str:
    """Best-effort primary IPv4 address of this machine.

    Opens a UDP socket toward a public address to learn which local interface
    would route outbound traffic. No packets are actually sent, so this works
    offline as long as a network interface with a route exists (e.g. a LAN with
    no internet). Falls back to the hostname's address, then loopback.
    """
    ip = ""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = ""
    finally:
        sock.close()
    if not ip or ip.startswith("0."):
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = "127.0.0.1"
    return ip


def default_network(prefix: int = 24) -> ipaddress.IPv4Network:
    """The local subnet containing this machine's primary address."""
    return ipaddress.ip_network(f"{local_ip()}/{prefix}", strict=False)


def gateway_ip() -> str | None:
    """The default-gateway (router) address, or ``None`` if undetermined."""
    system = platform.system().lower()
    try:
        if system == "windows":
            out = _run_text(["route", "print", "0.0.0.0"], timeout=5)[1]
            for line in out.splitlines():
                if line.strip().startswith("0.0.0.0"):
                    ips = _IP_RE.findall(line)
                    if len(ips) >= 3:
                        return ips[2]  # destination, netmask, gateway
            return None
        if system == "darwin":
            out = _run_text(["route", "-n", "get", "default"], timeout=5)[1]
            match = re.search(r"gateway:\s*(" + _IP_RE.pattern + ")", out)
            return match.group(1) if match else None
        # Linux and other Unixes
        out = _run_text(["ip", "route"], timeout=5)[1]
        match = re.search(r"default via (" + _IP_RE.pattern + ")", out)
        return match.group(1) if match else None
    except Exception:
        return None


def _normalize_mac(mac: str) -> str:
    parts = re.split(r"[:-]", mac)
    return ":".join(part.zfill(2).lower() for part in parts)


def is_private_mac(mac: str) -> bool:
    """Whether ``mac`` is locally administered (a randomized / private address).

    Modern phones and laptops rotate a random MAC per network for privacy. The
    locally-administered bit (``0x02`` of the first octet) is set on these, and
    they carry no manufacturer information, so an OUI lookup is pointless.
    """
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


def lookup_vendor(mac: str, timeout: float = 3.0, retries: int = 2) -> str | None:
    """Resolve the manufacturer for ``mac`` via the macvendors.com API (online).

    Results are cached per OUI prefix for the life of the process. Returns
    ``None`` when the vendor is unknown or the service is unreachable.

    NOTE: this sends the MAC's first three octets to a third-party service; it
    only runs when the caller explicitly opts in (``scan_network(lookup=True)``).
    """
    prefix = ":".join(mac.split(":")[:3])
    if prefix in _vendor_cache:
        return _vendor_cache[prefix]

    request = urllib.request.Request(
        f"https://api.macvendors.com/{mac}",
        headers={"User-Agent": "devbits-netscan"},
    )
    # Fall back to an unverified (still-encrypted) context if the default CA
    # bundle can't validate the cert — common on macOS Python.framework installs
    # that never ran "Install Certificates.command". The payload is only a MAC
    # OUI prefix and a vendor name, so this is acceptable here.
    contexts = [None, ssl._create_unverified_context()]
    vendor: str | None = None
    for context in contexts:
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                    vendor = response.read().decode("utf-8", "replace").strip() or None
                _vendor_cache[prefix] = vendor
                return vendor
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < retries:  # rate limited: back off and retry
                    time.sleep(1.2)
                    continue
                _vendor_cache[prefix] = None  # 404 / other HTTP error → unknown, don't retry other contexts
                return None
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, ssl.SSLError):
                    break  # try the next (unverified) context
                _vendor_cache[prefix] = None
                return None
            except Exception:
                _vendor_cache[prefix] = None
                return None
    _vendor_cache[prefix] = vendor
    return vendor


def arp_entries() -> list[tuple[str, str]]:
    """``(IP, MAC)`` pairs from the system ARP / neighbour cache (cross-platform).

    Tries each candidate command until one yields entries:

    * Windows: ``arp -a`` (already numeric; it has no ``-n`` flag).
    * macOS / Linux: ``arp -an`` — the ``-n`` avoids slow per-entry reverse DNS.
    * Linux without net-tools (no ``arp``): ``ip neigh show`` as a fallback.

    Duplicates are preserved: the same IP answering from two different MACs is
    exactly the address collision :func:`survey_network` reports, and a dict
    would hide it. The cache also holds neighbours *outside* our own subnet,
    which is how foreign network segments on the same switch are spotted.
    """
    if platform.system().lower() == "windows":
        commands = [["arp", "-a"]]
    else:
        commands = [["arp", "-an"], ["ip", "neigh", "show"]]

    for command in commands:
        try:
            out = _run_text(command, timeout=10)[1]
        except Exception:
            continue
        entries: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for line in out.splitlines():
            ip_match = _IP_RE.search(line)
            mac_match = _MAC_RE.search(line)
            if not (ip_match and mac_match):
                continue
            pair = (ip_match.group(), _normalize_mac(mac_match.group()))
            if pair not in seen:  # the same pair can be listed per interface
                seen.add(pair)
                entries.append(pair)
        if entries:
            return entries
    return []


def arp_table() -> dict[str, str]:
    """Map ``IP -> MAC`` from the system ARP cache (cross-platform).

    A convenience view over :func:`arp_entries`; when an IP has more than one
    MAC (an address collision) the last one listed wins.
    """
    return dict(arp_entries())


def _ping(ip: str, timeout: float = 1.0) -> bool:
    system = platform.system().lower()
    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
    elif system == "darwin":
        cmd = ["ping", "-c", "1", "-W", str(int(timeout * 1000)), ip]  # -W is milliseconds on macOS
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(round(timeout)))), ip]  # -W is seconds on Linux
    try:
        returncode, out = _run_text(cmd, timeout=timeout + 2)
    except Exception:
        return False
    if returncode != 0:
        return False
    if system == "windows":
        # Windows ping can exit 0 while printing "Destination host unreachable"
        # (a reply from the gateway, not the target). Require a real echo reply.
        # "TTL=" is ASCII in every locale, so this survives localized output.
        return "ttl=" in out.lower()
    return True


def _resolve_hostname(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return None


def ping_sweep(hosts: Sequence[str], timeout: float = 1.0, workers: int = 64) -> set[str]:
    """Ping every address in ``hosts`` concurrently; return the ones that reply."""
    alive: set[str] = set()
    if not hosts:
        return alive
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(_ping, ip, timeout): ip for ip in hosts}
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                alive.add(futures[future])
    return alive


def scan_network(
    network: ipaddress.IPv4Network | None = None,
    timeout: float = 1.0,
    workers: int = 64,
    resolve: bool = True,
    lookup: bool = False,
) -> list[Device]:
    """Discover live hosts on ``network`` via a threaded ICMP ping sweep.

    After the sweep, the system ARP cache is read for MAC addresses and (unless
    ``resolve`` is false) reverse DNS is queried for hostnames. This machine and
    the default gateway are always included even if they don't answer pings.

    When ``lookup`` is true, each MAC's manufacturer is resolved online (private
    /randomized MACs are labelled instead) — see :func:`lookup_vendor`.
    """
    net = network or default_network()
    self_ip = local_ip()
    gateway = gateway_ip()

    alive = ping_sweep([str(host) for host in net.hosts()], timeout=timeout, workers=workers)

    alive.add(self_ip)
    if gateway and ipaddress.ip_address(gateway) in net:
        alive.add(gateway)

    arp = arp_table()
    devices = [
        Device(
            ip=ip,
            mac=arp.get(ip),
            hostname=_resolve_hostname(ip) if resolve else None,
            is_self=ip == self_ip,
            is_gateway=ip == gateway,
        )
        for ip in alive
    ]
    if lookup:
        for device in devices:
            if not device.mac:
                continue
            device.vendor = PRIVATE_MAC_LABEL if is_private_mac(device.mac) else lookup_vendor(device.mac)

    devices.sort(key=lambda device: tuple(int(octet) for octet in device.ip.split(".")))
    return devices


# ---------------------------------------------------------------------------
# Network survey: which segments and addresses are in use around us
# ---------------------------------------------------------------------------

#: Multicast destinations used for passive discovery. Answers reveal hosts that
#: never appear in our ARP cache — including ones on *foreign* subnets sharing
#: the same switch, which a single-subnet ping sweep can never find.
_SSDP_ADDR = ("239.255.255.250", 1900)
_MDNS_ADDR = ("224.0.0.251", 5353)
_ALL_HOSTS = "224.0.0.1"

_SSDP_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 1\r\n"
    "ST: ssdp:all\r\n"
    "\r\n"
).encode()


@dataclass
class Interface:
    """One IPv4 address configured on a local interface."""

    name: str
    ip: str
    prefix: int

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(f"{self.ip}/{self.prefix}", strict=False)

    @property
    def is_link_local(self) -> bool:
        """A 169.254.x.x self-assigned address, i.e. DHCP never answered."""
        return self.ip.startswith("169.254.")


@dataclass
class Segment:
    """A network range observed in the current environment."""

    network: ipaddress.IPv4Network
    devices: list[Device] = field(default_factory=list)
    #: Local interfaces addressed in this range (empty for foreign segments).
    interfaces: list[str] = field(default_factory=list)
    #: How the segment was discovered: ``arp``, ``ping``, ``ssdp``, ``mdns``, ``interface``.
    sources: set[str] = field(default_factory=set)
    #: True when the range was ping-swept, so the host list is complete.
    swept: bool = False

    @property
    def is_local(self) -> bool:
        return bool(self.interfaces)


@dataclass
class Conflict:
    """A clash worth knowing about before plugging kit into a shared network."""

    kind: str  # duplicate-ip | overlap | shared-l2 | no-dhcp | router-bridge
    message: str


@dataclass
class Survey:
    """The result of :func:`survey_network`."""

    interfaces: list[Interface] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    gateway: str | None = None
    self_ip: str = ""

    @property
    def device_count(self) -> int:
        return sum(len(segment.devices) for segment in self.segments)


def _mask_prefix(text: str) -> int | None:
    """Parse a netmask into a prefix length.

    Accepts every form the platform tools print: dotted (``255.255.255.0``),
    hexadecimal (``0xffffff00``, BSD ``ifconfig``) and a bare prefix (``24``).
    Returns ``None`` when ``text`` is not a contiguous netmask — which is how
    the ``ipconfig`` parser tells an address line from a mask line.
    """
    text = text.strip()
    try:
        if text.lower().startswith("0x"):
            bits = int(text, 16)
        elif text.isdigit():
            prefix = int(text)
            return prefix if 0 <= prefix <= 32 else None
        else:
            bits = int(ipaddress.IPv4Address(text))
    except (ValueError, ipaddress.AddressValueError):
        return None
    if not 0 <= bits <= 0xFFFFFFFF:
        return None
    inverted = bits ^ 0xFFFFFFFF
    if (inverted + 1) & inverted:  # ones must be contiguous from the top
        return None
    return bin(bits).count("1")


def _keep_interface(ip: str) -> bool:
    return not ip.startswith("127.") and ip != "0.0.0.0"


def is_host_address(ip: str) -> bool:
    """Whether ``ip`` can name an actual host.

    Our own multicast probes leave the group addresses (224.0.0.1, 224.0.0.251,
    239.255.255.250) in the ARP cache, and a broadcast ping leaves x.x.x.255
    there too. Reporting those as "devices" would be pure noise.
    """
    try:
        address = ipaddress.IPv4Address(ip)
    except (ValueError, ipaddress.AddressValueError):
        return False
    return not (
        address.is_multicast
        or address.is_loopback
        or address.is_unspecified
        or address.is_reserved
        or address == ipaddress.IPv4Address("255.255.255.255")
    )


def host_count(network: ipaddress.IPv4Network) -> int:
    """How many addresses in ``network`` are usable hosts.

    ``num_addresses - 2`` is wrong for the point-to-point cases: a /32 (a VPN
    interface) holds one host and a /31 two, neither reserving network and
    broadcast addresses.
    """
    if network.prefixlen >= 31:
        return network.num_addresses
    return network.num_addresses - 2


def is_hardware_mac(mac: str) -> bool:
    """Whether ``mac`` belongs to one interface, rather than a group.

    The low bit of the first octet marks multicast (``01:00:5e:...`` for IPv4
    multicast) and ``ff:ff:ff:ff:ff:ff`` is broadcast; neither identifies a host.
    """
    try:
        return not int(mac.split(":")[0], 16) & 0x01
    except (ValueError, IndexError):
        return False


def _parse_ip_addr(out: str) -> list[Interface]:
    """Parse ``ip -o -4 addr show`` (Linux, iproute2)."""
    interfaces = []
    for line in out.splitlines():
        match = re.search(r"^\d+:\s+(\S+)\s+inet\s+(" + _IP_RE.pattern + r")/(\d{1,2})", line)
        if match and _keep_interface(match.group(2)):
            interfaces.append(Interface(match.group(1), match.group(2), int(match.group(3))))
    return interfaces


def _parse_ifconfig(out: str) -> list[Interface]:
    """Parse ``ifconfig -a`` (macOS / BSD, and net-tools on Linux)."""
    interfaces = []
    name = "?"
    for line in out.splitlines():
        if line[:1].strip():  # unindented line starts a new interface block
            head = re.match(r"^([\w.:@-]+?):?\s", line + " ")
            if head:
                name = head.group(1).rstrip(":")
            continue
        match = re.search(r"\binet\s+(?:addr:)?(" + _IP_RE.pattern + r")", line)
        if not match or not _keep_interface(match.group(1)):
            continue
        mask = re.search(r"\b(?:netmask|Mask:)\s*(0x[0-9a-fA-F]+|" + _IP_RE.pattern + r")", line)
        prefix = _mask_prefix(mask.group(1)) if mask else None
        interfaces.append(Interface(name, match.group(1), prefix if prefix is not None else 24))
    return interfaces


def _parse_ipconfig(out: str) -> list[Interface]:
    """Parse ``ipconfig`` (Windows).

    The field labels are localized, so nothing is matched by name: an address
    line is followed by its subnet-mask line, and a mask is recognised by its
    bit pattern (see :func:`_mask_prefix`).
    """
    interfaces = []
    name = "?"
    pending: str | None = None
    for line in out.splitlines():
        if line.strip() and not line[0].isspace():
            name = line.strip().rstrip(":").strip()
            pending = None
            continue
        found = _IP_RE.findall(line)
        if len(found) != 1:
            continue
        address = found[0]
        prefix = _mask_prefix(address)
        if pending and prefix is not None and 0 < prefix <= 32:
            interfaces.append(Interface(name, pending, prefix))
            pending = None
        elif prefix is None and _keep_interface(address):
            pending = address  # candidate address; confirmed by the mask below it
    return interfaces


def local_interfaces() -> list[Interface]:
    """Every IPv4 address configured on this machine, with its subnet.

    Uses the platform tools (``ip``, ``ifconfig``, ``ipconfig``) because the
    standard library exposes no netmask. Falls back to the primary address with
    an assumed /24 when none of them can be parsed.
    """
    system = platform.system().lower()
    interfaces: list[Interface] = []
    if system == "windows":
        try:
            interfaces = _parse_ipconfig(_run_text(["ipconfig"], timeout=10)[1])
        except Exception:
            interfaces = []
    else:
        if system != "darwin":
            try:
                interfaces = _parse_ip_addr(_run_text(["ip", "-o", "-4", "addr", "show"], timeout=10)[1])
            except Exception:
                interfaces = []
        if not interfaces:
            try:
                interfaces = _parse_ifconfig(_run_text(["ifconfig", "-a"], timeout=10)[1])
            except Exception:
                interfaces = []
    if not interfaces:
        ip = local_ip()
        if _keep_interface(ip):
            interfaces = [Interface("(primary)", ip, 24)]
    return interfaces


def _mdns_query() -> bytes:
    """A DNS-SD service-enumeration query (``_services._dns-sd._udp.local`` PTR).

    The question class carries the mDNS "unicast response" bit (0x8000) so
    responders answer our ephemeral port directly; without it replies go to the
    multicast group on port 5353, which the OS resolver already owns.
    """
    header = b"\x00\x00" + b"\x00\x00" + b"\x00\x01" + b"\x00\x00" * 3
    labels = (b"_services", b"_dns-sd", b"_udp", b"local")
    name = b"".join(bytes([len(label)]) + label for label in labels) + b"\x00"
    return header + name + b"\x00\x0c" + b"\x80\x01"  # QTYPE=PTR, QCLASS=IN|unicast


def _multicast_probe(
    destination: tuple[str, int],
    payload: bytes,
    timeout: float,
    sources: Sequence[str],
) -> set[str]:
    """Send ``payload`` to a multicast group and collect the responders' addresses."""
    found: set[str] = set()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return found
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.3)
        sock.bind(("", 0))
        # Send once per local interface: a multicast datagram leaves through a
        # single interface, so a laptop on both Wi-Fi and Ethernet would
        # otherwise only ever probe one of the two segments.
        for source in sources or [""]:
            if source:
                try:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(source))
                except OSError:
                    continue
            try:
                sock.sendto(payload, destination)
            except OSError:
                continue
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                _, address = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            found.add(address[0])
    except OSError:
        return found
    finally:
        sock.close()
    return found


def discover_passive(timeout: float = 2.0, interfaces: Sequence[Interface] | None = None) -> dict[str, set[str]]:
    """Find hosts by multicast, without scanning any address range.

    Sends an SSDP ``M-SEARCH`` and an mDNS service query out of every local
    interface and notes who answers. Costs two datagrams per interface and
    reaches hosts on subnets we don't even have an address in, which is what
    makes rogue segments on a shared venue switch visible.

    Returns ``{ip: {"ssdp", "mdns"}}``.
    """
    sources = [nic.ip for nic in (interfaces if interfaces is not None else local_interfaces())]
    probes = (("ssdp", _SSDP_ADDR, _SSDP_MSEARCH), ("mdns", _MDNS_ADDR, _mdns_query()))
    found: dict[str, set[str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(probes)) as executor:
        futures = {
            executor.submit(_multicast_probe, destination, payload, timeout, sources): label
            for label, destination, payload in probes
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                addresses = future.result()
            except Exception:
                continue
            for ip in addresses:
                found.setdefault(ip, set()).add(futures[future])
    return found


def _prime_arp_cache(interfaces: Sequence[Interface], timeout: float = 1.0) -> None:
    """Nudge neighbours into replying so the ARP cache isn't empty.

    Pings the all-hosts multicast group and each interface's broadcast address.
    Many stacks answer these, which populates the ARP cache in one round trip
    instead of one per address. Failures are ignored: several platforms drop
    broadcast pings, and this is only an optimisation on top of the cache.
    """
    targets = [_ALL_HOSTS]
    for nic in interfaces:
        network = nic.network
        if network.num_addresses > 2:
            targets.append(str(network.broadcast_address))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(targets))) as executor:
        list(executor.map(lambda ip: _ping(ip, timeout), targets))


def _sweep_targets(
    interfaces: Sequence[Interface],
    foreign: Sequence[ipaddress.IPv4Network],
    include: Sequence[ipaddress.IPv4Network],
    self_ip: str,
    max_hosts: int,
    skipped: list[str],
) -> list[ipaddress.IPv4Network]:
    """Pick the ranges worth ping-sweeping, shrinking or dropping huge ones."""
    ordered: list[ipaddress.IPv4Network] = []
    for network in [nic.network for nic in interfaces] + list(foreign) + list(include):
        if network in ordered:
            continue
        if host_count(network) > max_hosts:
            # A /16 (or worse, an included /8) would take minutes. Sweep the /24
            # around our own address if we sit inside it; otherwise say so.
            try:
                if ipaddress.ip_address(self_ip) in network:
                    narrowed = ipaddress.ip_network(f"{self_ip}/24", strict=False)
                    if narrowed not in ordered:
                        ordered.append(narrowed)
                        skipped.append(f"{network} is too large to sweep; swept {narrowed} instead")
                    continue
            except ValueError:
                pass
            skipped.append(f"{network} skipped: {host_count(network)} hosts exceeds --max-sweep")
            continue
        ordered.append(network)
    return ordered


def _group_networks(addresses: Sequence[str], prefix: int) -> list[ipaddress.IPv4Network]:
    """Bucket loose addresses into /``prefix`` networks, largest bucket first."""
    counts: dict[ipaddress.IPv4Network, int] = {}
    for ip in addresses:
        try:
            network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
        except ValueError:
            continue
        counts[network] = counts.get(network, 0) + 1
    return sorted(counts, key=lambda net: (-counts[net], int(net.network_address)))


def _find_conflicts(
    interfaces: Sequence[Interface],
    segments: Sequence[Segment],
    mac_owners: dict[str, set[str]],
    ip_owners: dict[str, set[str]],
) -> list[Conflict]:
    conflicts: list[Conflict] = []

    for ip in sorted(ip_owners, key=lambda value: tuple(int(octet) for octet in value.split("."))):
        macs = ip_owners[ip]
        if len(macs) > 1:
            conflicts.append(Conflict(
                "duplicate-ip",
                f"{ip} answers from {len(macs)} MAC addresses ({', '.join(sorted(macs))}) "
                "— two devices claim the same address",
            ))

    # Two of our own interfaces in the same range: traffic for one silently
    # takes the other's route. The classic VPN-vs-venue-LAN collision.
    for index, first in enumerate(interfaces):
        for second in interfaces[index + 1:]:
            if first.network.overlaps(second.network):
                conflicts.append(Conflict(
                    "overlap",
                    f"{first.name} ({first.ip}/{first.prefix}) and {second.name} "
                    f"({second.ip}/{second.prefix}) are on overlapping ranges",
                ))

    for segment in segments:
        if segment.is_local:
            continue
        for nic in interfaces:
            if segment.network.overlaps(nic.network):
                conflicts.append(Conflict(
                    "overlap",
                    f"foreign segment {segment.network} overlaps your own {nic.name} "
                    f"({nic.ip}/{nic.prefix}) — pick a different range",
                ))

    for nic in interfaces:
        if nic.is_link_local:
            conflicts.append(Conflict(
                "no-dhcp",
                f"{nic.name} self-assigned {nic.ip} — no DHCP server answered on that link",
            ))

    # One MAC holding addresses in several segments is a router or bridge
    # joining them; at a venue that is usually somebody else's router leaking
    # their subnet onto the shared switch.
    address_of = {}
    for segment in segments:
        for device in segment.devices:
            address_of[device.ip] = segment.network
    for mac, ips in sorted(mac_owners.items()):
        networks = {address_of[ip] for ip in ips if ip in address_of}
        if len(networks) > 1:
            joined = ", ".join(str(network) for network in sorted(networks, key=int_of_network))
            conflicts.append(Conflict(
                "router-bridge",
                f"{mac} answers for addresses in {len(networks)} segments ({joined}) "
                "— a router or bridge joining them",
            ))

    foreign = [segment for segment in segments if not segment.is_local]
    if foreign:
        joined = ", ".join(str(segment.network) for segment in foreign)
        conflicts.append(Conflict(
            "shared-l2",
            f"{len(foreign)} foreign segment(s) share this link: {joined}",
        ))
    return conflicts


def int_of_network(network: ipaddress.IPv4Network) -> int:
    return int(network.network_address)


def survey_network(
    include: Sequence[str] = (),
    group_prefix: int = 24,
    sweep: bool = False,
    passive: bool = True,
    timeout: float = 0.6,
    workers: int = 128,
    discover_timeout: float = 2.0,
    resolve: bool = False,
    max_sweep_hosts: int = 4096,
    progress: Callable[[str], None] | None = None,
) -> tuple[Survey, list[str]]:
    """Map the network segments and addresses in use around this machine.

    Answers "what is on this wire?" rather than "who is on my subnet?" — the
    question that matters on a venue or office network shared with equipment you
    don't administer. Three independent sources are combined:

    * every IPv4 address configured locally, with its real netmask;
    * the ARP / neighbour cache, which lists link neighbours *regardless of
      subnet* — this is what exposes foreign ranges on the same switch;
    * SSDP and mDNS multicast answers (unless ``passive`` is false).

    With ``sweep`` the local and discovered ranges are also ping-swept so each
    segment's address list is complete; ``include`` ranges are always swept.
    Addresses that belong to no local interface are bucketed into
    /``group_prefix`` networks, since a foreign segment's real mask is unknowable
    from outside.

    Returns ``(survey, notes)``, where ``notes`` are human-readable remarks
    about ranges that were narrowed or skipped.
    """
    notes: list[str] = []
    interfaces = local_interfaces()
    self_ip = local_ip()
    gateway = gateway_ip()

    included: list[ipaddress.IPv4Network] = []
    for value in include:
        included.append(ipaddress.ip_network(value, strict=False))

    # The network and broadcast addresses of our own ranges are not hosts, and
    # priming the ARP cache put the broadcast one there.
    edges = set()
    for nic in interfaces:
        if nic.network.num_addresses > 2:
            edges.add(str(nic.network.network_address))
            edges.add(str(nic.network.broadcast_address))

    sources: dict[str, set[str]] = {}

    def note_source(ip: str, source: str) -> None:
        if not is_host_address(ip) or ip in edges:
            return
        sources.setdefault(ip, set()).add(source)

    if passive:
        if progress:
            progress("Probing for neighbours (ARP, SSDP, mDNS) ...")
        _prime_arp_cache(interfaces, timeout=max(timeout, 1.0))
        for ip, labels in discover_passive(discover_timeout, interfaces).items():
            for label in labels:
                note_source(ip, label)

    ip_owners: dict[str, set[str]] = {}
    mac_owners: dict[str, set[str]] = {}

    def note_arp() -> None:
        for ip, mac in arp_entries():
            if not is_hardware_mac(mac):
                continue
            note_source(ip, "arp")
            if ip in sources:  # dropped by the address filter otherwise
                ip_owners.setdefault(ip, set()).add(mac)
                mac_owners.setdefault(mac, set()).add(ip)

    note_arp()

    swept: set[ipaddress.IPv4Network] = set()
    if sweep or included:
        foreign = [] if not sweep else [
            network for network in _group_networks(list(sources), group_prefix)
            if not any(network.overlaps(nic.network) for nic in interfaces)
        ]
        targets = _sweep_targets(
            interfaces if sweep else [], foreign, included, self_ip, max_sweep_hosts, notes,
        )
        for network in targets:
            if progress:
                progress(f"Sweeping {network} ({host_count(network)} hosts) ...")
            addresses = [str(host) for host in network.hosts()] or [str(network.network_address)]
            for ip in ping_sweep(addresses, timeout=timeout, workers=workers):
                note_source(ip, "ping")
            swept.add(network)
        note_arp()  # a sweep fills the ARP cache with everything that replied

    note_source(self_ip, "self")
    for nic in interfaces:
        note_source(nic.ip, "self")
    if gateway:
        note_source(gateway, "gateway")

    macs = {ip: sorted(owners)[0] for ip, owners in ip_owners.items() if owners}
    self_addresses = {self_ip, *(nic.ip for nic in interfaces)}

    def make_device(ip: str) -> Device:
        return Device(
            ip=ip,
            mac=macs.get(ip),
            hostname=_resolve_hostname(ip) if resolve else None,
            is_self=ip in self_addresses,
            is_gateway=ip == gateway,
            sources=set(sources.get(ip, ())),
        )

    # Local interface networks keep their real prefix; whatever is left over is
    # bucketed by /group_prefix.
    segments: list[Segment] = []
    for nic in interfaces:
        existing = next((s for s in segments if s.network == nic.network), None)
        if existing:
            existing.interfaces.append(nic.name)
            continue
        segments.append(Segment(network=nic.network, interfaces=[nic.name], sources={"interface"}))

    unplaced = [
        ip for ip in sources
        if not any(ipaddress.ip_address(ip) in segment.network for segment in segments)
    ]
    for network in _group_networks(unplaced, group_prefix):
        if not any(segment.network == network for segment in segments):
            segments.append(Segment(network=network))

    for ip in sources:
        address = ipaddress.ip_address(ip)
        for segment in segments:
            if address in segment.network:
                segment.devices.append(make_device(ip))
                segment.sources |= sources[ip]
                break

    for segment in segments:
        segment.devices.sort(key=lambda device: int(ipaddress.ip_address(device.ip)))
        # Only claim completeness when the whole segment was covered: sweeping a
        # /29 inside a /24 says nothing about the rest of the /24.
        segment.swept = any(segment.network.subnet_of(network) for network in swept)

    segments.sort(key=lambda segment: (not segment.is_local, int_of_network(segment.network)))
    survey = Survey(
        interfaces=interfaces,
        segments=segments,
        conflicts=_find_conflicts(interfaces, segments, mac_owners, ip_owners),
        gateway=gateway,
        self_ip=self_ip,
    )
    return survey, notes
