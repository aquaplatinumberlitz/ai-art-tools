#!/usr/bin/env python3
"""Probe SeaArt Python SDKs as alternatives to the Playwright DOM scraper.

This script intentionally does not modify or depend on seaart_trending.py
internals beyond optionally importing its public fetch_trending() function for
comparison.
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback
from pathlib import Path
from typing import Any


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key.lower() in {"token", "access_token", "refresh_token"} and item:
                out[key] = "<redacted>"
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def schema_shape(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {str(k): schema_shape(v, depth + 1) for k, v in list(value.items())[:20]}
    if isinstance(value, list):
        if not value:
            return []
        return [schema_shape(value[0], depth + 1)]
    if value is None:
        return "null"
    return type(value).__name__


def summarize_exception(exc: BaseException) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    body_text = ""
    if isinstance(body, bytes):
        body_text = body[:500].decode("utf-8", errors="replace")
    elif body is not None:
        body_text = str(body)[:500]
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "http_status": getattr(exc, "http_status", None),
        "code": getattr(exc, "code", None),
        "msg": getattr(exc, "msg", None),
        "body_excerpt": body_text,
        "cloudflare_challenge": "Just a moment" in body_text
        or "challenges.cloudflare.com" in body_text,
    }


def try_unofficial_seaart() -> dict[str, Any]:
    result: dict[str, Any] = {
        "package": "seaart",
        "imported": False,
        "version": None,
        "tourist_login": None,
        "post_fetch_attempts": [],
    }
    try:
        seaart = importlib.import_module("seaart")
        result["imported"] = True
        result["version"] = getattr(seaart, "__version__", None)
        sync_client = getattr(seaart, "SyncClient")
    except Exception as exc:
        result["import_error"] = summarize_exception(exc)
        return result

    try:
        with sync_client(timeout=25, impersonate="chrome") as client:
            try:
                session = client.auth.login_as_tourist()
                data = redact(to_jsonable(session))
                result["tourist_login"] = {
                    "ok": True,
                    "schema": schema_shape(data),
                    "sample": data,
                    "client_has_token": bool(getattr(client, "token", None)),
                }
            except Exception as exc:
                result["tourist_login"] = {"ok": False, "error": summarize_exception(exc)}

            attempts = [
                (
                    "SDK search.posts('', order_by='hot')",
                    lambda: client.search.posts("", order_by="hot", page=1, page_size=5),
                ),
                (
                    "SDK search.posts('ai', order_by='hot')",
                    lambda: client.search.posts("ai", order_by="hot", page=1, page_size=5),
                ),
                (
                    "raw POST square/v3/search/list",
                    lambda: client.request_raw(
                        "POST",
                        "square/v3/search/list",
                        json={
                            "obj_name": "",
                            "obj_name_en": "",
                            "obj_type": 4,
                            "order_by": "hot",
                            "page": 1,
                            "page_size": 5,
                            "scene": "square",
                        },
                    ),
                ),
            ]
            for name, call in attempts:
                try:
                    response = call()
                    if hasattr(response, "json"):
                        try:
                            payload = response.json()
                        except Exception:
                            payload = {
                                "status_code": getattr(response, "status_code", None),
                                "text_excerpt": getattr(response, "text", "")[:500],
                                "headers": dict(getattr(response, "headers", {}) or {}),
                            }
                    else:
                        payload = to_jsonable(response)
                    payload = redact(payload)
                    result["post_fetch_attempts"].append(
                        {
                            "name": name,
                            "ok": True,
                            "schema": schema_shape(payload),
                            "sample": payload,
                        }
                    )
                except Exception as exc:
                    result["post_fetch_attempts"].append(
                        {"name": name, "ok": False, "error": summarize_exception(exc)}
                    )
    except Exception as exc:
        result["client_error"] = summarize_exception(exc)

    return result


def try_official_gateway_sdk() -> dict[str, Any]:
    result: dict[str, Any] = {"package": "seaart-sdk", "imported": False}
    try:
        sdk = importlib.import_module("seaart_sdk")
        result.update(
            {
                "imported": True,
                "version": getattr(sdk, "SDK_VERSION", None),
                "top_level": [name for name in dir(sdk) if not name.startswith("_")][:80],
                "has_public_post_or_trending_resource": any(
                    key in name.lower()
                    for name in dir(sdk)
                    for key in ("trend", "post", "community", "square")
                ),
            }
        )
    except Exception as exc:
        result["import_error"] = summarize_exception(exc)
    return result


def try_playwright_comparison(count: int = 5) -> dict[str, Any]:
    result: dict[str, Any] = {"source": "scripts.seaart_trending.fetch_trending"}
    try:
        scripts_dir = Path(__file__).resolve().parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        seaart_trending = importlib.import_module("seaart_trending")
        posts = seaart_trending.fetch_trending(count)
        result.update(
            {
                "ok": True,
                "count": len(posts),
                "schema": schema_shape(posts),
                "sample": posts[: min(len(posts), 2)],
            }
        )
    except Exception as exc:
        result.update({"ok": False, "error": summarize_exception(exc)})
        result["traceback"] = traceback.format_exc(limit=3)
    return result


def main() -> int:
    report = {
        "unofficial_seaart": try_unofficial_seaart(),
        "seaart_sdk": try_official_gateway_sdk(),
        "playwright_comparison": try_playwright_comparison(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
