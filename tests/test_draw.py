from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from unittest.mock import patch

import pytest
from PIL import Image

from devbits.cli import main


def _png_bytes(size=(4, 3), color=(255, 0, 0, 128)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_draw_without_image_launches_blank_canvas() -> None:
    with patch("devbits.draw.launch_draw") as mock_launch:
        assert main(["draw"]) == 0
    mock_launch.assert_called_once_with(None, None, (1280, 720))


def test_draw_with_image_and_options(tmp_path) -> None:
    image = tmp_path / "photo.png"
    image.write_bytes(_png_bytes())
    with patch("devbits.draw.launch_draw") as mock_launch:
        assert main(["draw", str(image), "-o", str(tmp_path / "out.jpg"), "--size", "640x480"]) == 0
    called_image, called_output, called_size = mock_launch.call_args[0]
    assert called_image.name == "photo.png"
    assert called_output.name == "out.jpg"
    assert called_size == (640, 480)


def test_draw_missing_image_errors(tmp_path, capsys) -> None:
    assert main(["draw", str(tmp_path / "nope.png")]) == 1
    assert "Path does not exist" in capsys.readouterr().err


def test_launch_draw_rejects_non_image(tmp_path) -> None:
    from devbits.draw import launch_draw

    bogus = tmp_path / "notes.png"
    bogus.write_text("not an image")
    with pytest.raises(ValueError, match="Not a readable image"):
        launch_draw(bogus)


def test_safe_name() -> None:
    from devbits.draw import _safe_name

    assert _safe_name("pic.png") == "pic.png"
    assert _safe_name("  pic  ") == "pic.png"
    assert _safe_name("photo.JPG") == "photo.JPG"
    for bad in ("", "..", "../evil.png", "sub/pic.png", "a\\b.png", "C:pic.png", "pic.exe"):
        with pytest.raises(ValueError):
            _safe_name(bad)


def test_default_save_target(tmp_path, monkeypatch) -> None:
    from devbits.draw import _default_save_target

    monkeypatch.chdir(tmp_path)
    assert _default_save_target(None, None) == (tmp_path, "untitled.png")
    assert _default_save_target(tmp_path / "a" / "cat.jpg", None) == (tmp_path / "a", "cat_drawn.jpg")
    # Formats Save can't write fall back to PNG
    assert _default_save_target(tmp_path / "scan.psd", None)[1] == "scan_drawn.png"
    assert _default_save_target(tmp_path / "depth.pgm", None)[1] == "depth_drawn.pgm"
    assert _default_save_target(tmp_path / "cat.jpg", tmp_path / "out" / "x.webp") == (tmp_path / "out", "x.webp")


@pytest.mark.parametrize("ext, mode", [(".png", "RGBA"), (".jpg", "RGB"), (".bmp", "RGB"),
                                       (".webp", "RGBA"), (".tiff", "RGBA"), (".gif", "P"), (".ico", "RGBA"),
                                       (".pgm", "L"), (".ppm", "RGB")])
def test_encode_image_formats(tmp_path, ext, mode) -> None:
    from devbits.draw import _encode_image

    target = tmp_path / f"out{ext}"
    _encode_image(_png_bytes((20, 10)), target)
    with Image.open(target) as im:
        assert im.mode == mode
        if ext != ".ico":
            assert im.size == (20, 10)
    assert not list(tmp_path.glob(".*.tmp"))


def test_encode_image_flattens_alpha_onto_white(tmp_path) -> None:
    from devbits.draw import _encode_image

    target = tmp_path / "out.bmp"
    _encode_image(_png_bytes(color=(0, 0, 0, 0)), target)
    with Image.open(target) as im:
        assert im.getpixel((0, 0)) == (255, 255, 255)


def test_encode_pgm_writes_binary_graymap(tmp_path) -> None:
    from devbits.draw import _encode_image

    target = tmp_path / "out.pgm"
    _encode_image(_png_bytes((5, 4), color=(255, 0, 0, 255)), target)
    assert target.read_bytes().startswith(b"P5")
    with Image.open(target) as im:
        # red -> luminance 76 (ITU-R 601-2)
        assert im.getpixel((0, 0)) == 76


@pytest.mark.parametrize("maxval", [255, 4095, 65535])
def test_to_png_reads_pgm(tmp_path, maxval) -> None:
    import numpy as np

    from devbits.draw import _to_png

    dtype = ">u2" if maxval > 255 else "u1"
    pixels = np.array([[0, maxval // 2, maxval]], dtype=dtype)
    src = tmp_path / "gray.pgm"
    src.write_bytes(f"P5\n3 1\n{maxval}\n".encode() + pixels.tobytes())
    with Image.open(io.BytesIO(_to_png(src))) as im:
        values = [im.getpixel((x, 0))[0] for x in range(3)]
    # 16-bit data must be scaled, not clipped to white
    assert values[0] == 0 and 120 <= values[1] <= 135 and values[2] == 255


def test_to_png_applies_exif_orientation(tmp_path) -> None:
    from devbits.draw import _to_png

    src = tmp_path / "rot.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # rotated 90° clockwise
    Image.new("RGB", (40, 20), "blue").save(src, exif=exif)
    with Image.open(io.BytesIO(_to_png(src))) as im:
        assert im.format == "PNG"
        assert im.size == (20, 40)


@pytest.fixture
def draw_server(tmp_path):
    from devbits.draw import _DrawHandler

    image = tmp_path / "start.png"
    image.write_bytes(_png_bytes((6, 5)))

    class Handler(_DrawHandler):
        pass

    Handler.image_path = image
    Handler.save_dir = tmp_path
    Handler.save_name = "start_drawn.png"
    Handler.canvas_size = (100, 50)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", tmp_path
    finally:
        server.shutdown()
        server.server_close()


def _post(url, data, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_server_serves_page_and_initial_image(draw_server) -> None:
    base, _ = draw_server
    with urllib.request.urlopen(base + "/") as resp:
        html = resp.read().decode("utf-8")
    config = json.loads(html.split("window.__DRAW__ = ", 1)[1].split(";</script>", 1)[0])
    assert config["image"] == "/api/initial"
    assert config["saveName"] == "start_drawn.png"
    assert config["size"] == [100, 50]
    with urllib.request.urlopen(base + "/api/initial") as resp:
        assert resp.headers["Content-Type"] == "image/png"
        with Image.open(io.BytesIO(resp.read())) as im:
            assert im.size == (6, 5)


def test_server_save_writes_and_guards_overwrite(draw_server) -> None:
    base, folder = draw_server
    status, body = _post(base + "/api/save?name=pic.jpg", _png_bytes((8, 8)))
    assert status == 200
    assert json.loads(body)["name"] == "pic.jpg"
    with Image.open(folder / "pic.jpg") as im:
        assert im.format == "JPEG" and im.size == (8, 8)

    # A second save without overwrite=1 is refused, with it succeeds
    status, body = _post(base + "/api/save?name=pic.jpg", _png_bytes((3, 3)))
    assert status == 409 and json.loads(body)["error"] == "exists"
    status, _ = _post(base + "/api/save?name=pic.jpg&overwrite=1", _png_bytes((3, 3)))
    assert status == 200
    with Image.open(folder / "pic.jpg") as im:
        assert im.size == (3, 3)


def test_server_save_rejects_bad_requests(draw_server) -> None:
    base, folder = draw_server
    assert _post(base + "/api/save?name=../x.png", _png_bytes())[0] == 400
    assert _post(base + "/api/save?name=x.png", b"garbage")[0] == 500
    assert not (folder / "x.png").exists()
    status, _ = _post(base + "/api/save?name=y.png", _png_bytes(), {"Origin": "http://evil.example"})
    assert status == 403
    assert not (folder / "y.png").exists()


def test_server_decode(draw_server) -> None:
    base, _ = draw_server
    buf = io.BytesIO()
    Image.new("RGB", (7, 9), "green").save(buf, format="TIFF")
    status, body = _post(base + "/api/decode", buf.getvalue())
    assert status == 200
    with Image.open(io.BytesIO(body)) as im:
        assert im.format == "PNG" and im.size == (7, 9)
    assert _post(base + "/api/decode", b"nope")[0] == 415
