from __future__ import annotations

import sys
from collections.abc import Sequence

from .cli import main as devbits_main


def _run(command: str, argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return devbits_main([command, *args])


def clearcache() -> int:
    return _run("clearcache")


def images2video() -> int:
    return _run("images2video")


def video2images() -> int:
    return _run("video2images")


def images2gif() -> int:
    return _run("images2gif")


def video2gif() -> int:
    return _run("video2gif")


def editvideo() -> int:
    return _run("editvideo")


def clipvideo() -> int:
    # Deprecated alias; main() prints the rename notice.
    return _run("clipvideo")


def resizevideo() -> int:
    return _run("resizevideo")


def image2ico() -> int:
    return _run("image2ico")


def resizeimage() -> int:
    return _run("resizeimage")


def recolor() -> int:
    return _run("recolor")


def batchimages() -> int:
    return _run("batchimages")


def checkimages() -> int:
    return _run("checkimages")


def contactsheet() -> int:
    return _run("contactsheet")


def tree() -> int:
    return _run("tree")


def size() -> int:
    return _run("size")


def renamefiles() -> int:
    return _run("renamefiles")


def samplefiles() -> int:
    return _run("samplefiles")


def netscan() -> int:
    return _run("netscan")


def wifi() -> int:
    return _run("wifi")


def netsurvey() -> int:
    return _run("netsurvey")


def ime() -> int:
    return _run("ime")
