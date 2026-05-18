#!/usr/bin/env python3
"""Standalone x_search wrapper for Codex.

This script does not import Hermes Agent and does not shell out to ``hermes``.
It reuses Hermes-managed configuration and credentials from ``HERMES_HOME``:

* ``auth.json`` for xAI OAuth tokens created by ``hermes auth add xai-oauth``
* ``.env`` or process env for ``XAI_API_KEY``
* ``config.yaml`` for the ``x_search`` model/timeout/retry settings
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except Exception:  # pragma: no cover - non-POSIX fallback
    fcntl = None


DEFAULT_HERMES_HOME = Path.home() / ".hermes"
DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_X_SEARCH_MODEL = "grok-4.20-reasoning"
DEFAULT_X_SEARCH_TIMEOUT_SECONDS = 180
DEFAULT_X_SEARCH_RETRIES = 2
XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_DISCOVERY_URL = "https://auth.x.ai/.well-known/openid-configuration"
XAI_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120
MAX_HANDLES = 10


class XSearchError(RuntimeError):
    def __init__(self, message: str, error_type: str = "XSearchError", status_code: int | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _json_list(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = str(value or "").strip().lstrip("@")
        if item:
            cleaned.append(item)
    if len(cleaned) > MAX_HANDLES:
        raise XSearchError(f"handle filters support at most {MAX_HANDLES} handles", "ArgumentError")
    return cleaned


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _env_value(name: str, hermes_home: Path) -> str:
    dotenv = _read_dotenv(hermes_home / ".env")
    return str(dotenv.get(name) or os.environ.get(name) or "").strip()


def _load_config(hermes_home: Path) -> dict[str, dict[str, str]]:
    """Parse only the simple config.yaml keys this wrapper needs."""
    path = hermes_home / "config.yaml"
    result: dict[str, dict[str, str]] = {}
    if not path.exists():
        return result

    current: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw and not raw.startswith((" ", "\t")) and raw.rstrip().endswith(":"):
            current = raw.strip()[:-1]
            result.setdefault(current, {})
            continue
        if current and raw.startswith((" ", "\t")) and ":" in raw:
            key, value = raw.strip().split(":", 1)
            result.setdefault(current, {})[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _config_value(hermes_home: Path, section: str, key: str, default: str) -> str:
    return _load_config(hermes_home).get(section, {}).get(key, default) or default


def _get_model(hermes_home: Path) -> str:
    return _config_value(hermes_home, "x_search", "model", DEFAULT_X_SEARCH_MODEL).strip()


def _get_timeout(hermes_home: Path) -> int:
    raw = _config_value(hermes_home, "x_search", "timeout_seconds", str(DEFAULT_X_SEARCH_TIMEOUT_SECONDS))
    try:
        return max(30, int(raw))
    except Exception:
        return DEFAULT_X_SEARCH_TIMEOUT_SECONDS


def _get_retries(hermes_home: Path) -> int:
    raw = _config_value(hermes_home, "x_search", "retries", str(DEFAULT_X_SEARCH_RETRIES))
    try:
        return max(0, int(raw))
    except Exception:
        return DEFAULT_X_SEARCH_RETRIES


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    form_body: dict[str, str] | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    body: bytes | None = None
    req_headers = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    elif form_body is not None:
        body = urllib.parse.urlencode(form_body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req_headers.setdefault("Accept", "application/json")

    request = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise XSearchError(detail or f"HTTP {exc.code} from {url}", "HTTPError", status_code=exc.code) from exc
    except urllib.error.URLError as exc:
        raise XSearchError(str(exc.reason), "ConnectionError") from exc

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise XSearchError(f"Invalid JSON from {url}: {text[:500]}", "JSONDecodeError") from exc
    if not isinstance(parsed, dict):
        raise XSearchError(f"JSON response from {url} was not an object", "InvalidResponse")
    return parsed


def _validate_xai_endpoint(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or (host != "x.ai" and not host.endswith(".x.ai")):
        raise XSearchError(f"Refusing non-xAI OAuth endpoint: {url}", "InvalidOAuthEndpoint")
    return url


def _xai_discovery(timeout: float = 15) -> dict[str, str]:
    payload = _http_json(XAI_OAUTH_DISCOVERY_URL, timeout=timeout)
    token_endpoint = str(payload.get("token_endpoint") or "").strip()
    if not token_endpoint:
        raise XSearchError("xAI discovery response missing token_endpoint", "InvalidOAuthDiscovery")
    return {"token_endpoint": _validate_xai_endpoint(token_endpoint)}


def _token_is_expiring(access_token: str, skew_seconds: int = XAI_ACCESS_TOKEN_REFRESH_SKEW_SECONDS) -> bool:
    if "." not in access_token:
        return False
    try:
        payload_b64 = access_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8"))
        exp = payload.get("exp")
        return isinstance(exp, (int, float)) and float(exp) <= time.time() + skew_seconds
    except Exception:
        return False


class _AuthStoreLock:
    def __init__(self, hermes_home: Path) -> None:
        self.lock_path = hermes_home / "auth.lock"
        self.handle: Any = None

    def __enter__(self) -> "_AuthStoreLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.lock_path.open("a+")
        if fcntl is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.handle is not None:
            if fcntl is not None:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def _load_auth_store(hermes_home: Path) -> dict[str, Any]:
    path = hermes_home / "auth.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _save_auth_store(hermes_home: Path, data: dict[str, Any]) -> None:
    path = hermes_home / "auth.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except Exception:
        pass


def _refresh_xai_oauth(hermes_home: Path, state: dict[str, Any], timeout: float) -> dict[str, Any]:
    tokens = dict(state.get("tokens") or {})
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    if not refresh_token:
        raise XSearchError("xAI OAuth refresh_token is missing. Re-run `hermes auth add xai-oauth`.", "AuthError")

    discovery = dict(state.get("discovery") or {})
    token_endpoint = str(discovery.get("token_endpoint") or "").strip()
    if not token_endpoint:
        token_endpoint = _xai_discovery(timeout)["token_endpoint"]
    token_endpoint = _validate_xai_endpoint(token_endpoint)

    payload = _http_json(
        token_endpoint,
        method="POST",
        form_body={
            "grant_type": "refresh_token",
            "client_id": XAI_OAUTH_CLIENT_ID,
            "refresh_token": refresh_token,
        },
        timeout=timeout,
    )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise XSearchError("xAI OAuth refresh response missing access_token", "AuthError")

    tokens["access_token"] = access_token
    tokens["refresh_token"] = str(payload.get("refresh_token") or refresh_token).strip()
    if payload.get("id_token"):
        tokens["id_token"] = str(payload["id_token"])
    if payload.get("expires_in") is not None:
        tokens["expires_in"] = payload["expires_in"]
    tokens["token_type"] = str(payload.get("token_type") or tokens.get("token_type") or "Bearer")
    state["tokens"] = tokens
    state["auth_mode"] = "oauth_pkce"
    state["discovery"] = {**discovery, "token_endpoint": token_endpoint}
    state["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    store = _load_auth_store(hermes_home)
    store.setdefault("providers", {})["xai-oauth"] = state
    _save_auth_store(hermes_home, store)
    return tokens


def _resolve_credentials(hermes_home: Path, *, force_refresh: bool = False) -> dict[str, str]:
    refresh_timeout = float(os.environ.get("HERMES_XAI_REFRESH_TIMEOUT_SECONDS", "20"))
    with _AuthStoreLock(hermes_home):
        store = _load_auth_store(hermes_home)
        state = dict((store.get("providers") or {}).get("xai-oauth") or {})
        tokens = dict(state.get("tokens") or {})
        access_token = str(tokens.get("access_token") or "").strip()
        if access_token and (force_refresh or _token_is_expiring(access_token)):
            tokens = _refresh_xai_oauth(hermes_home, state, refresh_timeout)
            access_token = str(tokens.get("access_token") or "").strip()
        if access_token:
            base_url = (
                os.environ.get("HERMES_XAI_BASE_URL", "").strip().rstrip("/")
                or _env_value("XAI_BASE_URL", hermes_home).rstrip("/")
                or DEFAULT_XAI_BASE_URL
            )
            return {"provider": "xai-oauth", "api_key": access_token, "base_url": base_url}

    api_key = _env_value("XAI_API_KEY", hermes_home)
    if api_key:
        base_url = _env_value("XAI_BASE_URL", hermes_home).rstrip("/") or DEFAULT_XAI_BASE_URL
        return {"provider": "xai", "api_key": api_key, "base_url": base_url}
    raise XSearchError(
        "No xAI credentials available. Run `hermes auth add xai-oauth` or set XAI_API_KEY.",
        "AuthError",
    )


def _extract_response_text(payload: dict[str, Any]) -> str:
    text = str(payload.get("output_text") or "").strip()
    if text:
        return text
    parts: list[str] = []
    for item in payload.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                value = str(content.get("text") or "").strip()
                if value:
                    parts.append(value)
    return "\n\n".join(parts).strip()


def _extract_inline_citations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for item in payload.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            for annotation in content.get("annotations", []) or []:
                if isinstance(annotation, dict) and annotation.get("type") == "url_citation":
                    citations.append(
                        {
                            "url": annotation.get("url", ""),
                            "title": annotation.get("title", ""),
                            "start_index": annotation.get("start_index"),
                            "end_index": annotation.get("end_index"),
                        }
                    )
    return citations


def _x_search(args: argparse.Namespace, hermes_home: Path) -> dict[str, Any]:
    allowed = _json_list(args.allowed_handle)
    excluded = _json_list(args.excluded_handle)
    if allowed and excluded:
        raise XSearchError("allowed_x_handles and excluded_x_handles cannot be used together", "ArgumentError")

    creds = _resolve_credentials(hermes_home)
    tool_def: dict[str, Any] = {"type": "x_search"}
    if allowed:
        tool_def["allowed_x_handles"] = allowed
    if excluded:
        tool_def["excluded_x_handles"] = excluded
    if args.from_date.strip():
        tool_def["from_date"] = args.from_date.strip()
    if args.to_date.strip():
        tool_def["to_date"] = args.to_date.strip()
    if args.image_understanding:
        tool_def["enable_image_understanding"] = True
    if args.video_understanding:
        tool_def["enable_video_understanding"] = True

    model = _get_model(hermes_home)
    body = {
        "model": model,
        "input": [{"role": "user", "content": args.query.strip()}],
        "tools": [tool_def],
        "store": False,
    }
    timeout = _get_timeout(hermes_home)
    retries = _get_retries(hermes_home)
    response: dict[str, Any] | None = None
    for attempt in range(retries + 1):
        try:
            response = _http_json(
                f"{creds['base_url']}/responses",
                method="POST",
                headers={
                    "Authorization": f"Bearer {creds['api_key']}",
                    "User-Agent": "Codex-Hermes-x-search-skill/1",
                },
                json_body=body,
                timeout=timeout,
            )
            break
        except XSearchError as exc:
            retryable_http = exc.error_type == "HTTPError" and exc.status_code is not None and exc.status_code >= 500
            if attempt >= retries or (exc.error_type == "HTTPError" and not retryable_http):
                raise
            time.sleep(min(5.0, 1.5 * (attempt + 1)))

    if response is None:
        raise XSearchError("x_search request did not return a response", "RequestError")

    return {
        "success": True,
        "provider": "xai",
        "credential_source": creds["provider"],
        "tool": "x_search",
        "runner": "direct",
        "model": model,
        "query": args.query.strip(),
        "answer": _extract_response_text(response),
        "citations": list(response.get("citations") or []),
        "inline_citations": _extract_inline_citations(response),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run xAI x_search directly using Hermes credentials.")
    parser.add_argument("--query", help="X search query.")
    parser.add_argument("--allowed-handle", action="append", default=[], help="Only include this X handle. May be repeated.")
    parser.add_argument("--excluded-handle", action="append", default=[], help="Exclude this X handle. May be repeated.")
    parser.add_argument("--from-date", default="", help="Start date in YYYY-MM-DD format.")
    parser.add_argument("--to-date", default="", help="End date in YYYY-MM-DD format.")
    parser.add_argument("--image-understanding", action="store_true", help="Ask xAI to understand images on matched posts.")
    parser.add_argument("--video-understanding", action="store_true", help="Ask xAI to understand videos on matched posts.")
    parser.add_argument("--check", action="store_true", help="Check whether xAI credentials are available.")
    parser.add_argument("--hermes-home", default=str(DEFAULT_HERMES_HOME), help="Hermes home directory.")
    parser.add_argument("--force-refresh", action="store_true", help="Force refresh of xAI OAuth credentials.")
    args = parser.parse_args()

    hermes_home = Path(args.hermes_home).expanduser()
    try:
        if args.check:
            creds = _resolve_credentials(hermes_home, force_refresh=args.force_refresh)
            _print_json(
                {
                    "success": True,
                    "registered": True,
                    "toolset": "x_search",
                    "requirements_ok": True,
                    "credential_source": creds["provider"],
                    "runner": "direct",
                    "model": _get_model(hermes_home),
                    "timeout_seconds": _get_timeout(hermes_home),
                    "retries": _get_retries(hermes_home),
                    "hermes_home": str(hermes_home),
                }
            )
            return 0

        if not args.query:
            parser.error("--query is required unless --check is used")
        _print_json(_x_search(args, hermes_home))
        return 0
    except XSearchError as exc:
        _print_json({"success": False, "tool": "x_search", "error": str(exc), "error_type": exc.error_type})
        return 1
    except Exception as exc:
        _print_json({"success": False, "tool": "x_search", "error": str(exc), "error_type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
