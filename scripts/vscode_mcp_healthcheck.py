#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT
"""VS Code/devcontainer MCP workspace health check."""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

BASE_URL = os.getenv("MCP_HEALTHCHECK_BASE_URL", "http://localhost:8000").rstrip("/")
MCP_URL = os.getenv("MCP_HEALTHCHECK_MCP_URL", urljoin(BASE_URL + "/", "mcp"))
OLLAMA_URL = os.getenv("MCP_HEALTHCHECK_OLLAMA_URL", "http://localhost:2345")
TOKEN_ENV = os.getenv("MCP_HEALTHCHECK_TOKEN_ENV", "MCP_HTTP_BEARER_TOKEN")
TOKEN = os.getenv(TOKEN_ENV, "")
EXPECTED_MUTATIONS = os.getenv("MCP_HEALTHCHECK_EXPECT_ALLOW_MUTATIONS", "true").lower()
TIMEOUT_SECONDS = float(os.getenv("MCP_HEALTHCHECK_TIMEOUT_SECONDS", "3"))


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    remediation: str = ""


def _request_json(url: str, headers: dict[str, str] | None = None) -> tuple[int, Any, str]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        return exc.code, parsed, body
    except Exception as exc:  # noqa: BLE001 - CLI needs actionable diagnostics.
        return 0, None, str(exc)


def _request_status(url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read(512).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(512).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - CLI needs actionable diagnostics.
        return 0, str(exc)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or "localhost", parsed.port or (443 if parsed.scheme == "https" else 80)


_EXPECTED_UNAUTH_DETAILS = (
    "missing bearer token",
    "mcp_http_bearer_token is not configured",
    "http auth is enabled but",
)
_AUTH_ERROR_DETAILS = _EXPECTED_UNAUTH_DETAILS + (
    "invalid bearer token",
    "unauthorized",
    "forbidden",
)


def _is_expected_unauth_mcp_rejection(status: int, body: str) -> bool:
    body_lower = body.lower()
    return status in {401, 403} and any(detail in body_lower for detail in _EXPECTED_UNAUTH_DETAILS)


def _is_auth_error_response(status: int, body: str) -> bool:
    body_lower = body.lower()
    return status in {401, 403} or any(detail in body_lower for detail in _AUTH_ERROR_DETAILS)


def run_checks() -> list[Check]:
    checks: list[Check] = []

    health_status, health, health_body = _request_json(f"{BASE_URL}/healthz")
    health_ok = health_status == 200 and isinstance(health, dict) and health.get("ok") is True
    checks.append(
        Check(
            "health endpoint",
            health_ok,
            f"GET {BASE_URL}/healthz returned {health_status or 'no response'}",
            "Start/rebuild the devcontainer, then check 'Docker: Container Logs' or run `docker logs codebase-tooling-mcp`."
            if not health_ok
            else "",
        )
    )

    if not isinstance(health, dict):
        health = {}
    server = health.get("server") if isinstance(health.get("server"), dict) else {}
    ollama = health.get("ollama") if isinstance(health.get("ollama"), dict) else {}

    checks.append(
        Check(
            "HTTP transport",
            health.get("transport") in {"http", "streamable-http", "streamable_http"}
            and server.get("http_mode") is True,
            f"transport={health.get('transport')!r}, http_mode={server.get('http_mode')!r}",
            "Set MCP_TRANSPORT=http and rebuild/reopen the devcontainer.",
        )
    )

    expected_mutations_bool = EXPECTED_MUTATIONS in {"1", "true", "yes", "on"}
    mutation_ok = bool(health.get("allow_mutations")) is expected_mutations_bool
    checks.append(
        Check(
            "mutation mode",
            mutation_ok,
            f"allow_mutations={health.get('allow_mutations')!r}, expected={expected_mutations_bool}",
            "Set ALLOW_MUTATIONS=true for editing workflows, or MCP_HEALTHCHECK_EXPECT_ALLOW_MUTATIONS=false for read-only checks.",
        )
    )

    for label, url in [("MCP forwarded port", BASE_URL), ("Ollama forwarded port", OLLAMA_URL)]:
        host, port = _host_port(url)
        ok = _port_open(host, port)
        checks.append(
            Check(
                label,
                ok,
                f"{host}:{port} {'is reachable' if ok else 'is not reachable'}",
                "In VS Code, forward ports 8000 and 2345 from the devcontainer and confirm they are not occupied by another process."
                if not ok
                else "",
            )
        )

    ollama_health_ok = ollama.get("running") is True and (
        ollama.get("configured_port_listening") is True or ollama.get("port_11434_listening") is True
    )
    checks.append(
        Check(
            "Ollama status",
            ollama_health_ok,
            "running={!r}, configured_port={!r}, configured_port_listening={!r}".format(
                ollama.get("running"),
                ollama.get("configured_port"),
                ollama.get("configured_port_listening"),
            ),
            "Check the devcontainer logs for Ollama startup. If models are missing, keep OLLAMA_ALLOW_PULL=false for offline runs or opt in explicitly after reviewing network policy.",
        )
    )

    tags_status, _, _ = _request_json(f"{OLLAMA_URL.rstrip('/')}/api/tags")
    checks.append(
        Check(
            "Ollama API",
            tags_status == 200,
            f"GET {OLLAMA_URL.rstrip('/')}/api/tags returned {tags_status or 'no response'}",
            "Forward port 2345 and confirm OLLAMA_HOST=0.0.0.0:2345 inside the devcontainer.",
        )
    )

    unauth_status, unauth_body = _request_status(MCP_URL)
    unauth_ok = _is_expected_unauth_mcp_rejection(unauth_status, unauth_body)
    if TOKEN:
        auth_status, auth_body = _request_status(MCP_URL, {"Authorization": f"Bearer {TOKEN}"})
        token_reached_endpoint = auth_status != 0 and not _is_auth_error_response(auth_status, auth_body)
        auth_ok = unauth_ok and token_reached_endpoint
        auth_detail = (
            f"without token={unauth_status or 'no response'}"
            f" ({'expected auth rejection' if unauth_ok else 'unexpected response'}), "
            f"with ${TOKEN_ENV}={auth_status or 'no response'}"
        )
        if auth_status in {405, 406} and token_reached_endpoint:
            auth_detail += " (endpoint reached; method/content negotiation failed after auth)"
        auth_remediation = (
            f"Export {TOKEN_ENV} before rebuilding/opening the devcontainer and use the same env var in VS Code MCP config. "
            "Token mode must reject missing/incorrect tokens but accept the configured bearer token."
        )
    else:
        auth_ok = False
        auth_detail = (
            f"no ${TOKEN_ENV} set; unauthenticated MCP request returned {unauth_status or 'no response'}"
            f" ({'expected auth rejection' if unauth_ok else 'unexpected response'})"
        )
        auth_remediation = (
            f"Generate a local token with `export {TOKEN_ENV}=\"$(openssl rand -hex 32)\"`, "
            "then rebuild/reopen the devcontainer so the server and VS Code use the same value."
        )
    checks.append(Check("HTTP authorization state", auth_ok, auth_detail, auth_remediation if not auth_ok else ""))

    if health_body and not health_ok:
        checks.append(Check("health response body", False, health_body[:500], "Use this response when debugging server startup."))

    return checks


def main() -> int:
    checks = run_checks()
    failed = [check for check in checks if not check.ok]
    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")
        if not check.ok and check.remediation:
            print(f"       remediation: {check.remediation}")
    if failed:
        print(f"\n{len(failed)} check(s) failed.")
        return 1
    print("\nAll VS Code MCP workspace checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
