"""Attachment preprocessing for local multimodal chat.

Files are copied under ``~/.local/share/gemma/attachments/<session_id>/``.
Images are resized, videos are sampled into frames with ffmpeg, and text/PDF
becomes a capped context block. Only small descriptors are stored in the DB;
image data URIs are regenerated from disk when building a model request.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

MAX_ATTACHMENTS = 8
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_SIDE = 1024
MAX_VIDEO_FRAME_SIDE = 768
MAX_VIDEO_FRAMES = 8
MAX_TEXT_TOKENS = 6_000
MAX_TEXT_CHARS = MAX_TEXT_TOKENS * 4

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
PDF_EXT = {".pdf"}
TEXT_EXT = {
    ".txt", ".md", ".markdown", ".py", ".js", ".jsx", ".ts", ".tsx", ".json",
    ".jsonl", ".toml", ".yaml", ".yml", ".css", ".html", ".htm", ".xml", ".sql",
    ".c", ".h", ".cpp", ".hpp", ".rs", ".go", ".java", ".rb", ".sh", ".zsh",
    ".log", ".csv", ".ini", ".cfg", ".gradle", ".kt", ".swift", ".lua",
}


class AttachmentError(ValueError):
    """User-facing attachment failure."""


def suggest_paths(query: str, limit: int = 20) -> list[str]:
    """Shell-free path completion for /attach."""
    raw = query.strip() or "~/"
    expanded = Path(raw).expanduser()
    if raw.endswith("/") or expanded.is_dir():
        directory = expanded if expanded.is_dir() else expanded.parent
        prefix = ""
    else:
        directory = expanded.parent
        prefix = expanded.name
    if not directory.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(directory.iterdir()):
        name = entry.name
        if prefix and not name.lower().startswith(prefix.lower()):
            continue
        candidate = str(entry)
        if entry.is_dir():
            candidate += "/"
        out.append(candidate)
        if len(out) >= limit:
            break
    return out


def vision_supported(base_url: str) -> bool:
    """True when llama.cpp was started with --mmproj (vision modality enabled)."""
    try:
        import httpx

        response = httpx.get(f"{base_url.rstrip('/')}/props", timeout=2.0)
        response.raise_for_status()
        data = response.json()
        return bool((data.get("modalities") or {}).get("vision"))
    except Exception:  # noqa: BLE001
        return False


def capabilities(base_url: str) -> dict[str, Any]:
    return {
        "vision": vision_supported(base_url),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "chafa": shutil.which("chafa") is not None,
        "pdftotext": shutil.which("pdftotext") is not None,
        "max_attachments": MAX_ATTACHMENTS,
        "max_file_bytes": MAX_FILE_BYTES,
    }


def attachments_root() -> Path:
    return Path.home() / ".local" / "share" / "gemma" / "attachments"


def session_dir(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:64]
    target = attachments_root() / (safe or "new")
    target.mkdir(parents=True, exist_ok=True)
    return target


def kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXT:
        return "image"
    if suffix in VIDEO_EXT:
        return "video"
    if suffix in PDF_EXT:
        return "pdf"
    if suffix in TEXT_EXT:
        return "text"
    return "unsupported"


def _preview_path(stored: Path, kind: str) -> str | None:
    return str(stored) if kind in ("image", "video") else None


def _copy_source(path: Path, session_id: str) -> Path:
    target_dir = session_dir(session_id)
    name = f"{uuid.uuid4().hex[:8]}_{path.name[:120]}"
    target = target_dir / name
    shutil.copyfile(path, target)
    return target


def _image_to_data_uri(path: Path) -> tuple[str, str]:
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        out = target = path.with_name(path.stem + "_small" + (".png" if has_alpha else ".jpg"))
        if has_alpha:
            image.save(out, "PNG")
            mime = "image/png"
        else:
            image.convert("RGB").save(out, "JPEG", quality=86)
            mime = "image/jpeg"
    data = base64.b64encode(out.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}", str(out)


def _ffprobe(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        return json.loads(result.stdout or "{}")
    except Exception:  # noqa: BLE001
        return {}


def _video_frames(path: Path, session_id: str, duration: float) -> list[str]:
    target_dir = session_dir(session_id)
    if duration <= MAX_VIDEO_FRAMES:
        times = [float(i) for i in range(max(1, int(duration) or 1))]
    else:
        times = [
            round(duration * (i + 1) / (MAX_VIDEO_FRAMES + 1), 2)
            for i in range(MAX_VIDEO_FRAMES)
        ]
    uris: list[str] = []
    for index, at in enumerate(times):
        out = target_dir / f"{path.stem}_frame{index:02d}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", str(at), "-i", str(path),
                 "-frames:v", "1", "-vf", f"scale='min({MAX_VIDEO_FRAME_SIDE},iw)':-2",
                 "-q:v", "4", str(out)],
                capture_output=True, timeout=40, check=True,
            )
        except FileNotFoundError as exc:
            raise AttachmentError("ffmpeg is not installed. Install it with: sudo pacman -S ffmpeg") from exc
        except Exception as exc:  # noqa: BLE001
            raise AttachmentError(f"Could not sample video frames: {exc}") from exc
        if out.exists():
            uri, _ = _image_to_data_uri(out)
            uris.append(uri)
    return uris


def _extract_text(path: Path, kind: str) -> str:
    if kind == "pdf":
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True, text=True, timeout=30, check=True,
            )
            text = result.stdout
        except FileNotFoundError as exc:
            raise AttachmentError("pdftotext is not installed. Install it with: sudo pacman -S poppler") from exc
        except Exception as exc:  # noqa: BLE001
            raise AttachmentError(f"Could not read PDF: {exc}") from exc
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n... [text truncated]"
    return text


def prepare_attachment(session_id: str, raw_path: str) -> dict[str, Any]:
    """Validate, store and preprocess one file; returns a DB-safe descriptor."""
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise AttachmentError(f"File not found: {raw_path}")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise AttachmentError(f"{path.name} is {size / 1024 / 1024:.1f}MB — the limit is 20MB")
    kind = kind_of(path)
    if kind == "unsupported":
        raise AttachmentError(
            f"Unsupported file type: {path.suffix or '(none)'}. "
            "Use images, video, PDF or text/code files."
        )

    stored = _copy_source(path, session_id)
    descriptor: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "name": path.name,
        "kind": kind,
        "size": size,
        "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "stored_path": str(stored),
        "preview": None,
        "text": "",
        "duration": 0.0,
        "frames": 0,
        "note": "",
        "audio_skipped": False,
        "tokens_est": max(1, size // 4),
    }

    if kind == "image":
        uri, preview = _image_to_data_uri(stored)
        descriptor.update(
            image_data=uri, preview=preview,
            tokens_est=max(256, len(uri) // 1400),
        )
    elif kind == "video":
        probe = _ffprobe(stored)
        duration = float(probe.get("format", {}).get("duration") or 0)
        streams = probe.get("streams") or []
        audio = any(s.get("codec_type") == "audio" for s in streams)
        frames = _video_frames(stored, session_id, duration)
        if not frames:
            raise AttachmentError("No frames could be sampled from that video")
        descriptor.update(
            image_data_list=frames, preview=frames[0], duration=round(duration, 2),
            frames=len(frames), audio_skipped=audio,
            note=(
                f"These are {len(frames)} frames sampled from a video of duration "
                f"{duration:.1f} seconds."
            ),
            tokens_est=max(256, sum(len(uri) for uri in frames) // 1400),
        )
    else:
        text = _extract_text(stored, kind)
        descriptor.update(text=text, tokens_est=max(1, len(text) // 4))

    # Data URIs are regenerated from stored_path when building model messages.
    descriptor.pop("image_data", None)
    descriptor.pop("image_data_list", None)
    return descriptor


def relocate_attachment(descriptor: dict[str, Any], session_id: str) -> dict[str, Any]:
    """Move prepared files into the target session's attachment directory."""
    stored = Path(str(descriptor.get("stored_path") or ""))
    if not stored.is_file() or stored.parent == session_dir(session_id):
        return descriptor
    target_dir = session_dir(session_id)
    moved = target_dir / stored.name
    shutil.move(str(stored), str(moved))
    descriptor["stored_path"] = str(moved)
    if descriptor.get("preview") and descriptor["preview"].startswith(str(stored.parent)):
        preview = Path(str(descriptor["preview"]))
        descriptor["preview"] = str(target_dir / preview.name)
    for frame in stored.parent.glob(f"{stored.stem}_frame*.jpg"):
        shutil.move(str(frame), str(target_dir / frame.name))
    return descriptor


def for_model(attachment: dict[str, Any], *, include_images: bool = True) -> dict[str, Any]:
    """Build one OpenAI-style message content entry for a stored attachment."""
    kind = attachment.get("kind")
    name = attachment.get("name", "attachment")
    if kind in ("image", "video") and include_images:
        uris: list[str] = []
        if kind == "image":
            path = Path(str(attachment.get("stored_path")))
            if path.exists():
                uri, _ = _image_to_data_uri(path)
                uris.append(uri)
        else:
            stored = Path(str(attachment.get("stored_path")))
            frames = sorted(stored.parent.glob(f"{stored.stem}_frame*.jpg"))
            uris = [
                "data:image/jpeg;base64," + base64.b64encode(frame.read_bytes()).decode("ascii")
                for frame in frames
            ]
        if uris:
            parts: list[dict[str, Any]] = []
            if attachment.get("note"):
                parts.append({"type": "text", "text": attachment["note"]})
            parts.extend(
                {"type": "image_url", "image_url": {"url": uri}} for uri in uris
            )
            return {"role": "user", "content": parts}
        return {"role": "user", "content": f"[image: {name} — file no longer available]"}
    if kind in ("image", "video"):
        return {"role": "user", "content": f"[image: {name}]"}
    if attachment.get("text"):
        return {
            "role": "user",
            "content": f"Attached file: {name}\n<file name=\"{name}\">\n{attachment['text']}\n</file>",
        }
    return {"role": "user", "content": f"[attachment: {name}]"}


def multimodal_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace attachment metadata with OpenAI-style content arrays."""
    out: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        attachments = item.pop("attachments", None)
        if attachments:
            parts: list[dict[str, Any]] = []
            if item.get("content"):
                parts.append({"type": "text", "text": str(item["content"])})
            for attachment in attachments:
                rendered = for_model(attachment)
                content = rendered.get("content")
                if isinstance(content, list):
                    parts.extend(content)
                else:
                    parts.append({"type": "text", "text": str(content)})
            item["content"] = parts
        out.append(item)
    return out


def recent_attachment_messages(
    messages: list[dict[str, Any]], *, keep_turns: int = 2
) -> tuple[list[dict[str, Any]], list[str]]:
    """Attach images from the last N user turns; older ones become text markers."""
    users = [i for i, item in enumerate(messages) if item.get("role") == "user"]
    recent = set(users[-keep_turns:]) if keep_turns > 0 else set()
    out: list[dict[str, Any]] = []
    stale: list[str] = []
    for index, message in enumerate(messages):
        item = dict(message)
        attachments = (message.get("metadata") or {}).get("attachments") or []
        if attachments:
            if index not in recent:
                stale.extend(
                    f"[image: {a.get('name')}]" if a.get("kind") in ("image", "video")
                    else f"[file: {a.get('name')}]"
                    for a in attachments
                )
                item.pop("attachments", None)
            else:
                item["attachments"] = attachments
        out.append(item)
    return out, stale
