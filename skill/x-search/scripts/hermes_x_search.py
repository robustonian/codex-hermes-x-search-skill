#!/usr/bin/env python3
"""Codex skill wrapper for Hermes Agent's x_search tool.

The wrapper calls the installed ``hermes`` CLI in one-shot mode instead of
importing Hermes Agent internals. This keeps the skill independent from a
Hermes Agent source checkout while reusing Hermes-managed auth and config.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_HERMES_HOME = Path.home() / ".hermes"
DEFAULT_TIMEOUT_SECONDS = 240


def _json_list(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = str(value or "").strip().lstrip("@")
        if item:
            cleaned.append(item)
    return cleaned


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _resolve_hermes_command(command: str) -> str | None:
    expanded = str(Path(command).expanduser()) if "/" in command else command
    if "/" in expanded:
        return expanded if Path(expanded).exists() else None
    return shutil.which(expanded)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(stripped[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _run_hermes(
    hermes_command: str,
    prompt: str,
    hermes_home: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("HERMES_HOME", str(hermes_home))
    return subprocess.run(
        [
            hermes_command,
            "-z",
            prompt,
            "--toolsets",
            "x_search",
            "--ignore-rules",
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def _run_hermes_tools_list(
    hermes_command: str,
    hermes_home: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("HERMES_HOME", str(hermes_home))
    return subprocess.run(
        [hermes_command, "tools", "list"],
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def _parse_x_search_status(output: str) -> tuple[bool, bool]:
    for line in output.splitlines():
        if " x_search " not in f" {line} ":
            continue
        lowered = line.lower()
        registered = True
        enabled = "enabled" in lowered or "✓" in line
        return registered, enabled
    return False, False


def _build_search_prompt(args: argparse.Namespace) -> str:
    allowed = _json_list(args.allowed_handle)
    excluded = _json_list(args.excluded_handle)
    request: dict[str, Any] = {
        "query": args.query.strip(),
        "allowed_x_handles": allowed,
        "excluded_x_handles": excluded,
        "from_date": args.from_date.strip(),
        "to_date": args.to_date.strip(),
        "enable_image_understanding": bool(args.image_understanding),
        "enable_video_understanding": bool(args.video_understanding),
    }
    return (
        "Use the x_search tool exactly once with the following request. "
        "Return only a JSON object, with no markdown or commentary. "
        "The JSON object must include: success, provider, credential_source, "
        "tool, query, answer, citations, inline_citations. "
        "If the tool fails, return success=false and include error and error_type. "
        f"Request JSON: {json.dumps(request, ensure_ascii=False)}"
    )


def _command_error_payload(
    process: subprocess.CompletedProcess[str],
    *,
    hermes_command: str,
    hermes_home: Path,
) -> dict[str, Any]:
    stderr = process.stderr.strip()
    stdout = process.stdout.strip()
    error = stderr or stdout or f"hermes exited with status {process.returncode}"
    return {
        "success": False,
        "tool": "x_search",
        "error": error,
        "error_type": "HermesCommandError",
        "returncode": process.returncode,
        "hermes_command": hermes_command,
        "hermes_home": str(hermes_home),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Hermes x_search from Codex via hermes -z.")
    parser.add_argument("--query", help="X search query.")
    parser.add_argument("--allowed-handle", action="append", default=[], help="Only include this X handle. May be repeated.")
    parser.add_argument("--excluded-handle", action="append", default=[], help="Exclude this X handle. May be repeated.")
    parser.add_argument("--from-date", default="", help="Start date in YYYY-MM-DD format.")
    parser.add_argument("--to-date", default="", help="End date in YYYY-MM-DD format.")
    parser.add_argument("--image-understanding", action="store_true", help="Ask xAI to understand images on matched posts.")
    parser.add_argument("--video-understanding", action="store_true", help="Ask xAI to understand videos on matched posts.")
    parser.add_argument("--check", action="store_true", help="Check whether Hermes one-shot mode can see x_search.")
    parser.add_argument("--hermes-home", default=str(DEFAULT_HERMES_HOME), help="Hermes home directory.")
    parser.add_argument("--hermes-command", default="hermes", help="Hermes CLI command or path.")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Hermes command timeout.")
    args = parser.parse_args()

    hermes_home = Path(args.hermes_home).expanduser()
    hermes_command = _resolve_hermes_command(args.hermes_command)
    if not hermes_command:
        _print_json(
            {
                "success": False,
                "tool": "x_search",
                "error": f"Hermes CLI not found: {args.hermes_command}",
                "error_type": "HermesNotFound",
                "hermes_home": str(hermes_home),
            }
        )
        return 1

    try:
        if args.check:
            process = _run_hermes_tools_list(
                hermes_command=hermes_command,
                hermes_home=hermes_home,
                timeout_seconds=max(30, int(args.timeout_seconds)),
            )
        else:
            if not args.query:
                parser.error("--query is required unless --check is used")
            if args.allowed_handle and args.excluded_handle:
                _print_json(
                    {
                        "success": False,
                        "tool": "x_search",
                        "error": "allowed_x_handles and excluded_x_handles cannot be used together",
                        "error_type": "ArgumentError",
                    }
                )
                return 1
            process = _run_hermes(
                hermes_command=hermes_command,
                prompt=_build_search_prompt(args),
                hermes_home=hermes_home,
                timeout_seconds=max(30, int(args.timeout_seconds)),
            )
    except subprocess.TimeoutExpired as exc:
        _print_json(
            {
                "success": False,
                "tool": "x_search",
                "error": f"hermes timed out after {exc.timeout} seconds",
                "error_type": "TimeoutExpired",
                "hermes_command": hermes_command,
                "hermes_home": str(hermes_home),
            }
        )
        return 1

    if args.check:
        if process.returncode != 0:
            _print_json(_command_error_payload(process, hermes_command=hermes_command, hermes_home=hermes_home))
            return 1
        registered, enabled = _parse_x_search_status(process.stdout)
        _print_json(
            {
                "success": True,
                "registered": registered,
                "toolset": "x_search",
                "requirements_ok": enabled,
                "enabled": enabled,
                "runner": "hermes tools list",
                "tool": "x_search",
                "hermes_command": hermes_command,
                "hermes_home": str(hermes_home),
            }
        )
        return 0 if registered and enabled else 1

    if process.returncode != 0:
        _print_json(_command_error_payload(process, hermes_command=hermes_command, hermes_home=hermes_home))
        return 1

    parsed = _extract_json_object(process.stdout)
    if parsed is None:
        _print_json(
            {
                "success": False,
                "tool": "x_search",
                "error": "Hermes one-shot returned non-JSON output",
                "error_type": "InvalidHermesOutput",
                "raw": process.stdout.strip(),
                "stderr": process.stderr.strip(),
                "hermes_command": hermes_command,
                "hermes_home": str(hermes_home),
            }
        )
        return 1

    parsed.setdefault("tool", "x_search")
    parsed.setdefault("runner", "hermes -z")
    parsed.setdefault("hermes_command", hermes_command)
    parsed.setdefault("hermes_home", str(hermes_home))
    _print_json(parsed)
    return 0 if parsed.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
