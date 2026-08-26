from __future__ import annotations
from unittest.mock import patch
import pytest
from devbits.cli import main

def test_editvideo_no_gui_requires_video(capsys) -> None:
    # Running editvideo without GUI should return exit code 1 if video is missing
    exit_code = main(["editvideo"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "video path is required when --gui is not specified" in captured.err

def test_editvideo_with_gui_no_video() -> None:
    # Running editvideo with --gui and no video should launch GUI (which we mock)
    with patch("devbits.gui.launch_gui") as mock_launch:
        main(["editvideo", "--gui"])
        mock_launch.assert_called_once_with(None)

def test_editvideo_with_gui_and_video(tmp_path) -> None:
    # Running editvideo with --gui and a video should launch GUI with that path
    video_file = tmp_path / "sample.mp4"
    video_file.touch()
    with patch("devbits.gui.launch_gui") as mock_launch:
        main(["editvideo", "--gui", str(video_file)])
        mock_launch.assert_called_once()
        called_arg = mock_launch.call_args[0][0]
        assert called_arg.name == "sample.mp4"

def test_clipvideo_alias_prints_rename_notice(capsys) -> None:
    # The old clipvideo name still works but prints a rename notice
    with patch("devbits.gui.launch_gui") as mock_launch:
        exit_code = main(["clipvideo", "--gui"])
        mock_launch.assert_called_once_with(None)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "'clipvideo' has been renamed to 'editvideo'" in captured.err

def test_gui_upload(tmp_path) -> None:
    from devbits.gui import _Handler
    import http.server
    import threading
    import urllib.request
    import json
    
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    
    class TestHandler(_Handler):
        pass
    TestHandler.upload_dir = str(upload_dir)
    TestHandler.export_dir = str(export_dir)
    TestHandler.video_path = None
        
    server = http.server.HTTPServer(("127.0.0.1", 0), TestHandler)
    port = server.server_address[1]
    
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    
    try:
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        filename = "test_sample.mp4"
        file_content = b"fake video bytes"
        
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: video/mp4\r\n\r\n"
        ).encode("utf-8") + file_content + f"\r\n--{boundary}--\r\n".encode("utf-8")
        
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/upload",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body))
            }
        )
        
        with urllib.request.urlopen(req) as response:
            assert response.status == 200
            resp_body = response.read().decode("utf-8")
            data = json.loads(resp_body)
            assert "src" in data
            assert data["src"] == f"/uploads/{filename}"
            
        saved_file = upload_dir / filename
        assert saved_file.exists()
        assert saved_file.read_bytes() == file_content
    finally:
        server.shutdown()
        server.server_close()

def test_norm_crop_valid_and_clamped() -> None:
    from devbits.gui import _norm_crop
    c = _norm_crop({"x": 0.25, "y": 0.1, "w": 0.5, "h": 0.5})
    assert c == {"x": 0.25, "y": 0.1, "w": 0.5, "h": 0.5}
    # Out-of-range values are clamped so x+w and y+h stay within the frame
    c = _norm_crop({"x": 0.8, "y": 0.8, "w": 0.9, "h": 0.9})
    assert c["x"] + c["w"] <= 1.0 and c["y"] + c["h"] <= 1.0

def test_norm_crop_rejects_noop_and_malformed() -> None:
    from devbits.gui import _norm_crop
    assert _norm_crop(None) is None
    assert _norm_crop("junk") is None
    assert _norm_crop({"x": 0}) is None
    assert _norm_crop({"x": "a", "y": 0, "w": 1, "h": 1}) is None
    # Full-frame crop is a no-op
    assert _norm_crop({"x": 0, "y": 0, "w": 1, "h": 1}) is None

def test_crop_frame_slices_region() -> None:
    import numpy as np
    from devbits.gui import _crop_frame
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    out = _crop_frame(frame, {"x": 0.25, "y": 0.1, "w": 0.5, "h": 0.5})
    assert out.shape[:2] == (50, 100)
    # Tiny crops still yield at least 2px in each dimension
    out = _crop_frame(frame, {"x": 0.99, "y": 0.99, "w": 0.001, "h": 0.001})
    assert out.shape[0] >= 2 and out.shape[1] >= 2
