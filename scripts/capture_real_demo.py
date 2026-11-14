import asyncio
import os
import sys
import shutil
import random
import wave
import struct
import subprocess
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

import tui.app as tui_app
from config.settings import Settings
from tui.app import GemmaTUI, ChatMessage, ToolStepCard, LandingInput
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# Mock server health to always show Connected in green
async def _mock_healthy(*args, **kwargs):
    return True

async def _mock_model(*args, **kwargs):
    return "gemma-4-E4B-it-Q4_K_M"

tui_app.async_server_is_healthy = _mock_healthy
tui_app.async_get_loaded_model = _mock_model

OUTPUT_DIR = PROJECT_ROOT / "brag-output" / "real_captures"
FRAMES_DIR = OUTPUT_DIR / "frames"
SFX_DIR = Path("/home/spix/.agents/skills/brag/assets/sfx")
KEYBOARD_SFX = sorted(list((SFX_DIR / "keyboard").glob("keypress-*.wav")))
CLICK_SFX = SFX_DIR / "interface" / "click_001.ogg"
SELECT_SFX = SFX_DIR / "interface" / "select_008.ogg"

async def capture_demo():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    # Clean old frames
    for f in FRAMES_DIR.glob("*.png"):
        f.unlink()

    frame_idx = 0
    audio_events = []  # list of (timestamp_seconds, wav_file_path)

    def record_audio(sound_file):
        nonlocal frame_idx
        t = frame_idx / 25.0
        audio_events.append((t, sound_file))

    # ── PART 1: Real Terminal Shell Launch ──
    print("Capturing Terminal Shell Launch...")
    cli_command = "gemma-local"
    typed_cli = ""

    def render_terminal(cmd_text, cursor=True, extra_lines=None):
        nonlocal frame_idx
        console = Console(width=140, height=42, record=True)
        content = Text()
        content.append("\n")
        content.append("spix@workstation", style="bold #50fa7b")
        content.append(":", style="bold white")
        content.append("~/GEMMA-TUI", style="bold #8be9fd")
        content.append("$ ", style="bold white")
        content.append(cmd_text, style="bold #f8f8f2")
        if cursor:
            content.append(" █", style="bold #50fa7b")
        content.append("\n")

        if extra_lines:
            content.append("\n")
            for line, style in extra_lines:
                content.append(f"{line}\n", style=style)

        panel = Panel(
            content,
            title="[bold #bd93f9] Terminal — bash (140x42) [/bold #bd93f9]",
            border_style="#6272a4",
            height=42,
        )
        console.print(panel)
        svg = console.export_svg()
        svg_path = FRAMES_DIR / f"temp_{frame_idx:04d}.svg"
        png_path = FRAMES_DIR / f"frame_{frame_idx:04d}.png"
        svg_path.write_text(svg, encoding="utf-8")
        subprocess.run(
            ["rsvg-convert", "-w", "1920", str(svg_path), "-o", str(png_path)],
            check=True,
            capture_output=True,
        )
        svg_path.unlink()
        frame_idx += 1

    # Initial terminal idle (0.6s)
    for _ in range(15):
        render_terminal("", cursor=True)

    # Type "gemma-local"
    for char in cli_command:
        typed_cli += char
        sfx = random.choice(KEYBOARD_SFX)
        record_audio(sfx)
        for _ in range(2):
            render_terminal(typed_cli, cursor=True)

    # Pause on complete command before hitting Enter
    for _ in range(10):
        render_terminal(typed_cli, cursor=True)

    # Hit Enter: show start messages
    record_audio(CLICK_SFX)
    boot_lines = [
        ("Starting GEMMA-TUI v0.1.0 (Autonomous Agent Mode enabled)...", "bold #bd93f9"),
        ("Connecting to llama.cpp backend at http://localhost:8080...", "dim white"),
        ("● Connected to model: gemma-4-E4B-it-Q4_K_M (Active)", "bold #50fa7b"),
        ("Loading Dracula theme & terminal interface...", "dim white"),
    ]
    for _ in range(20):
        render_terminal(typed_cli, cursor=False, extra_lines=boot_lines)

    shutil.copy(FRAMES_DIR / f"frame_{frame_idx-1:04d}.png", OUTPUT_DIR / "snapshot_00_cli_launch.png")

    # ── PART 2: Textual TUI Capture ──
    print("Launching GEMMA-TUI inside Textual Pilot...")
    settings = Settings.load()
    settings.theme = "dracula"
    app = GemmaTUI(settings=settings)

    def save_app_frame(app):
        nonlocal frame_idx
        svg_content = app.export_screenshot()
        svg_path = FRAMES_DIR / f"temp_{frame_idx:04d}.svg"
        png_path = FRAMES_DIR / f"frame_{frame_idx:04d}.png"
        svg_path.write_text(svg_content, encoding="utf-8")
        subprocess.run(
            ["rsvg-convert", "-w", "1920", str(svg_path), "-o", str(png_path)],
            check=True,
            capture_output=True,
        )
        svg_path.unlink()
        frame_idx += 1

    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)

        # Set Connected status in green
        app.model_loaded = True
        try:
            status_widget = app.query_one("#model-status", Static)
            status_widget.update("●  CONNECTED")
            status_widget.styles.color = "#50FA7B"
        except Exception:
            pass

        # ── 1. Landing Screen ──
        print("Capturing Welcome Screen...")
        prompt_input = app.query_one("#prompt", LandingInput)
        prompt_input.focus()
        for _ in range(20):
            save_app_frame(app)
        shutil.copy(FRAMES_DIR / f"frame_{frame_idx-1:04d}.png", OUTPUT_DIR / "snapshot_01_welcome.png")

        # ── 2. Natural User Typing with Moving Cursor ──
        print("Simulating Natural User Typing with Cursor...")
        prompt_text = "Refactor auth.py with JWT tokens and run pytest"

        typed = ""
        for char in prompt_text:
            typed += char
            prompt_input.value = typed
            prompt_input.cursor_position = len(typed)
            sfx = random.choice(KEYBOARD_SFX)
            record_audio(sfx)
            for _ in range(2):
                save_app_frame(app)

        # Hold prompt before submit
        for _ in range(12):
            save_app_frame(app)

        # ── 3. Submit Message & Mount Agent Tools ──
        print("Submitting Message & Mounting Agent Tools...")
        record_audio(CLICK_SFX)
        prompt_input.value = ""
        app.show_chat()
        now = "03:55 AM"
        app.write_message("user", prompt_text, timestamp=now)
        app._flush_pending_mounts()
        await pilot.pause(0.2)

        for _ in range(10):
            save_app_frame(app)

        container = app.query_one("#chat-messages", Vertical)

        # Mount Tool 1: read_file (status="done" for checkmark ✓)
        record_audio(SELECT_SFX)
        tool1 = ToolStepCard(
            tool_name="read_file",
            args={"path": "src/auth.py", "start_line": 1, "end_line": 40},
            step=1,
            status="done",
            result="Success: Read 40 lines from 'src/auth.py'",
        )
        container.mount(tool1)
        await pilot.pause(0.2)
        for _ in range(14):
            save_app_frame(app)

        # Mount Tool 2: edit_file (status="done" for checkmark ✓)
        record_audio(SELECT_SFX)
        tool2 = ToolStepCard(
            tool_name="edit_file",
            args={"path": "src/auth.py", "old_str": "session_id = create_session()", "new_str": "token = jwt.encode(payload, SECRET_KEY)"},
            step=2,
            status="done",
            result="Success: Replaced session auth with PyJWT token verification (+18 lines)",
        )
        container.mount(tool2)
        await pilot.pause(0.2)
        for _ in range(14):
            save_app_frame(app)

        # Mount Tool 3: run_command (status="done" for checkmark ✓)
        record_audio(SELECT_SFX)
        tool3 = ToolStepCard(
            tool_name="run_command",
            args={"command": "pytest tests/test_auth.py -v"},
            step=3,
            status="done",
            result="==================== 12 passed in 0.42s ====================",
        )
        container.mount(tool3)
        await pilot.pause(0.2)
        for _ in range(18):
            save_app_frame(app)

        shutil.copy(FRAMES_DIR / f"frame_{frame_idx-1:04d}.png", OUTPUT_DIR / "snapshot_02_agent_tools.png")

        # ── 4. Assistant Response Streaming ──
        print("Streaming Assistant Response...")
        reply_code = """I refactored `src/auth.py` with JWT token verification and ran tests:

```python
import jwt
from datetime import datetime, timedelta

def create_token(payload: dict) -> str:
    return jwt.encode(payload, "secret-key", algorithm="HS256")

def verify_token(token: str) -> dict:
    return jwt.decode(token, "secret-key", algorithms=["HS256"])
```

✓ All 12 unit tests passed in 0.42s."""

        app.write_message("assistant", reply_code, elapsed=0.8, timestamp=now)
        app._flush_pending_mounts()
        
        # Update status bar with active metrics
        app.total_tokens = 248
        app.gen_time = 4.6
        app.total_gen_count = 1
        app.context_used = 1420
        app.update_status_bar()

        await pilot.pause(0.3)
        try:
            scroll = app.query_one("#chat-scroll", VerticalScroll)
            scroll.scroll_end(animate=False)
        except Exception:
            pass
        await pilot.pause(0.1)

        for _ in range(45):  # 1.8s hold
            save_app_frame(app)

        shutil.copy(FRAMES_DIR / f"frame_{frame_idx-1:04d}.png", OUTPUT_DIR / "snapshot_03_code_response.png")

        # ── 5. Theme Switching Live ──
        print("Switching Theme to Tokyo Night...")
        app.switch_theme("tokyo-night")
        app.update_status_bar()
        try:
            status_widget = app.query_one("#model-status", Static)
            status_widget.update("●  CONNECTED")
            status_widget.styles.color = "#50FA7B"
        except Exception:
            pass
        await pilot.pause(0.2)
        for _ in range(25):
            save_app_frame(app)
        shutil.copy(FRAMES_DIR / f"frame_{frame_idx-1:04d}.png", OUTPUT_DIR / "snapshot_04_tokyo_night.png")

        print("Switching Theme to Cyberpunk...")
        app.switch_theme("cyberpunk")
        app.update_status_bar()
        try:
            status_widget = app.query_one("#model-status", Static)
            status_widget.update("●  CONNECTED")
            status_widget.styles.color = "#50FA7B"
        except Exception:
            pass
        await pilot.pause(0.2)
        for _ in range(25):
            save_app_frame(app)
        shutil.copy(FRAMES_DIR / f"frame_{frame_idx-1:04d}.png", OUTPUT_DIR / "snapshot_05_cyberpunk.png")

        print("Switching Theme to Nord...")
        app.switch_theme("nord")
        app.update_status_bar()
        try:
            status_widget = app.query_one("#model-status", Static)
            status_widget.update("●  CONNECTED")
            status_widget.styles.color = "#50FA7B"
        except Exception:
            pass
        await pilot.pause(0.2)
        for _ in range(25):
            save_app_frame(app)
        shutil.copy(FRAMES_DIR / f"frame_{frame_idx-1:04d}.png", OUTPUT_DIR / "snapshot_06_nord.png")

        print(f"Total frames captured: {frame_idx}")

    # ── 6. Build Clean Synchronized Audio Track ──
    print("Generating clean audio track with mechanical keystrokes...")
    sample_rate = 44100
    total_samples = int((frame_idx / 25.0 + 1.0) * sample_rate)
    audio_data = bytearray(total_samples * 2)

    wav_cache = {}
    for t_sec, sfx_path in audio_events:
        path_str = str(sfx_path)
        if path_str.endswith(".ogg"):
            temp_wav = FRAMES_DIR / f"temp_{Path(path_str).stem}.wav"
            if not temp_wav.exists():
                subprocess.run(["ffmpeg", "-y", "-i", path_str, "-ar", "44100", "-ac", "1", str(temp_wav)], check=True, capture_output=True)
            path_str = str(temp_wav)

        if path_str not in wav_cache:
            with wave.open(path_str, "rb") as wf:
                wav_cache[path_str] = wf.readframes(wf.getnframes())

        raw_frames = wav_cache[path_str]
        start_sample = int(t_sec * sample_rate)
        num_samples = len(raw_frames) // 2

        for i in range(num_samples):
            idx = start_sample + i
            if idx >= total_samples:
                break
            existing = struct.unpack_from("<h", audio_data, idx * 2)[0]
            sound = struct.unpack_from("<h", raw_frames, i * 2)[0]
            mixed = max(-32768, min(32767, existing + sound))
            struct.pack_into("<h", audio_data, idx * 2, mixed)

    out_wav = OUTPUT_DIR / "clean_audio.wav"
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data)

    print(f"Audio track created: {out_wav}")

    # ── 7. Compile Video with FFmpeg ──
    print("Compiling final realistic demo video with FFmpeg...")
    demo_video_path = OUTPUT_DIR / "real_demo.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-framerate", "25",
        "-i", str(FRAMES_DIR / "frame_%04d.png"),
        "-i", str(out_wav),
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(demo_video_path)
    ]
    subprocess.run(cmd, check=True)
    print(f"Successfully generated real demo video: {demo_video_path}")

    # Create root level convenience copies
    shutil.copy(demo_video_path, PROJECT_ROOT / "brag-output" / "original_demo.mp4")
    shutil.copy(OUTPUT_DIR / "snapshot_03_code_response.png", PROJECT_ROOT / "brag-output" / "original_snapshot.png")
    print("Exported original_demo.mp4 and original_snapshot.png to brag-output/")

if __name__ == "__main__":
    asyncio.run(capture_demo())
