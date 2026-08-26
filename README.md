# devbits

A lightweight CLI toolkit for daily development utilities — video/image processing, project file management, and more.

## Installation

```bash
pip install devbits
```

Requires Python ≥ 3.9.

## Usage

All commands are available in two ways:

```bash
# As subcommands of devbits
devbits <command> [options]

# As standalone commands
<command> [options]
```

Use `--help` on any command for detailed usage and parameter descriptions:

```bash
devbits editvideo --help
editvideo --help
```

## Commands

### Video

| Command | Description |
|---------|-------------|
| `editvideo` | Trim a video by time (seconds) or frame range. Includes `--gui` for browser-based editing. (Formerly `clipvideo`, which still works but prints a rename notice.) |
| `video2images` | Extract frames from a video. |
| `video2gif` | Convert a video (or a portion) to animated GIF. |
| `images2video` | Assemble an image sequence into an MP4 video. |
| `images2gif` | Assemble an image sequence into an animated GIF. |
| `resizevideo` | Re-encode a video at a different resolution. |

### Image

| Command | Description |
|---------|-------------|
| `resizeimage` | Resize a single image (preserves aspect ratio by default). |
| `recolor` | Recolor a logo/icon foreground, leaving the background intact. |
| `image2ico` | Convert an image to a multi-size ICO file. |
| `batchimages` | Batch resize or convert all images in a folder. |
| `checkimages` | Scan for broken / corrupt image files. |
| `contactsheet` | Generate a thumbnail grid (contact sheet) from a folder of images. |

### Project / Files

| Command | Description |
|---------|-------------|
| `clearcache` | Remove `__pycache__` and other Python cache directories. |
| `tree` | Print a directory tree. |
| `size` | List the largest files / folders, sorted by size. |
| `renamefiles` | Batch rename files sequentially. |
| `samplefiles` | Copy or move the first N files to another folder. |

### Network

| Command | Description |
|---------|-------------|
| `netscan` | List devices connected to your local network (Wi-Fi / router) with their IP, MAC, and hostname. `--lookup` adds the manufacturer. |
| `netsurvey` | Map **every network segment** visible on the current link — your own interfaces, foreign subnets on the same switch, the addresses in use, and the conflicts between them. Built for shared networks (trade shows, offices). |
| `wifi` | Manage Wi-Fi: `list`, `connect` (arrow-key picker + hidden password prompt), `on`, `off`, `forget`. Linux and Windows fully; macOS without `list`. |

## Examples

```bash
# Trim video from 5s to 20s
editvideo movie.mp4 --start 5.0 --end 20.0

# Open interactive clip editor in the browser
editvideo movie.mp4 --gui

# Convert video to GIF (3.5s–10s at 15 fps)
video2gif movie.mp4 --start 3.5 --end 10.0 --fps 15

# Extract every 5th frame as PNG
video2images movie.mp4 --every 5 --format png

# Recolor a logo's foreground to black (keeps the background)
recolor logo.png

# Recolor a logo's foreground to a custom color (hex or R,G,B)
recolor logo.png --color '#1a73e8'
recolor logo.png --color 0,178,179

# Batch resize images to 800×600
batchimages ./photos -o ./resized --size 800,600

# Clean Python caches
clearcache . --all

# List every device on your local network
netscan

# Also identify each device's manufacturer (online OUI lookup)
netscan --lookup

# Scan a specific subnet, faster, without hostname lookups
netscan --network 192.168.1.0/24 --timeout 0.5 --no-resolve

# Map every network segment and address around you (passive, ~2s)
netsurvey

# Segments and conflicts only, no address lists
netsurvey --summary

# Also ping-sweep your ranges and the discovered ones
netsurvey --sweep

# Check specific ranges before assigning them at a venue
netsurvey --include 192.168.1.0/24 --include 10.0.0.0/24

# Show the Wi-Fi networks in range (Linux / Windows)
wifi list

# Pick a Wi-Fi network with the arrow keys, then type the password
wifi connect

# Join a specific network without the picker
wifi connect MyHome-5G

# Turn the Wi-Fi radio on / off
wifi on
wifi off

# Stop a network from auto-connecting
wifi forget OldCafe
```

> `netscan` reports IP, MAC, hostname and (with `--lookup`) the hardware
> **manufacturer** — a network scan can't read a device's CPU/RAM/OS. Phones and
> laptops that use a randomized/private MAC show up as `(private)` and can't be
> attributed to a vendor.

### Surveying a shared network

`netscan` answers "who is on **my** subnet?". `netsurvey` answers "what is on
this **wire**?" — the question that matters in an exhibition hall, an office, or
a co-working space where other people's routers hand out their own ranges:

```
INTERFACES
  en0         192.168.0.122/24      gateway 192.168.0.1
  utun6       10.2.244.44/32

SEGMENTS  (3 total, 2 yours, 9 address(es) in use)
  192.168.0.0/24      local (en0)             swept     6 host(s)
    192.168.0.1     06:f2:67:75:4d:e2   gateway,arp,ping      gateway / router
    192.168.0.122   4e:e5:41:93:7c:1f   self,arp,mdns         this device
    ...
  10.77.0.0/24        foreign                 passive   2 host(s)
    10.77.0.9       de:ad:be:ef:00:01   ssdp,arp

CONFLICTS  (2)
  ! [duplicate-ip] 192.168.0.50 answers from 2 MAC addresses (…) — two devices claim the same address
  - [shared-l2] 1 foreign segment(s) share this link: 10.77.0.0/24
```

Three independent sources are combined, so segments you have no address in still
show up:

- **every local interface** with its real netmask (`ip` / `ifconfig` / `ipconfig`);
- the **ARP / neighbour cache**, which lists link neighbours *regardless of
  subnet* — this is what exposes somebody else's range on the same switch;
- **SSDP and mDNS** answers, sent out of each interface (skip with `--no-passive`).

The default pass is passive and takes about two seconds. `--sweep` additionally
ping-sweeps your ranges and the discovered ones so each address list is complete;
`--include CIDR` sweeps a specific range you are about to assign. Ranges larger
than `--max-sweep` (4096 hosts) are narrowed to the /24 around your own address
or skipped, with a note on stderr.

Reported conflicts:

| Kind | Meaning |
|------|---------|
| `duplicate-ip` | One address answers from two MACs — two devices claim it. |
| `overlap` | A foreign range, or a second local interface (VPN!), overlaps one of your subnets. |
| `no-dhcp` | An interface self-assigned a `169.254.x.x` address; no DHCP server answered. |
| `router-bridge` | One MAC answers for addresses in several segments — a router joining them. |
| `shared-l2` | Foreign segments are present on your link (informational). |

> Survey only networks you are entitled to. `netsurvey` sends ordinary pings and
> standard discovery multicast — nothing privileged — but probing networks you
> don't administer may still violate policy or law.

### Wi-Fi

`wifi connect` lists everything in range — move with ↑/↓, press Enter to join,
Esc to cancel. The password prompt is hidden, skipped for open networks, and
skipped again for networks your system already remembers. When stdout isn't a
terminal the picker degrades to a numbered prompt, so the command still works
over pipes and in scripts.

Each subcommand drives the platform's own tooling, so no extra dependency or
driver access is needed:

| OS | Tooling used | Notes |
|----|--------------|-------|
| Linux | `nmcli` (NetworkManager) | The Ubuntu default. Systems without NetworkManager aren't supported. See the sudo note below. |
| Windows | `netsh` | `wifi on` / `wifi off` enable and disable the adapter, which needs an Administrator terminal. |
| macOS | `networksetup` | **No scanning** — see below. `connect`, `on`, `off` and `forget` all work; `wifi forget` edits the preferred-networks list and may need `sudo`. |

### Scanning support

| | `list` | `connect <ssid>` | `connect` (picker) | `on` / `off` | `forget` |
|---|---|---|---|---|---|
| Linux | ✅ | ✅ | networks in range | ✅ | ✅ |
| Windows | ✅ | ✅ | networks in range | ✅ | ✅ |
| macOS | ❌ | ✅ | saved networks | ✅ | ✅ |

> `--password` exists for automation but lands in your shell history — prefer the
> interactive prompt. On Linux, NetworkManager itself takes the passphrase as a
> command-line argument, so it is briefly visible in the process list.

#### Linux: polkit and sudo

NetworkManager's polkit rules usually let a local desktop session toggle Wi-Fi
without a password, but deny the same thing over SSH. When an operation is
refused, `wifi` says so and re-runs **just that `nmcli` command** under `sudo`,
which prompts for your password on the terminal:

```
Error: Failed to set radio: Not authorized to enable/disable WiFi.
Retrying with sudo (you may be asked for your password) ...
```

The escalation only happens on an interactive terminal. In a script or pipeline
the command fails with the permission error instead of hanging on a prompt.

> If you have a shell function or alias named `wifi` (a common `nmcli` wrapper),
> it takes precedence over this command — shell functions win over `PATH`. Use
> `devbits wifi ...`, or remove the function.

#### macOS: no scanning

`wifi list` is unsupported on macOS, and `wifi connect` without an SSID picks
from the networks this Mac already **remembers** rather than what's in range:

```
$ devbits wifi connect
Listing nearby Wi-Fi networks is not supported on macOS. ...

Falling back to your saved networks.
Select a network to join:
❯ MyHome-5G                     (saved)
  CoffeeShop                    (saved)
```

Joining by name — `wifi connect MyHome-5G` — always works, as do `on`, `off`
and `forget`.

The reason is that the last remaining macOS API that enumerates networks,
`system_profiler SPAirPortDataType`, replaces every SSID with the literal string
`<redacted>` unless the calling process holds Location Services authorization.
That is a TCC privacy permission, not a file permission: `sudo` does not bypass
it, the authorization database is SIP-protected, and a CLI cannot request it —
only a bundled app linking CoreLocation can. Rather than ship a pyobjc
dependency for one platform, devbits doesn't scan on macOS at all.

## Output Defaults

When `-o` / `--output` is omitted, the output filename is derived from the input:

```
editvideo movie.mp4          →  movie_clip.mp4
video2gif movie.mp4          →  movie.gif
resizeimage photo.jpg        →  photo_resized.jpg
recolor logo.png             →  logo_revised.png
contactsheet ./photos        →  photos_sheet.jpg
```

## License

MIT
