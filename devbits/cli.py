from __future__ import annotations

import argparse
import ipaddress
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .cache import clear_cache
from .image import batch_images, check_images, contact_sheet, image_to_ico, recolor_image, resize_image
from .media import clip_video, images_to_gif, images_to_video, resize_video, video_to_gif, video_to_images
from .network import scan_network
from .project import print_tree, rename_files, sample_files, top_sizes
from .utils import ensure_exists


# ---------------------------------------------------------------------------
# Helper: terminal colors
# ---------------------------------------------------------------------------

_ANSI = {
    "dir": "\033[1;34m",
    "file": "\033[0m",
    "size": "\033[36m",
    "header": "\033[1m",
    "self": "\033[1;32m",
    "gateway": "\033[1;33m",
    "mac": "\033[36m",
    "host": "\033[35m",
    "vendor": "\033[33m",
    "ssid": "\033[1;36m",
    "signal": "\033[32m",
    "lock": "\033[33m",
    "dim": "\033[2m",
    "segment": "\033[1;36m",
    "warn": "\033[1;31m",
    "note": "\033[2m",
    "reset": "\033[0m",
}


def _use_color(force: bool | None = None) -> bool:
    """Whether to emit ANSI colors: only on a TTY, unless overridden.

    Honors the ``NO_COLOR`` convention (https://no-color.org). ``force`` of
    ``True``/``False`` bypasses auto-detection (e.g. from a ``--no-color`` flag).
    """
    if force is not None:
        return force
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _colorize(text: str, kind: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_ANSI[kind]}{text}{_ANSI['reset']}"


# ---------------------------------------------------------------------------
# Helper: derive default output path from input path
# ---------------------------------------------------------------------------

def _derive_output(input_path: Path, suffix: str, tag: str = "") -> Path:
    """Build ``<stem>[_<tag>].<suffix>`` next to the input file."""
    stem = input_path.stem
    name = f"{stem}_{tag}{suffix}" if tag else f"{stem}{suffix}"
    return input_path.parent / name


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devbits",
        description="Daily development utility CLI toolkit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"devbits {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── clearcache ─────────────────────────────────────────────
    p = sub.add_parser(
        "clearcache",
        help="Clear Python cache files under a folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Recursively remove __pycache__ directories under the given folder.\n"
            "With --all, also removes .pytest_cache, .mypy_cache, and .ruff_cache."
        ),
    )
    p.add_argument("folder", type=Path, help="Root folder to scan.")
    p.add_argument("--all", action="store_true", help="Also remove pytest / mypy / ruff caches.")
    p.add_argument("--dry-run", action="store_true", help="List targets without deleting.")
    p.set_defaults(func=cmd_clearcache)

    # ── images2video ───────────────────────────────────────────
    p = sub.add_parser(
        "images2video",
        help="Convert image sequence to MP4 video.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Read images from a folder (sorted by name), encode them into an MP4\n"
            "video at the specified frame rate.\n\n"
            "Examples:\n"
            "  devbits images2video ./frames\n"
            "  devbits images2video ./frames --fps 60 --pattern '*.png'"
        ),
    )
    p.add_argument("folder", type=Path, help="Folder containing source images.")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output video path. Default: <folder_name>.mp4")
    p.add_argument("--fps", type=float, default=30.0,
                   help="Frame rate (frames per second). Default: 30.0")
    p.add_argument("--pattern", default="*",
                   help="Glob pattern to filter images, e.g. '*.png'. Default: '*'")
    p.set_defaults(func=cmd_images2video)

    # ── video2images ───────────────────────────────────────────
    p = sub.add_parser(
        "video2images",
        help="Extract frames from a video.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Decode a video file and save individual frames as images.\n\n"
            "Examples:\n"
            "  devbits video2images movie.mp4\n"
            "  devbits video2images movie.mp4 --every 5 --format png"
        ),
    )
    p.add_argument("video", type=Path, help="Input video file.")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output folder for frames. Default: <video_stem>_frames/")
    p.add_argument("--every", type=int, default=1,
                   help="Save every N-th frame (1 = all frames). Default: 1")
    p.add_argument("--prefix", default="frame",
                   help="Filename prefix for saved frames. Default: 'frame'")
    p.add_argument("--digits", type=int, default=6,
                   help="Number of zero-padded digits in filenames. Default: 6")
    p.add_argument("--format", default="jpg",
                   help="Image format for saved frames (jpg, png, bmp). Default: 'jpg'")
    p.set_defaults(func=cmd_video2images)

    # ── images2gif ─────────────────────────────────────────────
    p = sub.add_parser(
        "images2gif",
        help="Convert image sequence to GIF.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Read images from a folder (sorted by name) and assemble them into\n"
            "an animated GIF.\n\n"
            "Examples:\n"
            "  devbits images2gif ./frames\n"
            "  devbits images2gif ./frames --fps 15 --pattern '*.png'"
        ),
    )
    p.add_argument("folder", type=Path, help="Folder containing source images.")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output GIF path. Default: <folder_name>.gif")
    p.add_argument("--fps", type=float, default=10.0,
                   help="Frame rate (frames per second). Default: 10.0")
    p.add_argument("--pattern", default="*",
                   help="Glob pattern to filter images. Default: '*'")
    p.set_defaults(func=cmd_images2gif)

    # ── video2gif ──────────────────────────────────────────────
    p = sub.add_parser(
        "video2gif",
        help="Convert video to GIF.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Convert a video file (or a portion of it) into an animated GIF.\n\n"
            "Time arguments (--start, --end) are in seconds.\n\n"
            "Examples:\n"
            "  devbits video2gif movie.mp4\n"
            "  devbits video2gif movie.mp4 --start 3.5 --end 10.0 --fps 15\n"
            "  devbits video2gif movie.mp4 --size 640,360"
        ),
    )
    p.add_argument("video", type=Path, help="Input video file.")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output GIF path. Default: <video_stem>.gif")
    p.add_argument("--fps", type=float, default=10.0,
                   help="GIF frame rate (frames per second). Default: 10.0")
    p.add_argument("--start", type=float, metavar="SEC",
                   help="Start time in seconds (from the beginning of the video).")
    p.add_argument("--end", type=float, metavar="SEC",
                   help="End time in seconds (from the beginning of the video).")
    p.add_argument("--size", metavar="W,H",
                   help="Output size as 'width,height' in pixels, e.g. 640,360.")
    p.set_defaults(func=cmd_video2gif)

    # ── clipvideo ──────────────────────────────────────────────
    p = sub.add_parser(
        "clipvideo",
        help="Clip (trim) a video by time range or frame range.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Trim a portion of a video. You can specify the range in seconds\n"
            "(--start / --end) or in frame indices (--start-frame / --end-frame).\n"
            "If no range is given, the interactive browser-based editor opens.\n\n"
            "Examples:\n"
            "  devbits clipvideo movie.mp4                 # opens the GUI editor\n"
            "  devbits clipvideo movie.mp4 --start 5.0 --end 20.0\n"
            "  devbits clipvideo movie.mp4 --start-frame 150 --end-frame 600\n"
            "  devbits clipvideo --gui                     # GUI with no initial video"
        ),
    )
    p.add_argument("video", type=Path, nargs="?", default=None,
                   help="Input video file (optional when --gui is used).")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output video path. Default: <video_stem>_clip.mp4")
    p.add_argument("--start", type=float, metavar="SEC",
                   help="Start time in seconds.")
    p.add_argument("--end", type=float, metavar="SEC",
                   help="End time in seconds.")
    p.add_argument("--start-frame", type=int, metavar="IDX",
                   help="Start frame index (0-based).")
    p.add_argument("--end-frame", type=int, metavar="IDX",
                   help="End frame index (0-based, inclusive).")
    p.add_argument("--gui", action="store_true",
                   help="Open interactive browser-based clip editor.")
    p.set_defaults(func=cmd_clipvideo)

    # ── resizevideo ────────────────────────────────────────────
    p = sub.add_parser(
        "resizevideo",
        help="Resize a video to a specified resolution.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Re-encode a video at a different resolution.\n\n"
            "Examples:\n"
            "  devbits resizevideo movie.mp4 --size 1280,720\n"
            "  devbits resizevideo movie.mp4 --size 640,480 -o small.mp4"
        ),
    )
    p.add_argument("video", type=Path, help="Input video file.")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output video path. Default: <video_stem>_resized.mp4")
    p.add_argument("--size", required=True, metavar="W,H",
                   help="Target resolution as 'width,height' in pixels, e.g. 1280,720.")
    p.set_defaults(func=cmd_resizevideo)

    # ── image2ico ──────────────────────────────────────────────
    p = sub.add_parser(
        "image2ico",
        help="Convert an image to a multi-size ICO file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Generate a Windows ICO icon file containing multiple sizes.\n\n"
            "Examples:\n"
            "  devbits image2ico logo.png\n"
            "  devbits image2ico logo.png --sizes 32,64,128"
        ),
    )
    p.add_argument("image", type=Path, help="Input image file.")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output ICO path. Default: <image_stem>.ico")
    p.add_argument("--sizes", default="16,32,48,64,128,256",
                   help="Comma-separated icon sizes in pixels. Default: 16,32,48,64,128,256")
    p.set_defaults(func=cmd_image2ico)

    # ── resizeimage ────────────────────────────────────────────
    p = sub.add_parser(
        "resizeimage",
        help="Resize an image to a specified size.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Resize a single image. By default, aspect ratio is preserved.\n\n"
            "Examples:\n"
            "  devbits resizeimage photo.jpg --size 800,600\n"
            "  devbits resizeimage photo.png --size 256,256 --no-keep-ratio"
        ),
    )
    p.add_argument("image", type=Path, help="Input image file.")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output image path. Default: <image_stem>_resized.<ext>")
    p.add_argument("--size", required=True, metavar="W,H",
                   help="Target size as 'width,height' in pixels, e.g. 800,600.")
    p.add_argument("--no-keep-ratio", action="store_true",
                   help="Do not preserve aspect ratio; stretch to exact size.")
    p.set_defaults(func=cmd_resizeimage)

    # ── recolor ────────────────────────────────────────────────
    p = sub.add_parser(
        "recolor",
        help="Recolor the foreground of a logo / icon image.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Recolor a logo or icon. The background (transparent or a lighter\n"
            "surrounding color) is detected automatically and left untouched,\n"
            "while every foreground pixel is repainted with the target color.\n"
            "The result is always saved as an RGBA PNG.\n\n"
            "Examples:\n"
            "  devbits recolor logo.png\n"
            "  devbits recolor logo.png --color '#1a73e8'\n"
            "  devbits recolor logo.png --color 0,178,179\n"
            "  devbits recolor icon.jpg --color white --threshold 90"
        ),
    )
    p.add_argument("image", type=Path, help="Input logo / icon image.")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output image path. Default: <image_stem>_revised.png")
    p.add_argument("--color", default="black",
                   help="Target foreground color: name, hex, or R,G,B (e.g. black, '#1a73e8', 0,178,179). Default: black")
    p.add_argument("--threshold", type=int, default=60,
                   help="Color distance from the background for opaque images. Default: 60")
    p.set_defaults(func=cmd_recolor)

    # ── batchimages ────────────────────────────────────────────
    p = sub.add_parser(
        "batchimages",
        help="Batch resize or convert images in a folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Process all images in a folder: resize, convert format, or both.\n\n"
            "Examples:\n"
            "  devbits batchimages ./photos -o ./resized --size 800,600\n"
            "  devbits batchimages ./raw -o ./converted --format png"
        ),
    )
    p.add_argument("folder", type=Path, help="Folder containing source images.")
    p.add_argument("-o", "--output", type=Path, required=True,
                   help="Output folder for processed images (required).")
    p.add_argument("--size", metavar="W,H",
                   help="Target size as 'width,height' in pixels.")
    p.add_argument("--format",
                   help="Convert images to this format (jpg, png, bmp, etc.).")
    p.set_defaults(func=cmd_batchimages)

    # ── checkimages ────────────────────────────────────────────
    p = sub.add_parser(
        "checkimages",
        help="Scan for broken / corrupt image files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan a folder for image files that cannot be opened or decoded.\n\n"
            "Examples:\n"
            "  devbits checkimages ./photos\n"
            "  devbits checkimages ./photos --recursive --remove-broken"
        ),
    )
    p.add_argument("folder", type=Path, help="Folder to scan.")
    p.add_argument("--recursive", action="store_true",
                   help="Scan sub-folders recursively.")
    p.add_argument("--remove-broken", action="store_true",
                   help="Delete broken image files after detection.")
    p.set_defaults(func=cmd_checkimages)

    # ── contactsheet ───────────────────────────────────────────
    p = sub.add_parser(
        "contactsheet",
        help="Create a contact sheet (thumbnail grid) from images.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Generate a single image containing a grid of thumbnails from all\n"
            "images in a folder.\n\n"
            "Examples:\n"
            "  devbits contactsheet ./photos\n"
            "  devbits contactsheet ./photos --cols 8 --thumb-size 128,128 --labels"
        ),
    )
    p.add_argument("folder", type=Path, help="Folder containing source images.")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output image path. Default: <folder_name>_sheet.jpg")
    p.add_argument("--cols", type=int, default=5,
                   help="Number of columns in the grid. Default: 5")
    p.add_argument("--thumb-size", default="256,256", metavar="W,H",
                   help="Thumbnail size as 'width,height' in pixels. Default: 256,256")
    p.add_argument("--labels", action="store_true",
                   help="Print filenames below each thumbnail.")
    p.set_defaults(func=cmd_contactsheet)

    # ── tree ───────────────────────────────────────────────────
    p = sub.add_parser(
        "tree",
        help="Print a project directory tree.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Display the directory structure as an indented tree.\n\n"
            "Examples:\n"
            "  devbits tree\n"
            "  devbits tree ./src --depth 5"
        ),
    )
    p.add_argument("folder", type=Path, nargs="?", default=Path("."),
                   help="Root folder to display. Default: current directory")
    p.add_argument("--depth", type=int, default=3,
                   help="Maximum depth to traverse. Default: 3")
    p.add_argument("--no-color", action="store_true",
                   help="Disable colored output (also honors NO_COLOR).")
    p.set_defaults(func=cmd_tree)

    # ── size ───────────────────────────────────────────────────
    p = sub.add_parser(
        "size",
        help="Show top-level folder / file sizes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "List the largest items under a folder, sorted by size.\n\n"
            "Examples:\n"
            "  devbits size\n"
            "  devbits size /data --top 50"
        ),
    )
    p.add_argument("folder", type=Path, nargs="?", default=Path("."),
                   help="Folder to inspect. Default: current directory")
    p.add_argument("--top", type=int, default=20,
                   help="Number of items to show. Default: 20")
    p.add_argument("--no-color", action="store_true",
                   help="Disable colored output (also honors NO_COLOR).")
    p.set_defaults(func=cmd_size)

    # ── renamefiles ────────────────────────────────────────────
    p = sub.add_parser(
        "renamefiles",
        help="Batch rename files sequentially.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Rename all files in a folder to a sequential numbered pattern:\n"
            "  <prefix><number>.<ext>\n\n"
            "Examples:\n"
            "  devbits renamefiles ./photos --prefix img --digits 4\n"
            "  devbits renamefiles ./data --start 100 --dry-run"
        ),
    )
    p.add_argument("folder", type=Path, help="Folder containing files to rename.")
    p.add_argument("--prefix", default="file",
                   help="Filename prefix. Default: 'file'")
    p.add_argument("--digits", type=int, default=6,
                   help="Number of zero-padded digits. Default: 6")
    p.add_argument("--start", type=int, default=1,
                   help="Starting number. Default: 1")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview renames without applying.")
    p.set_defaults(func=cmd_renamefiles)

    # ── samplefiles ────────────────────────────────────────────
    p = sub.add_parser(
        "samplefiles",
        help="Copy (or move) the first N files to another folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Pick the first N files (sorted by name) from a folder and copy\n"
            "(or move) them to a destination folder.\n\n"
            "Examples:\n"
            "  devbits samplefiles ./photos -o ./sample --num 50\n"
            "  devbits samplefiles ./data -o ./subset --num 10 --move"
        ),
    )
    p.add_argument("folder", type=Path, help="Source folder.")
    p.add_argument("-o", "--output", type=Path, required=True,
                   help="Destination folder (required).")
    p.add_argument("--num", type=int, required=True,
                   help="Number of files to sample.")
    p.add_argument("--move", action="store_true",
                   help="Move files instead of copying.")
    p.set_defaults(func=cmd_samplefiles)

    # ── netscan ────────────────────────────────────────────────
    p = sub.add_parser(
        "netscan",
        help="List devices connected to the local network (Wi-Fi / router).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Discover hosts on your local subnet with a threaded ping sweep, then\n"
            "report each device's IP, MAC address, and hostname. This machine and\n"
            "the router (default gateway) are highlighted.\n\n"
            "Scan only your own network; probing networks you don't administer may\n"
            "violate policy or law.\n\n"
            "Examples:\n"
            "  devbits netscan\n"
            "  devbits netscan --lookup                    # also show manufacturer\n"
            "  devbits netscan --network 192.168.1.0/24\n"
            "  devbits netscan --timeout 0.5 --workers 128 --no-resolve"
        ),
    )
    p.add_argument("--network", metavar="CIDR", default=None,
                   help="Subnet to scan in CIDR, e.g. 192.168.1.0/24. Default: auto-detected.")
    p.add_argument("--timeout", type=float, default=1.0,
                   help="Per-host ping timeout in seconds. Default: 1.0")
    p.add_argument("--workers", type=int, default=64,
                   help="Number of concurrent ping workers. Default: 64")
    p.add_argument("--no-resolve", action="store_true",
                   help="Skip reverse-DNS hostname lookups (faster).")
    p.add_argument("--lookup", action="store_true",
                   help="Resolve each device's manufacturer online via macvendors.com "
                        "(sends MAC prefixes to a third-party service).")
    p.add_argument("--no-color", action="store_true",
                   help="Disable colored output (also honors NO_COLOR).")
    p.set_defaults(func=cmd_netscan)

    # ── netsurvey ──────────────────────────────────────────────
    p = sub.add_parser(
        "netsurvey",
        help="Map every network segment and address in use around you.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Survey the whole environment instead of a single subnet: list your own\n"
            "interfaces and their ranges, every segment visible on the same wire, the\n"
            "addresses in use in each, and the clashes between them.\n\n"
            "Built for shared networks — a trade-show hall, an office, a co-working\n"
            "space — where someone else's router hands out an overlapping range and\n"
            "traffic quietly goes to the wrong place. Reports duplicate IPs, ranges\n"
            "that overlap your own, links with no DHCP, and routers bridging two\n"
            "segments.\n\n"
            "The default pass is passive and takes a couple of seconds: it reads the\n"
            "ARP / neighbour cache (which lists neighbours on *foreign* subnets too)\n"
            "and asks for SSDP and mDNS answers. --sweep additionally ping-sweeps\n"
            "your ranges and the discovered ones so each address list is complete.\n\n"
            "Survey only networks you are entitled to; probing networks you don't\n"
            "administer may violate policy or law.\n\n"
            "Examples:\n"
            "  devbits netsurvey                              # quick passive map\n"
            "  devbits netsurvey --summary                     # segments only\n"
            "  devbits netsurvey --sweep                       # also enumerate every host\n"
            "  devbits netsurvey --include 192.168.1.0/24 --include 10.0.0.0/24\n"
            "  devbits netsurvey --sweep --group-prefix 16 --timeout 0.4"
        ),
    )
    p.add_argument("--sweep", action="store_true",
                   help="Also ping-sweep your own and the discovered segments (slower, complete).")
    p.add_argument("--include", metavar="CIDR", action="append", default=[],
                   help="Extra range to sweep, e.g. 10.0.0.0/24. Repeatable.")
    p.add_argument("--group-prefix", type=int, default=24, metavar="N",
                   help="Prefix used to group addresses outside your own subnets. Default: 24")
    p.add_argument("--timeout", type=float, default=0.6,
                   help="Per-host ping timeout in seconds. Default: 0.6")
    p.add_argument("--workers", type=int, default=128,
                   help="Number of concurrent ping workers. Default: 128")
    p.add_argument("--discover-timeout", type=float, default=2.0, metavar="SECONDS",
                   help="How long to listen for SSDP / mDNS answers. Default: 2.0")
    p.add_argument("--no-passive", action="store_true",
                   help="Skip the multicast discovery step; read the ARP cache only.")
    p.add_argument("--resolve", action="store_true",
                   help="Reverse-DNS each address for its hostname (slower).")
    p.add_argument("--max-sweep", type=int, default=4096, metavar="HOSTS",
                   help="Refuse to sweep a range larger than this. Default: 4096")
    p.add_argument("--summary", action="store_true",
                   help="List segments and conflicts only, without the addresses.")
    p.add_argument("--no-color", action="store_true",
                   help="Disable colored output (also honors NO_COLOR).")
    p.set_defaults(func=cmd_netsurvey)

    # ── wifi ───────────────────────────────────────────────────
    p = sub.add_parser(
        "wifi",
        help="Manage Wi-Fi: connect, power on/off, forget a network.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Control the Wi-Fi radio from the terminal on macOS, Windows and\n"
            "Linux (NetworkManager). Wraps the platform's own tooling:\n"
            "networksetup, netsh, and nmcli respectively.\n\n"
            "On Linux, operations NetworkManager refuses (common over SSH, where\n"
            "polkit denies what a desktop session is allowed) are retried under\n"
            "sudo after asking you.\n\n"
            "Examples:\n"
            "  devbits wifi list\n"
            "  devbits wifi connect                 # pick from a list, then type the password\n"
            "  devbits wifi connect MyHome-5G       # skip the picker\n"
            "  devbits wifi on\n"
            "  devbits wifi off\n"
            "  devbits wifi forget OldCafe"
        ),
    )
    wifi_sub = p.add_subparsers(dest="action", required=True)

    w = wifi_sub.add_parser(
        "connect",
        help="Scan for networks, pick one with the arrow keys, and join it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "List every Wi-Fi network in range and connect to the one you pick.\n"
            "Move with ↑/↓, confirm with Enter, cancel with Esc. The password is\n"
            "read without echoing; open networks skip the prompt, and networks\n"
            "your system already remembers are joined with the stored password.\n\n"
            "Pass an SSID to skip the picker (useful in scripts). --password is\n"
            "accepted for automation, but it lands in your shell history — prefer\n"
            "the interactive prompt.\n\n"
            "Examples:\n"
            "  devbits wifi connect\n"
            "  devbits wifi connect MyHome-5G\n"
            "  devbits wifi connect MyHome-5G --password 'secret'"
        ),
    )
    w.add_argument("ssid", nargs="?", default=None,
                   help="Network to join. Default: choose interactively.")
    w.add_argument("--password", default=None,
                   help="Password. Default: prompt (hidden) when one is needed.")
    w.add_argument("--interface", default=None,
                   help="Wi-Fi interface / adapter to use. Default: auto-detected.")
    w.add_argument("--timeout", type=float, default=30.0,
                   help="Seconds to wait for the connection. Default: 30.0")
    w.add_argument("--no-color", action="store_true",
                   help="Disable colored output (also honors NO_COLOR).")
    w.set_defaults(func=cmd_wifi_connect)

    w = wifi_sub.add_parser(
        "list",
        help="List the Wi-Fi networks in range.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Show every Wi-Fi network in range with its signal strength and\n"
            "security, strongest first. Networks broadcasting on several bands\n"
            "or access points are merged into one entry.\n\n"
            "Examples:\n"
            "  devbits wifi list\n"
            "  devbits wifi list --no-color"
        ),
    )
    w.add_argument("--interface", default=None,
                   help="Wi-Fi interface / adapter to use. Default: auto-detected.")
    w.add_argument("--no-color", action="store_true",
                   help="Disable colored output (also honors NO_COLOR).")
    w.set_defaults(func=cmd_wifi_list)

    w = wifi_sub.add_parser(
        "on",
        help="Turn the Wi-Fi radio on.",
        description="Power on the Wi-Fi adapter. On Windows this needs an Administrator terminal.",
    )
    w.add_argument("--interface", default=None,
                   help="Wi-Fi interface / adapter to use. Default: auto-detected.")
    w.set_defaults(func=cmd_wifi_on)

    w = wifi_sub.add_parser(
        "off",
        help="Turn the Wi-Fi radio off.",
        description="Power off the Wi-Fi adapter. On Windows this needs an Administrator terminal.",
    )
    w.add_argument("--interface", default=None,
                   help="Wi-Fi interface / adapter to use. Default: auto-detected.")
    w.set_defaults(func=cmd_wifi_off)

    w = wifi_sub.add_parser(
        "forget",
        help="Forget a saved network so it stops auto-connecting.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Remove a network from the list this machine remembers, so it is no\n"
            "longer joined automatically. Without an SSID, pick one from the saved\n"
            "networks with the arrow keys.\n\n"
            "On macOS this edits the preferred-networks list and may need sudo.\n\n"
            "Examples:\n"
            "  devbits wifi forget\n"
            "  devbits wifi forget OldCafe"
        ),
    )
    w.add_argument("ssid", nargs="?", default=None,
                   help="Network to forget. Default: choose interactively.")
    w.add_argument("--interface", default=None,
                   help="Wi-Fi interface / adapter to use. Default: auto-detected.")
    w.add_argument("--yes", action="store_true",
                   help="Skip the confirmation prompt.")
    w.add_argument("--no-color", action="store_true",
                   help="Disable colored output (also honors NO_COLOR).")
    w.set_defaults(func=cmd_wifi_forget)

    return parser


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_clearcache(args: argparse.Namespace) -> None:
    targets = clear_cache(ensure_exists(args.folder), include_extra=args.all, dry_run=args.dry_run)
    for target in targets:
        print(target)
    print(f"{'Found' if args.dry_run else 'Removed'} {len(targets)} cache item(s).")


def cmd_images2video(args: argparse.Namespace) -> None:
    folder = ensure_exists(args.folder)
    output = args.output or _derive_output(folder, ".mp4")
    print(images_to_video(folder, output, args.fps, args.pattern))


def cmd_video2images(args: argparse.Namespace) -> None:
    video = ensure_exists(args.video)
    output = args.output or (video.parent / f"{video.stem}_frames")
    outputs = video_to_images(video, output, args.every, args.prefix, args.digits, args.format)
    print(f"Saved {len(outputs)} frame(s) to {output}")


def cmd_images2gif(args: argparse.Namespace) -> None:
    folder = ensure_exists(args.folder)
    output = args.output or _derive_output(folder, ".gif")
    print(images_to_gif(folder, output, args.fps, args.pattern))


def cmd_video2gif(args: argparse.Namespace) -> None:
    video = ensure_exists(args.video)
    output = args.output or _derive_output(video, ".gif")
    print(video_to_gif(video, output, args.fps, args.start, args.end, args.size))


def cmd_clipvideo(args: argparse.Namespace) -> None:
    has_range = any(v is not None for v in (args.start, args.end, args.start_frame, args.end_frame))
    # Open the interactive editor when --gui is set, or by default when a video
    # is provided without an explicit trim range.
    if args.gui or (args.video is not None and not has_range):
        from .gui import launch_gui
        launch_gui(args.video)
        return
    if args.video is None:
        raise ValueError("video path is required when --gui is not specified")
    video = ensure_exists(args.video)
    output = args.output or _derive_output(video, ".mp4", "clip")
    print(clip_video(video, output, args.start, args.end, args.start_frame, args.end_frame))


def cmd_resizevideo(args: argparse.Namespace) -> None:
    video = ensure_exists(args.video)
    output = args.output or _derive_output(video, ".mp4", "resized")
    print(resize_video(video, output, args.size))


def cmd_image2ico(args: argparse.Namespace) -> None:
    image = ensure_exists(args.image)
    output = args.output or _derive_output(image, ".ico")
    print(image_to_ico(image, output, args.sizes))


def cmd_resizeimage(args: argparse.Namespace) -> None:
    image = ensure_exists(args.image)
    output = args.output or _derive_output(image, image.suffix, "resized")
    print(resize_image(image, output, args.size, not args.no_keep_ratio))


def cmd_recolor(args: argparse.Namespace) -> None:
    image = ensure_exists(args.image)
    output = args.output or _derive_output(image, ".png", "revised")
    print(recolor_image(image, output, args.color, args.threshold))


def cmd_batchimages(args: argparse.Namespace) -> None:
    outputs = batch_images(ensure_exists(args.folder), args.output, args.size, args.format)
    print(f"Saved {len(outputs)} image(s) to {args.output}")


def cmd_checkimages(args: argparse.Namespace) -> None:
    broken = check_images(ensure_exists(args.folder), args.recursive, args.remove_broken)
    if broken:
        print("Broken images:")
        for path in broken:
            print(path)
    print(f"Found {len(broken)} broken image(s).")


def cmd_contactsheet(args: argparse.Namespace) -> None:
    folder = ensure_exists(args.folder)
    output = args.output or (folder.parent / f"{folder.name}_sheet.jpg")
    print(contact_sheet(folder, output, args.cols, args.thumb_size, labels=args.labels))


def cmd_tree(args: argparse.Namespace) -> None:
    color = _use_color(False if args.no_color else None)
    for line in print_tree(ensure_exists(args.folder), args.depth):
        if not color:
            print(line)
            continue
        # Color only the name, leaving the tree-drawing prefix untouched.
        marker = "── "
        idx = line.rfind(marker)
        split = idx + len(marker) if idx != -1 else 0
        prefix, name = line[:split], line[split:]
        kind = "dir" if name.endswith("/") else "file"
        print(f"{prefix}{_colorize(name, kind, color)}")


def cmd_size(args: argparse.Namespace) -> None:
    color = _use_color(False if args.no_color else None)
    for path, _, size_text in top_sizes(ensure_exists(args.folder), args.top):
        kind = "dir" if path.is_dir() else "file"
        label = f"{path}{os.sep}" if path.is_dir() else str(path)
        size_col = _colorize(f"{size_text:>10}", "size", color)
        name_col = _colorize(label, kind, color)
        print(f"{size_col}  {name_col}")


def cmd_renamefiles(args: argparse.Namespace) -> None:
    mappings = rename_files(ensure_exists(args.folder), args.prefix, args.digits, args.start, args.dry_run)
    for old, new in mappings:
        print(f"{old.name} -> {new.name}")
    print(f"{'Planned' if args.dry_run else 'Renamed'} {len(mappings)} file(s).")


def cmd_samplefiles(args: argparse.Namespace) -> None:
    outputs = sample_files(ensure_exists(args.folder), args.output, args.num, copy=not args.move)
    print(f"Saved {len(outputs)} file(s) to {args.output}")


def cmd_netscan(args: argparse.Namespace) -> None:
    color = _use_color(False if args.no_color else None)
    network = ipaddress.ip_network(args.network, strict=False) if args.network else None

    from .network import default_network
    net = network or default_network()
    print(f"Scanning {net} ...", file=sys.stderr)

    if args.lookup:
        print("Looking up device manufacturers online ...", file=sys.stderr)
    devices = scan_network(
        net, timeout=args.timeout, workers=args.workers,
        resolve=not args.no_resolve, lookup=args.lookup,
    )

    vendor_col_head = f"{'VENDOR':<24}" if args.lookup else ""
    header = f"{'IP':<16}{'MAC':<20}{vendor_col_head}{'HOSTNAME':<28}NOTE"
    print(_colorize(header, "header", color))
    for device in devices:
        note = "this device" if device.is_self else ("gateway / router" if device.is_gateway else "")
        kind = "self" if device.is_self else ("gateway" if device.is_gateway else None)
        ip_col = _colorize(f"{device.ip:<16}", kind, color) if kind else f"{device.ip:<16}"
        mac_col = _colorize(f"{device.mac or '-':<20}", "mac", color)
        vendor_col = _colorize(f"{device.vendor or '-':<24}", "vendor", color) if args.lookup else ""
        host_col = _colorize(f"{device.hostname or '-':<28}", "host", color)
        print(f"{ip_col}{mac_col}{vendor_col}{host_col}{note}")
    print(f"Found {len(devices)} device(s).")


def _survey_source_label(sources: set[str], width: int = 22) -> str:
    """Condense a device's discovery sources into a stable, readable column."""
    order = ["self", "gateway", "arp", "ping", "ssdp", "mdns"]
    label = ",".join(source for source in order if source in sources) or "-"
    if len(label) > width - 1:
        label = label[: width - 2] + "…"
    return f"{label:<{width}}"


def cmd_netsurvey(args: argparse.Namespace) -> None:
    from .network import survey_network

    color = _use_color(False if args.no_color else None)
    if not 8 <= args.group_prefix <= 32:
        raise ValueError("--group-prefix must be between 8 and 32")

    survey, notes = survey_network(
        include=args.include,
        group_prefix=args.group_prefix,
        sweep=args.sweep,
        passive=not args.no_passive,
        timeout=args.timeout,
        workers=args.workers,
        discover_timeout=args.discover_timeout,
        resolve=args.resolve,
        max_sweep_hosts=args.max_sweep,
        progress=lambda message: print(message, file=sys.stderr),
    )

    print(_colorize("INTERFACES", "header", color))
    for nic in survey.interfaces:
        note = "gateway " + survey.gateway if survey.gateway and \
            ipaddress.ip_address(survey.gateway) in nic.network else ""
        if nic.is_link_local:
            note = "self-assigned (no DHCP)"
        name_col = _colorize(f"{nic.name:<12}", "self" if nic.ip == survey.self_ip else "dim", color)
        print(f"  {name_col}{f'{nic.ip}/{nic.prefix}':<22}{_colorize(note, 'note', color)}".rstrip())

    print()
    local_count = sum(1 for segment in survey.segments if segment.is_local)
    print(_colorize(
        f"SEGMENTS  ({len(survey.segments)} total, {local_count} yours, "
        f"{survey.device_count} address(es) in use)", "header", color,
    ))
    for segment in survey.segments:
        scope = f"local ({', '.join(segment.interfaces)})" if segment.is_local else "foreign"
        how = "swept" if segment.swept else "passive"
        head = f"  {str(segment.network):<20}{scope:<24}{how:<10}{len(segment.devices)} host(s)"
        print(_colorize(head.rstrip(), "segment" if segment.is_local else "warn", color))
        if args.summary:
            continue
        for device in segment.devices:
            note = "this device" if device.is_self else ("gateway / router" if device.is_gateway else "")
            kind = "self" if device.is_self else ("gateway" if device.is_gateway else None)
            ip_col = _colorize(f"{device.ip:<16}", kind, color) if kind else f"{device.ip:<16}"
            mac_col = _colorize(f"{device.mac or '-':<20}", "mac", color)
            host_col = _colorize(f"{device.hostname or '-':<28}", "host", color) if args.resolve else ""
            seen_col = _colorize(_survey_source_label(device.sources), "note", color)
            print(f"    {ip_col}{mac_col}{host_col}{seen_col}{note}".rstrip())

    print()
    if survey.conflicts:
        print(_colorize(f"CONFLICTS  ({len(survey.conflicts)})", "header", color))
        for conflict in survey.conflicts:
            marker = "!" if conflict.kind != "shared-l2" else "-"
            kind = "warn" if conflict.kind != "shared-l2" else "note"
            print(_colorize(f"  {marker} [{conflict.kind}] {conflict.message}", kind, color))
    else:
        print("No address or range conflicts detected.")

    for note in notes:
        print(_colorize(f"  note: {note}", "note", color), file=sys.stderr)
    if not args.sweep and not args.include:
        print(_colorize(
            "Passive pass only — run with --sweep to enumerate every address in each segment.",
            "note", color,
        ), file=sys.stderr)


# ---------------------------------------------------------------------------
# wifi
# ---------------------------------------------------------------------------

def _unicode_ok() -> bool:
    return "utf" in (getattr(sys.stderr, "encoding", None) or "").lower()


def _signal_bar(percent: int | None, cells: int = 4) -> str:
    """A tiny bar chart for a 0-100 signal quality."""
    filled_char, empty_char = ("█", "░") if _unicode_ok() else ("#", ".")
    if percent is None:
        return empty_char * cells
    filled = max(0, min(cells, round(percent / (100 / cells))))
    return filled_char * filled + empty_char * (cells - filled)


def _wifi_ssid_width(networks: list) -> int:
    """Column width for the SSID field, measured in terminal cells."""
    from .tui import visible_len

    # Pad by terminal columns, not characters, so CJK SSIDs still line up.
    return min(32, max([visible_len(n.ssid) for n in networks] + [12]))


def _run_privileged(action, *args) -> None:
    """Run a Wi-Fi operation, offering a sudo retry if the OS refuses it.

    NetworkManager's polkit rules typically allow a local desktop session to
    change Wi-Fi state but deny it over SSH; re-running just the refused
    command under sudo is what a user would do by hand.
    """
    from . import wifi

    try:
        action(*args)
    except wifi.PermissionRequired as exc:
        if not (sys.stdin.isatty() and shutil.which("sudo")):
            raise
        print(f"{exc}\nRetrying with sudo (you may be asked for your password) ...",
              file=sys.stderr)
        wifi.retry_with_sudo(exc.command)


def _wifi_rows(networks: list, color: bool) -> list[str]:
    """Render scanned networks as aligned rows for the selection list."""
    from .tui import pad

    width = _wifi_ssid_width(networks)
    # Remembered networks listed without a scan have neither signal nor
    # security; drop those columns rather than print a row of placeholders.
    detailed = any(n.signal is not None or n.security is not None for n in networks)
    rows = []
    for net in networks:
        tags = []
        if net.in_use:
            tags.append("connected")
        elif net.saved:
            tags.append("saved")
        if net.is_open:
            tags.append("open")
        row = _colorize(pad(net.ssid, width), "ssid", color)
        if detailed:
            signal = f"{net.signal:>3}%" if net.signal is not None else "  ?%"
            row += "  " + _colorize(f"{_signal_bar(net.signal)} {signal}", "signal", color)
            row += "  " + _colorize(pad(net.security or "-", 16), "lock", color)
        if tags:
            row += _colorize(f"  ({', '.join(tags)})", "dim", color)
        rows.append(row)
    return rows


def cmd_wifi_connect(args: argparse.Namespace) -> None:
    from getpass import getpass

    from . import wifi
    from .tui import select

    color = _use_color(False if args.no_color else None)
    target = None
    ssid = args.ssid

    if ssid is None:
        if wifi.supports_scanning():
            print("Scanning for Wi-Fi networks ...", file=sys.stderr)
        try:
            networks = wifi.scan_networks(args.interface)
        except wifi.ScanBlocked as exc:
            # No scanning on this platform (macOS). The remembered networks are
            # still readable, and joining one of those needs no scan at all.
            saved = _saved_or_empty(args.interface)
            if not saved:
                raise
            print(f"{exc}\n\nFalling back to your saved networks.", file=sys.stderr)
            networks = [wifi.Network(ssid=name, saved=True) for name in saved]
        if not networks:
            raise wifi.WifiError("No Wi-Fi networks in range.")
        index = select(
            _wifi_rows(networks, color),
            title=_colorize("Select a network to join:", "header", color),
            footer="↑/↓ move · Enter connect · Esc cancel" if _unicode_ok()
                   else "up/down move, Enter connect, Esc cancel",
            color=color,
        )
        if index is None:
            print("Cancelled.", file=sys.stderr)
            return
        target = networks[index]
        ssid = target.ssid

    is_open = target.is_open if target else False
    known = target.saved if target else ssid in _saved_or_empty(args.interface)

    password = args.password
    if password is None and not is_open and not known:
        password = getpass(f"Password for {ssid!r} (blank if open): ") or None

    print(f"Connecting to {ssid!r} ...", file=sys.stderr)

    def attempt(secret: str | None) -> None:
        _run_privileged(wifi.connect, ssid, secret, args.interface, args.timeout)

    try:
        attempt(password)
    except wifi.PermissionRequired:
        raise  # a privilege problem, not a wrong password — don't prompt
    except wifi.WifiError as exc:
        # A stored password can be stale or absent — offer to type one instead.
        if password is not None or is_open or not sys.stdin.isatty():
            raise
        # Not a failure yet — just means the OS had no usable stored password.
        print(f"No stored password could be used ({exc}). Enter it manually:", file=sys.stderr)
        password = getpass(f"Password for {ssid!r}: ") or None
        attempt(password)

    active = wifi.current_ssid(args.interface)
    if active is None or active == ssid:
        print(f"Connected to {ssid}.")
    else:
        print(f"Join requested for {ssid}, but the active network is {active}.")


def _saved_or_empty(interface: str | None) -> list[str]:
    from . import wifi

    try:
        return wifi.saved_networks(interface)
    except wifi.WifiError:
        return []


def cmd_wifi_list(args: argparse.Namespace) -> None:
    from . import wifi
    from .tui import pad

    color = _use_color(False if args.no_color else None)
    if wifi.supports_scanning():
        print("Scanning for Wi-Fi networks ...", file=sys.stderr)
    # Listing *is* the scan, so an unsupported platform is simply an error here.
    networks = wifi.scan_networks(args.interface)
    if not networks:
        print("No Wi-Fi networks in range.")
        return
    header = (
        pad("SSID", _wifi_ssid_width(networks)) + "  " + pad("SIGNAL", 9) + "  SECURITY"
    )
    print(_colorize(header, "header", color))
    for row in _wifi_rows(networks, color):
        print(row)
    print(f"Found {len(networks)} network(s).")


def _wifi_power(args: argparse.Namespace, enabled: bool) -> None:
    from . import wifi

    _run_privileged(wifi.set_radio, enabled, args.interface)
    state = wifi.radio_enabled(args.interface)
    word = "on" if enabled else "off"
    if state is None or state is enabled:
        print(f"Wi-Fi turned {word}.")
    else:
        print(f"Requested Wi-Fi {word}, but the adapter reports it is "
              f"{'on' if state else 'off'}.")


def cmd_wifi_on(args: argparse.Namespace) -> None:
    _wifi_power(args, True)


def cmd_wifi_off(args: argparse.Namespace) -> None:
    _wifi_power(args, False)


def cmd_wifi_forget(args: argparse.Namespace) -> None:
    from . import wifi
    from .tui import pad, select, visible_len

    color = _use_color(False if args.no_color else None)
    ssid = args.ssid

    if ssid is None:
        saved = wifi.saved_networks(args.interface)
        if not saved:
            raise wifi.WifiError("This machine has no saved Wi-Fi networks.")
        active = wifi.current_ssid(args.interface)
        width = min(32, max([visible_len(name) for name in saved] + [12]))
        rows = [
            _colorize(pad(name, width), "ssid", color)
            + (_colorize("  (connected)", "dim", color) if name == active else "")
            for name in saved
        ]
        index = select(
            rows,
            title=_colorize("Select a network to forget:", "header", color),
            footer="↑/↓ move · Enter forget · Esc cancel" if _unicode_ok()
                   else "up/down move, Enter forget, Esc cancel",
            color=color,
        )
        if index is None:
            print("Cancelled.", file=sys.stderr)
            return
        ssid = saved[index]

    if not args.yes and sys.stdin.isatty():
        try:
            answer = input(f"Forget {ssid!r} and stop auto-connecting? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            print("Cancelled.", file=sys.stderr)
            return

    _run_privileged(wifi.forget, ssid, args.interface)
    print(f"Forgot {ssid}. It will no longer connect automatically.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        # Interactive commands (e.g. wifi connect) can be aborted mid-prompt.
        print("\nAborted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
