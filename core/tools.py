from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

# Ignored directories for directory listings and grep
IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".gemini",
    "searxng",
}

_TOOL_CALL_RE = re.compile(r"<tool_call(?:\s+name=[\"'](?P<attr_name>[^\"']+)[\"'])?>(?P<body>.*?)</tool_call>", re.DOTALL | re.IGNORECASE)


class WorkspaceTools:
    """Provides safe, sandboxed file and command execution tools within a project workspace."""

    def __init__(self, workspace_dir: str | Path = ".", root_dir: str | Path | None = None) -> None:
        target = root_dir if root_dir is not None else workspace_dir
        self.root = Path(target).resolve()

    def _resolve_path(self, rel_path: str) -> Path:
        """Resolve a path and ensure it does not escape the workspace root."""
        clean = (rel_path or "").strip()
        if not clean:
            return self.root
        target = (self.root / clean).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError(f"Access denied: '{rel_path}' escapes workspace directory '{self.root}'")
        return target

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        """Read lines from a file with line numbers (1-indexed)."""
        try:
            target = self._resolve_path(path)
            if not target.exists():
                return f"Error: File '{path}' does not exist."
            if not target.is_file():
                return f"Error: '{path}' is a directory, not a file."

            try:
                content = target.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                return f"Error reading file '{path}': {exc}"

            lines = content.splitlines()
            total_lines = len(lines)

            s = 1 if start_line is None else max(1, start_line)
            e = total_lines if end_line is None else min(total_lines, end_line)

            if s > total_lines:
                return f"File '{path}' has {total_lines} lines. Requested start_line {s} exceeds line count."

            selected = lines[s - 1 : e]
            numbered = [f"{s + idx}: {line}" for idx, line in enumerate(selected)]
            header = f"[File: {path} | Lines {s}-{e} of {total_lines}]\n"
            return header + "\n".join(numbered)
        except Exception as exc:
            return f"Error in read_file: {exc}"

    def write_file(self, path: str, content: str) -> str:
        """Write content to a file, creating any required parent directories."""
        try:
            target = self._resolve_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            line_count = len(content.splitlines())
            return f"Success: Wrote {len(content)} bytes ({line_count} lines) to '{path}'."
        except Exception as exc:
            return f"Error in write_file: {exc}"

    def edit_file(self, path: str, old_str: str, new_str: str) -> str:
        """Replace exact instance of old_str with new_str in a file."""
        try:
            target = self._resolve_path(path)
            if not target.exists():
                return f"Error: File '{path}' does not exist."
            if not target.is_file():
                return f"Error: '{path}' is a directory, not a file."

            content = target.read_text(encoding="utf-8", errors="replace")
            if not old_str:
                return "Error: old_str cannot be empty."

            count = content.count(old_str)
            if count == 0:
                return f"Error: old_str not found in '{path}'. Please check exact indentation and text."
            if count > 1:
                return f"Error: old_str occurs {count} times in '{path}'. Provide more unique surrounding lines."

            new_content = content.replace(old_str, new_str, 1)
            target.write_text(new_content, encoding="utf-8")
            return f"Success: Replaced target block in '{path}'."
        except Exception as exc:
            return f"Error in edit_file: {exc}"

    def list_dir(self, path: str = ".", max_depth: int = 2) -> str:
        """List files and subdirectories up to max_depth."""
        try:
            target = self._resolve_path(path)
            if not target.exists():
                return f"Error: Directory '{path}' does not exist."
            if not target.is_dir():
                return f"Error: '{path}' is a file, not a directory."

            items_out: list[str] = []
            base_parts_len = len(target.parts)

            for root, dirs, files in os.walk(target):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
                cur_depth = len(Path(root).parts) - base_parts_len
                if cur_depth >= max_depth:
                    dirs.clear()

                rel_root = os.path.relpath(root, self.root)
                prefix = "" if rel_root == "." else rel_root + "/"

                for d in sorted(dirs):
                    items_out.append(f"📁 {prefix}{d}/")
                for f in sorted(files):
                    if not f.startswith("."):
                        items_out.append(f"📄 {prefix}{f}")

                if len(items_out) > 100:
                    items_out.append("... (truncated list)")
                    break

            if not items_out:
                return f"Directory '{path}' is empty."
            return "\n".join(items_out[:100])
        except Exception as exc:
            return f"Error in list_dir: {exc}"

    def grep_search(self, query: str, path: str = ".", max_matches: int = 30) -> str:
        """Search for a regex or text query across project files."""
        try:
            target = self._resolve_path(path)
            # Try ripgrep if available for speed
            try:
                cmd = [
                    "rg",
                    "-n",
                    "--max-count",
                    str(max_matches),
                    "--glob",
                    "!**/node_modules/**",
                    "--glob",
                    "!**/.venv/**",
                    "--glob",
                    "!**/.git/**",
                    query,
                    str(target),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if proc.stdout:
                    lines = proc.stdout.strip().splitlines()
                    rel_lines = []
                    for line in lines[:max_matches]:
                        rel_lines.append(line.replace(str(self.root) + "/", ""))
                    return "\n".join(rel_lines)
                if proc.returncode == 1:
                    return f"No matches found for query '{query}'."
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            # Python fallback search
            pattern = re.compile(query, re.IGNORECASE)
            matches: list[str] = []
            for root, dirs, files in os.walk(target):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
                for f in files:
                    if f.startswith("."):
                        continue
                    file_path = Path(root) / f
                    try:
                        text = file_path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    for idx, line in enumerate(text.splitlines(), start=1):
                        if pattern.search(line):
                            rel = file_path.relative_to(self.root)
                            matches.append(f"{rel}:{idx}: {line.strip()}")
                            if len(matches) >= max_matches:
                                return "\n".join(matches)
            if not matches:
                return f"No matches found for '{query}'."
            return "\n".join(matches)
        except Exception as exc:
            return f"Error in grep_search: {exc}"

    async def run_command(self, command: str, timeout: int = 30) -> str:
        """Run a shell command asynchronously in the workspace directory."""
        clean_cmd = (command or "").strip()
        if not clean_cmd:
            return "Error: Command cannot be empty."

        # Dangerous command protection
        dangerous = ["rm -rf /", ":(){ :|:& };:", "mkfs", "dd if=/dev/zero", "> /dev/sda"]
        if any(d in clean_cmd for d in dangerous):
            return f"Error: Refused to execute dangerous command '{clean_cmd}'."

        try:
            proc = await asyncio.create_subprocess_shell(
                clean_cmd,
                cwd=str(self.root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_data, stderr_data = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return f"Error: Command timed out after {timeout} seconds."

            out = stdout_data.decode("utf-8", errors="replace").strip()
            err = stderr_data.decode("utf-8", errors="replace").strip()

            parts: list[str] = [f"Exit Code: {proc.returncode}"]
            if out:
                parts.append(f"STDOUT:\n{out[:4000]}")
            if err:
                parts.append(f"STDERR:\n{err[:2000]}")
            if not out and not err:
                parts.append("(Command produced no output)")
            return "\n\n".join(parts)
        except Exception as exc:
            return f"Error running command: {exc}"


def parse_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Extract tool calls from model output in either XML or JSON format."""
    calls: list[tuple[str, dict[str, Any]]] = []

    # Match <tool_call>...</tool_call>
    for m in _TOOL_CALL_RE.finditer(text):
        attr_name = m.group("attr_name")
        body = m.group("body").strip()
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                name = attr_name or parsed.get("name") or parsed.get("tool") or ""
                args = parsed.get("arguments") or parsed.get("args") or parsed.get("parameters") or parsed
                # If parsed is {"path": "foo"}, and name is from attr_name
                if isinstance(args, dict) and "name" in args and "arguments" in args:
                    name = args["name"]
                    args = args["arguments"]
                elif name and args == parsed:
                    # Remove "name" / "tool" from args if present
                    args = {k: v for k, v in args.items() if k not in ("name", "tool")}
                if name:
                    calls.append((str(name).strip(), args if isinstance(args, dict) else {}))
        except Exception:
            # Fallback for simple single key-value: {"path": "..."} with attr_name
            if attr_name:
                calls.append((attr_name.strip(), {}))

    # Fallback 1: check ```json code block containing {"name": "...", "arguments": {...}}
    if not calls:
        code_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        for block in code_blocks:
            try:
                data = json.loads(block)
                if isinstance(data, dict):
                    name = data.get("name") or data.get("tool")
                    args = data.get("arguments") or data.get("args") or {}
                    if name and isinstance(args, dict):
                        calls.append((str(name).strip(), args))
            except Exception:
                continue

    # Fallback 2: detect raw markdown code blocks with target filenames or commands
    # If the model outputs code directly without <tool_call>, capture it and write directly to disk!
    if not calls:
        pattern = re.compile(r"```([a-zA-Z0-9_\-+]*)\s*\n(.*?)```", re.DOTALL)
        for m in pattern.finditer(text):
            lang = m.group(1).lower().strip()
            code = m.group(2)
            start_idx = m.start()

            # Skip empty blocks
            if not code.strip():
                continue

            # Check if it's a standalone bash command to execute
            if lang in ("bash", "sh", "shell", "console", "terminal") and len(code.splitlines()) <= 5:
                first_line = code.strip().splitlines()[0]
                if any(first_line.startswith(cmd) for cmd in ("python", "pytest", "npm", "pip", "cat", "ls", "node", "cargo", "git")):
                    calls.append(("run_command", {"command": code.strip()}))
                    continue

            # Check for file path in first 3 lines of code (e.g. # filename: snake.py)
            lines = code.splitlines()[:3]
            filename = None
            for line in lines:
                m_fn = re.search(r"(?:#|//|<!--|/\*)\s*(?:filename|filepath|file)?[:=\s]+([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9_]+)", line, re.IGNORECASE)
                if m_fn:
                    filename = m_fn.group(1).strip()
                    break

            # If not in code comment, check text before the code block (up to 250 chars)
            if not filename:
                preceding = text[max(0, start_idx - 250):start_idx]
                m_pre = re.search(r"(?:file|filename|save to|saved to|write to|create|created|into|named|path|in)\s+[`'\"]?([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9_]+)", preceding, re.IGNORECASE)
                if m_pre:
                    filename = m_pre.group(1).strip("`'\": ")
                else:
                    m_hdr = re.search(r"(?:###|\*\*|`)\s*([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9_]+)\s*(?:###|\*\*|`)", preceding)
                    if m_hdr:
                        filename = m_hdr.group(1).strip("`'\": ")
                    else:
                        m_ext = re.findall(r"\b([a-zA-Z0-9_\-./]+\.(?:py|js|ts|html|css|json|sh|bash|go|rs|c|cpp|h|md|txt|toml|yaml|yml))\b", preceding)
                        if m_ext:
                            filename = m_ext[-1].strip("`'\": ")

            # If still no filename, but code block has valid language extension and substantial code
            if not filename and lang in ("py", "python", "js", "html", "sh", "bash") and len(code.splitlines()) >= 2:
                ext_map = {"python": "py", "py": "py", "js": "js", "html": "html", "sh": "sh", "bash": "sh"}
                filename = f"main.{ext_map.get(lang, 'txt')}"

            if filename:
                calls.append(("write_file", {"path": filename, "content": code}))

    return calls


def format_tool_result(name: str, result: str) -> str:
    """Format tool output as observation XML tag."""
    return f"<tool_response name=\"{name}\">\n{result}\n</tool_response>"


AGENT_SYSTEM_PROMPT = """You are Gemma, an autonomous AI software engineer and coding agent executing tasks directly on the user's computer.
You have real filesystem and terminal access through tools.

CRITICAL MANDATES:
1. YOU ARE NOT A CHATBOT. YOU HAVE REAL WRITE AND EXECUTION ACCESS TO THIS COMPUTER.
2. NEVER JUST PRINT CODE IN CONVERSATIONAL CHAT MARKDOWN BLOCKS WHEN ASKED TO WRITE CODE, BUILD AN APP, CREATE A SCRIPT, OR FIX BUGS.
3. YOU MUST DIRECTLY WRITE CODE TO DISK USING <tool_call> with "write_file" OR "edit_file".
4. If the user does not specify a filename, choose an appropriate one (e.g. main.py, app.py, script.py, snake.py, index.html) and write it immediately to the workspace!
5. After creating or editing files, run tests or syntax checks using `run_command` if appropriate.

Available Tools:
1. write_file(path, content) - Create or overwrite a file directly on disk.
2. edit_file(path, old_str, new_str) - Surgically replace exact string in a file.
3. read_file(path, start_line, end_line) - Read file contents with line numbers.
4. list_dir(path, max_depth) - List project files and subdirectories.
5. grep_search(query, path) - Search for regex or code patterns across the codebase.
6. run_command(command) - Execute a shell command (e.g. python script.py, pytest, git status).

Tool Invocation Syntax:
Always invoke tools using a <tool_call> block with JSON:
<tool_call>
{"name": "write_file", "arguments": {"path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"}}
</tool_call>

Or to run commands:
<tool_call>
{"name": "run_command", "arguments": {"command": "python calculator.py"}}
</tool_call>

Workflow:
- To write new code -> <tool_call> write_file </tool_call>
- To modify code -> read_file first, then edit_file
- To verify -> run_command
- Once done -> Output a short confirmation explaining what files were created and the command results.
"""
