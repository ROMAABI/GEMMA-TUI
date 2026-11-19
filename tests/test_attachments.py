from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

import core.attachments as attachments


@pytest.fixture(autouse=True)
def _local_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attachments, "attachments_root", lambda: tmp_path / "attachments")


def _image(path: Path, size: tuple[int, int] = (2000, 1000)) -> Path:
    Image.new("RGB", size, (120, 40, 200)).save(path)
    return path


def test_prepare_image_resizes_and_builds_data_uri() -> None:
    source = _image(Path("/tmp/attach_test_big.png"))
    descriptor = attachments.prepare_attachment("s1", str(source))
    assert descriptor["kind"] == "image"
    assert Path(descriptor["stored_path"]).is_file()
    assert descriptor["preview"]
    rendered = attachments.for_model(descriptor)
    parts = rendered["content"]
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_prepare_text_file_quotes_content() -> None:
    source = Path("/tmp/attach_test.py")
    source.write_text("print('hello')\n", encoding="utf-8")
    descriptor = attachments.prepare_attachment("s1", str(source))
    rendered = attachments.for_model(descriptor)
    assert "Attached file: attach_test.py" in rendered["content"]
    assert "print('hello')" in rendered["content"]


def test_unsupported_and_oversize_files_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "archive.exe"
    bad.write_bytes(b"x")
    with pytest.raises(attachments.AttachmentError, match="Unsupported"):
        attachments.prepare_attachment("s1", str(bad))

    big = tmp_path / "big.png"
    big.write_bytes(b"x" * 100)
    monkeypatch.setattr(attachments, "MAX_FILE_BYTES", 10)
    with pytest.raises(attachments.AttachmentError, match="20MB"):
        attachments.prepare_attachment("s1", str(big))


def test_video_frames_sampled_and_noted(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2",
         "-pix_fmt", "yuv420p", str(video)],
        check=True, capture_output=True,
    )
    descriptor = attachments.prepare_attachment("s1", str(video))
    assert descriptor["kind"] == "video"
    assert 1 <= descriptor["frames"] <= attachments.MAX_VIDEO_FRAMES
    assert "frames sampled from a video" in descriptor["note"]
    parts = attachments.for_model(descriptor)["content"]
    assert any(part.get("type") == "text" for part in parts)
    assert any(part.get("type") == "image_url" for part in parts)


def test_old_attachments_become_text_markers() -> None:
    messages = [
        {"role": "user", "content": "first", "metadata": {"attachments": [{"name": "a.png", "kind": "image"}]}},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "second", "metadata": {"attachments": [{"name": "b.png", "kind": "image"}]}},
    ]
    recent, stale = attachments.recent_attachment_messages(messages, keep_turns=1)
    assert stale == ["[image: a.png]"]
    assert "attachments" not in recent[0]
    assert recent[-1]["attachments"][0]["name"] == "b.png"


def test_vision_detected_from_props(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"modalities": {"vision": True}}

    monkeypatch.setattr("httpx.get", lambda *a, **k: FakeResponse())
    assert attachments.vision_supported("http://127.0.0.1:8080") is True
