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
from dataclasses import dataclass

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


def arp_table() -> dict[str, str]:
    """Map ``IP -> MAC`` from the system ARP cache (cross-platform).

    Tries each candidate command until one yields entries:

    * Windows: ``arp -a`` (already numeric; it has no ``-n`` flag).
    * macOS / Linux: ``arp -an`` — the ``-n`` avoids slow per-entry reverse DNS.
    * Linux without net-tools (no ``arp``): ``ip neigh show`` as a fallback.
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
        table: dict[str, str] = {}
        for line in out.splitlines():
            ip_match = _IP_RE.search(line)
            mac_match = _MAC_RE.search(line)
            if ip_match and mac_match:
                table[ip_match.group()] = _normalize_mac(mac_match.group())
        if table:
            return table
    return {}


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

    hosts = [str(host) for host in net.hosts()]
    alive: set[str] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(_ping, ip, timeout): ip for ip in hosts}
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                alive.add(futures[future])

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
