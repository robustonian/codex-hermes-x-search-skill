#!/usr/bin/env python3
"""Codex skill wrapper for Hermes Agent's x_search tool."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


DEFAULT_HERMES_HOME = Path.home() / ".hermes"
DEFAULT_HERMES_AGENT = DEFAULT_HERMES_HOME / "hermes-agent"
REEXEC_ENV = "CODEX_HERMES_X_SEARCH_REEXECED"


def _add_hermes_to_path(hermes_agent: Path) -> None:
    if not hermes_agent.exists():
        raise SystemExit(
            json.dumps(
                {
                    "success": False,
                    "error": f"Hermes Agent checkout not found: {hermes_agent}",
                },
                ensure_ascii=False,
            )
        )
    sys.path.insert(0, str(hermes_agent))


def _maybe_reexec_with_hermes_python(hermes_agent: Path) -> None:
    if os.environ.get(REEXEC_ENV):
        return

    candidates = [
        hermes_agent / "venv" / "bin" / "python",
        hermes_agent / ".venv" / "bin" / "python",
    ]
    current = Path(sys.executable).resolve()
    for candidate in candidates:
        if candidate.exists() and candidate.resolve() != current:
            os.environ[REEXEC_ENV] = "1"
            os.execv(str(candidate), [str(candidate), __file__, *sys.argv[1:]])


def _json_list(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = str(value or "").strip().lstrip("@")
        if item:
            cleaned.append(item)
    return cleaned


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Hermes x_search from Codex.")
    parser.add_argument("--query", help="X search query.")
    parser.add_argument("--allowed-handle", action="append", default=[], help="Only include this X handle. May be repeated.")
    parser.add_argument("--excluded-handle", action="append", default=[], help="Exclude this X handle. May be repeated.")
    parser.add_argument("--from-date", default="", help="Start date in YYYY-MM-DD format.")
    parser.add_argument("--to-date", default="", help="End date in YYYY-MM-DD format.")
    parser.add_argument("--image-understanding", action="store_true", help="Ask xAI to understand images on matched posts.")
    parser.add_argument("--video-understanding", action="store_true", help="Ask xAI to understand videos on matched posts.")
    parser.add_argument("--check", action="store_true", help="Check whether x_search is registered and credentialed.")
    parser.add_argument("--hermes-home", default=str(DEFAULT_HERMES_HOME), help="Hermes home directory.")
    parser.add_argument("--hermes-agent", default=str(DEFAULT_HERMES_AGENT), help="Hermes Agent checkout directory.")
    args = parser.parse_args()

    hermes_home = Path(args.hermes_home).expanduser()
    hermes_agent = Path(args.hermes_agent).expanduser()
    os.environ.setdefault("HERMES_HOME", str(hermes_home))
    _maybe_reexec_with_hermes_python(hermes_agent)
    _add_hermes_to_path(hermes_agent)

    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv(hermes_home=hermes_home)
    except Exception:
        pass

    from tools.registry import registry
    from tools.x_search_tool import (
        _get_x_search_model,
        _get_x_search_retries,
        _get_x_search_timeout_seconds,
        x_search_tool,
    )
    from tools.xai_http import resolve_xai_http_credentials

    if args.check:
        entry = registry.get_entry("x_search")
        credential_source = ""
        requirements_ok = False
        try:
            creds = resolve_xai_http_credentials()
            if str(creds.get("api_key") or "").strip():
                requirements_ok = True
                credential_source = str(creds.get("provider") or "")
        except Exception:
            requirements_ok = False
            credential_source = ""
        _print_json(
            {
                "success": True,
                "registered": bool(entry),
                "toolset": getattr(entry, "toolset", None),
                "requirements_ok": requirements_ok,
                "credential_source": credential_source,
                "model": _get_x_search_model(),
                "timeout_seconds": _get_x_search_timeout_seconds(),
                "retries": _get_x_search_retries(),
                "hermes_home": str(hermes_home),
                "hermes_agent": str(hermes_agent),
                "python_executable": sys.executable,
            }
        )
        return 0

    if not args.query:
        parser.error("--query is required unless --check is used")

    result = x_search_tool(
        query=args.query,
        allowed_x_handles=_json_list(args.allowed_handle),
        excluded_x_handles=_json_list(args.excluded_handle),
        from_date=args.from_date,
        to_date=args.to_date,
        enable_image_understanding=args.image_understanding,
        enable_video_understanding=args.video_understanding,
    )

    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        _print_json({"success": False, "error": "Hermes x_search returned non-JSON output", "raw": result})
        return 1

    _print_json(parsed)
    return 0 if parsed.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
