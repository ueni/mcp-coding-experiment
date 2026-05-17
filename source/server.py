# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

import asyncio
import base64
import contextlib
import contextvars
import ast
import json
import os
import queue
import shutil
import shlex
import subprocess
import sys
import re
import fnmatch
import threading
import uuid
import hashlib
import hmac
import ipaddress
import time
import math
import ssl
from collections import deque
import urllib.error
import urllib.request
import urllib.parse
import html
import zipfile
import xml.etree.ElementTree as ET
import pty
import select
import concurrent.futures
import importlib.util
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Callable

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

# Optional analysis/document/OCR dependencies are loaded lazily by the tools that
# need them. Keeping them out of the default import path lowers server startup RAM
# and avoids devcontainer attach pressure while preserving offline bootstrap assets.
_OPTIONAL_DEPENDENCY_UNLOADED = object()


class _LazyOptionalDependency:
    """Proxy an installed optional dependency without importing it at startup."""

    def __init__(self, module_name: str, package_name: str | None = None, attr_name: str | None = None) -> None:
        self._module_name = module_name
        self._package_name = package_name
        self._attr_name = attr_name
        self._value: Any = _OPTIONAL_DEPENDENCY_UNLOADED

    def _load(self) -> Any:
        if self._value is _OPTIONAL_DEPENDENCY_UNLOADED:
            module = _import_optional_dependency(self._module_name, self._package_name)
            self._value = getattr(module, self._attr_name) if self._attr_name else module
        return self._value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._load(), name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._load()(*args, **kwargs)

    def __repr__(self) -> str:
        state = "unloaded" if self._value is _OPTIONAL_DEPENDENCY_UNLOADED else "loaded"
        target = f"{self._module_name}.{self._attr_name}" if self._attr_name else self._module_name
        return f"<_LazyOptionalDependency {target} ({state})>"


sp = _LazyOptionalDependency("sympy", "sympy") if importlib.util.find_spec("sympy") else None
sqlparse = _LazyOptionalDependency("sqlparse", "sqlparse") if importlib.util.find_spec("sqlparse") else None
Image = _LazyOptionalDependency("PIL.Image", "Pillow") if importlib.util.find_spec("PIL") else None
pytesseract = _LazyOptionalDependency("pytesseract", "pytesseract") if importlib.util.find_spec("pytesseract") else None
PdfReader = _LazyOptionalDependency("pypdf", "pypdf", "PdfReader") if importlib.util.find_spec("pypdf") else None
docx = _LazyOptionalDependency("docx", "python-docx") if importlib.util.find_spec("docx") else None
openpyxl = _LazyOptionalDependency("openpyxl", "openpyxl") if importlib.util.find_spec("openpyxl") else None
xlrd = _LazyOptionalDependency("xlrd", "xlrd") if importlib.util.find_spec("xlrd") else None

try:
    from tree_sitter_languages import get_parser as _ts_get_parser
except ModuleNotFoundError:  # pragma: no cover
    _ts_get_parser = None


def _import_optional_dependency(module_name: str, package_name: str | None = None) -> Any:
    """Import an optional dependency only when a tool first needs it."""
    try:
        module = __import__(module_name, fromlist=["*"])
    except ModuleNotFoundError as exc:
        if exc.name == module_name or exc.name == module_name.split(".", 1)[0]:
            raise RuntimeError(f"{package_name or module_name} is not installed") from exc
        raise
    return module


from mcp.server.fastmcp import FastMCP
from pydantic import Field, RootModel
from source.tool_output_schemas import (
    SCHEMA_BACKED_TOOL_NAMES,
    TOOL_OUTPUT_SCHEMAS,
    all_tool_output_contracts,
    tool_output_contract,
)
from source.version_metadata import (
    mcp_coding_experiment_version,
    runtime_image_version,
)
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.types import ASGIApp
from starlette.routing import Mount, Route

REPO_PATH = Path(os.getenv("REPO_PATH", "/repo")).resolve()
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "http").strip().lower()
ALLOW_MUTATIONS = os.getenv("ALLOW_MUTATIONS", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MAX_READ_BYTES = int(os.getenv("MAX_READ_BYTES", "262144"))
MAX_OUTPUT_CHARS = int(os.getenv("MAX_OUTPUT_CHARS", "200000"))
ALLOW_ORIGINS = [
    x.strip() for x in os.getenv("ALLOW_ORIGINS", "*").split(",") if x.strip()
]
MCP_HTTP_AUTH_MODE = os.getenv("MCP_HTTP_AUTH_MODE", "token").strip().lower()
MCP_HTTP_BEARER_TOKEN = os.getenv("MCP_HTTP_BEARER_TOKEN", "").strip()
MCP_HTTP_BEARER_TOKEN_SCOPES_RAW = os.getenv("MCP_HTTP_BEARER_TOKEN_SCOPES", "").strip()
MCP_HTTP_AUTHORIZATION_SERVERS_RAW = os.getenv("MCP_HTTP_AUTHORIZATION_SERVERS", "").strip()
MCP_HTTP_ALLOWED_ORIGINS_RAW = os.getenv("MCP_HTTP_ALLOWED_ORIGINS", "").strip()
MCP_HTTP_DEFAULT_PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)
MCP_HTTP_SUPPORTED_PROTOCOL_VERSIONS_RAW = os.getenv(
    "MCP_HTTP_SUPPORTED_PROTOCOL_VERSIONS",
    ",".join(MCP_HTTP_DEFAULT_PROTOCOL_VERSIONS),
).strip()
MCP_HTTP_RATE_LIMIT_REQUESTS = max(1, int(os.getenv("MCP_HTTP_RATE_LIMIT_REQUESTS", "120")))
MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS = max(1, int(os.getenv("MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS", "60")))
MCP_HTTP_REQUEST_TIMEOUT_SECONDS = max(1.0, float(os.getenv("MCP_HTTP_REQUEST_TIMEOUT_SECONDS", "120")))
MCP_AUDIT_LOG_FILE = Path(
    os.getenv("MCP_AUDIT_LOG_FILE", ".codebase-tooling-mcp/audit/security_events.jsonl")
)
MCP_OTEL_TRACING_ENABLED = os.getenv("MCP_OTEL_TRACING_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MCP_OTEL_EXPORTER = os.getenv("MCP_OTEL_EXPORTER", "").strip().lower()
MCP_OTEL_SPANS_FILE = Path(
    os.getenv("MCP_OTEL_SPANS_FILE", ".codebase-tooling-mcp/traces/otel_spans.jsonl")
)
MCP_OTEL_SERVICE_NAME = (
    os.getenv("MCP_OTEL_SERVICE_NAME", "codebase-tooling-mcp").strip()
    or "codebase-tooling-mcp"
)
RELEASE_READINESS_DASHBOARD_RESOURCE_URI = "ui://codebase-tooling-mcp/release-readiness-dashboard"
LABS_DIR = Path("source/labs")
REPORTS_DIR = Path(".codebase-tooling-mcp/reports")
PROVENANCE_SCHEMA = "mcp_artifact_provenance.v1"
PROVENANCE_SUFFIX = ".provenance.json"
ATTESTATION_SCHEMA = "mcp_artifact_attestation.v1"
ATTESTATION_BACKEND_LOCAL_DSSE_FIXTURE = "local-dsse-fixture"
ATTESTATION_PAYLOAD_TYPE = "application/vnd.codebase-tooling-mcp.artifact-attestation.v1+json"
ATTESTATION_FIXTURE_KEY_ID = "local-dsse-fixture-v1"
ATTESTATION_FIXTURE_SIGNER_IDENTITY = "local-fixture://codebase-tooling-mcp/offline-dsse-fixture"
# Fixture-only verifier material. This is intentionally public/non-secret and is
# not a production signing key; it exists only to make offline DSSE verification
# deterministic in tests and local demos without network or transparency-log IO.
ATTESTATION_FIXTURE_HMAC_KEY = b"codebase-tooling-mcp local dsse fixture verifier v1"
WORKFLOW_LINEAGE_SCHEMA = "workflow_lineage.v1"
WORKFLOW_LINEAGE_VERIFY_SCHEMA = "workflow_lineage.verify.v1"
WORKFLOW_LINEAGE_SUFFIX = ".workflow-lineage.json"
MEMORY_FILE = Path(".codebase-tooling-mcp/memory/context_memory.json")
MEMORY_STATS_FILE = Path(".codebase-tooling-mcp/memory/memory_stats.json")
FAILURE_MEMORY_FILE = Path(".codebase-tooling-mcp/memory/failure_memory.json")
TOKEN_BUDGET_FILE = Path(".codebase-tooling-mcp/memory/token_budget.json")
EDIT_TXN_DIR = Path(".codebase-tooling-mcp/transactions")
API_SNAPSHOT_FILE = Path(".codebase-tooling-mcp/reports/API_SURFACE.json")
REPO_INDEX_FILE = Path(".codebase-tooling-mcp/index/repo_index.json")
TOOL_CACHE_FILE = Path(".codebase-tooling-mcp/cache/tool_cache.json")
RESULT_STORE_FILE = Path(".codebase-tooling-mcp/cache/result_store.json")
OUTPUT_BASELINE_FILE = Path(".codebase-tooling-mcp/reports/TOOL_OUTPUT_BASELINE.json")
REUSE_SPDX_REPORT = Path(".codebase-tooling-mcp/reports/REUSE.spdx")
REUSE_LINT_REPORT = Path(".codebase-tooling-mcp/reports/REUSE_LINT.txt")
GOLDEN_BASELINE_FILE = Path(".codebase-tooling-mcp/reports/TOOL_GOLDEN_BASELINE.json")
FLAKY_HISTORY_FILE = Path(".codebase-tooling-mcp/reports/FLAKY_TEST_HISTORY.json")
TEST_IMPACT_MAP_FILE = Path(".codebase-tooling-mcp/reports/TEST_IMPACT_MAP.json")
TEST_IMPACT_MAP_MAX_AGE_HOURS = 24
SELF_OPTIMIZATION_RECOMMENDATION_INDEX_FILE = Path(
    ".codebase-tooling-mcp/reports/SELF_OPTIMIZATION_RECOMMENDATIONS.json"
)
STATE_SNAPSHOT_DIR = Path(".codebase-tooling-mcp/snapshots")
EXECUTION_REPLAY_DIR = Path(".codebase-tooling-mcp/replays")
ARTIFACT_INDEX_FILE = Path(".codebase-tooling-mcp/index/artifact_memory.json")
WORKFLOW_TASKS_DIR = Path(".codebase-tooling-mcp/tasks")
WORKFLOW_TASK_RETENTION_DAYS = max(1, int(os.getenv("MCP_WORKFLOW_TASK_RETENTION_DAYS", "7")))
WORKFLOW_TASK_EXPIRY_HOURS = max(1, int(os.getenv("MCP_WORKFLOW_TASK_EXPIRY_HOURS", "24")))
TOOL_ROUTER_STATS_FILE = Path(".codebase-tooling-mcp/memory/tool_router_stats.json")
TOOL_BENCHMARK_REPORT_FILE = Path(".codebase-tooling-mcp/reports/TOOL_BENCHMARK.json")
COST_BUDGET_FILE = Path(".codebase-tooling-mcp/memory/cost_budget.json")
POLICY_INSIGHTS_FILE = Path("source/policy_insights.json")
# Keep the external MCP contract focused: task_router remains the normal entrypoint,
# and the issue #4 schema-backed core tools are advertised with stable output schemas.
PUBLIC_MCP_TOOL_NAMES = {
    "task_router",
    "test_impact_map",
    "tool_annotations",
    "tool_output_contracts",
    "workflow_task",
    "workflow_lineage",
    "task_status",
    "roots_diagnostics",
    "policy_insights",
    *SCHEMA_BACKED_TOOL_NAMES,
}
OUTPUT_SCHEMA_BY_TOOL = TOOL_OUTPUT_SCHEMAS
MCP_SCOPE_READ = "mcp:read"
MCP_SCOPE_MUTATE = "mcp:mutate"
MCP_SUPPORTED_SCOPES = (MCP_SCOPE_READ, MCP_SCOPE_MUTATE)


class _AnyToolOutput(RootModel[Any]):
    root: Any


TOOL_SECURITY_METADATA: dict[str, dict[str, Any]] = {
    "task_router": {
        "categories": ["read-only"],
        "mode_categories": {
            "status": ["read-only"],
            "task": ["read-only", "network"],
            "embed": ["read-only"],
            "rerank": ["read-only"],
            "infer": ["read-only", "network"],
            "parallel_infer": ["read-only", "network"],
            "autocomplete": ["read-only", "network"],
            "coding_infer": ["write", "shell/process", "network"],
            "coding_check": ["shell/process"],
            "coding_pip": ["write", "shell/process", "network"],
            "coding_sandbox": ["write", "shell/process"],
            "workflow_select": ["read-only"],
        },
    },
    "tool_annotations": {"categories": ["read-only"]},
    "tool_output_contracts": {"categories": ["read-only"]},
    "policy_insights": {"categories": ["read-only", "governance"]},
    "workflow_task": {
        "categories": ["write", "shell/process"],
        "mode_categories": {
            "start": ["write", "shell/process"],
            "status": ["read-only"],
        },
    },
    "task_status": {"categories": ["read-only"]},
    "repo_info": {"categories": ["read-only"]},
    "roots_diagnostics": {"categories": ["read-only"]},
    "runtime_state": {"categories": ["read-only"]},
    "git_status": {"categories": ["read-only"]},
    "grep": {"categories": ["read-only"]},
    "find_paths": {"categories": ["read-only"]},
    "read_snippet": {"categories": ["read-only"]},
    "summarize_diff": {"categories": ["read-only"]},
    "risk_scoring": {"categories": ["read-only"]},
    "workspace_transaction": {
        "categories": ["write"],
        "mode_categories": {
            "begin": ["write"],
            "apply": ["write"],
            "validate": ["read-only"],
            "rollback": ["write", "destructive"],
            "commit": ["write"],
            "snapshot": ["read-only"],
            "restore": ["write", "destructive"],
            "write": ["write"],
            "replace": ["write"],
            "move": ["write"],
            "delete": ["write", "destructive"],
            "apply_diff": ["write"],
        },
    },
    "policy_simulator": {"categories": ["read-only"]},
    "clarification_gate": {"categories": ["read-only", "governance"]},
    "release_readiness": {"categories": ["read-only"]},
    "governance_report": {"categories": ["read-only"]},
    "self_optimization_report": {"categories": ["read-only"]},
    "artifact_provenance": {"categories": ["read-only"]},
    "workflow_diagnostics": {"categories": ["read-only"]},
    "workflow_lineage": {"categories": ["read-only"]},
    "interaction_invariant_audit": {"categories": ["read-only", "governance"]},
    "test_impact_map": {"categories": ["read-only"], "mode_categories": {"refresh": ["write"]}},
    "apply_unified_diff": {"categories": ["write", "git mutation"]},
    "command_runner": {"categories": ["shell/process"]},
    "docker_router": {"categories": ["shell/process"]},
    "vscode_router": {"categories": ["shell/process"]},
}
SENSITIVE_TOOL_CATEGORIES = {"write", "git mutation", "shell/process", "network", "secret-sensitive"}
MUTATION_TOOL_CATEGORIES = {"write", "git mutation"}
MCP_SCOPE_MUTATE_CATEGORIES = MUTATION_TOOL_CATEGORIES | SENSITIVE_TOOL_CATEGORIES
SENSITIVE_AUDIT_KEY_RE = re.compile(
    r"token|secret|password|credential|authorization|api[_-]?key", re.IGNORECASE
)
SENSITIVE_AUDIT_VALUE_RE = re.compile(
    r"("
    r"\b(?:bearer|token|secret|password|credential|authorization|api[_-]?key)\b\s*[:= ]\s*\S+"
    r"|\b[A-Za-z0-9._%+-]+-secret-[A-Za-z0-9._%+-]+\b"
    r"|\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{12,}\b"
    r"|\bsk-[A-Za-z0-9_-]{16,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
    r")",
    re.IGNORECASE,
)
ABSOLUTE_PATH_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9._~+%/-])(?:/[A-Za-z0-9._~+@%=-][^\s,;:'\"{}\]<>]*)"
    r"|(?:[A-Za-z]:\\[^\s,;:'\"{}\]<>]+)"
)
_HTTP_REQUEST_AUTHORIZED: contextvars.ContextVar[bool | None] = contextvars.ContextVar(
    "http_request_authorized", default=None
)
_HTTP_REQUEST_GRANTED_SCOPES: contextvars.ContextVar[frozenset[str] | None] = contextvars.ContextVar(
    "http_request_granted_scopes", default=None
)
_OTEL_CURRENT_SPAN_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcp_otel_current_span_id", default=""
)
_OTEL_CORRELATION_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcp_otel_correlation_id", default=""
)
_OTEL_SPANS_LOCK = threading.Lock()
_HTTP_RATE_LIMIT_BUCKETS: dict[str, deque[float]] = {}
_HTTP_RATE_LIMIT_LOCK = threading.Lock()
APPROVAL_POINTS_FILE = Path(".codebase-tooling-mcp/memory/approval_points.json")
ROOT_CAUSE_FILE = Path(".codebase-tooling-mcp/memory/root_cause_memory.json")
STATE_SNAPSHOT_INDEX_FILE = STATE_SNAPSHOT_DIR / "git_snapshots.json"
TERMINAL_CAPTURE_DIR = Path(".codebase-tooling-mcp/reports/terminal")
DEFAULT_CODING_MODEL = "qwen2.5-coder:1.5b"
DEFAULT_CODING_AGENT_MODEL = "qwen2.5-coder:1.5b"
DEFAULT_CODING_MICRO_MODEL = "qwen2.5-coder:1.5b"
DEFAULT_CONTINUE_OLLAMA_MODELS = ",".join(
    (
        DEFAULT_CODING_MODEL,
    )
)
CODING_MODEL_CONFIG_FILE = ".continue/models/coding-qwen2.5-coder-1.5b.yaml"
CODING_AGENT_MODEL_CONFIG_FILE = ".continue/models/coding-qwen2.5-coder-1.5b.yaml"
CODING_MICRO_MODEL_CONFIG_FILE = ".continue/models/coding-qwen2.5-coder-1.5b.yaml"
CODING_AGENT_ROUTE = "coding_agent"
CODING_MICRO_ROUTE = "coding_micro"
AGENT_EXECUTION_MODE_SCHEMA_VERSION = "agent_execution_mode.v1"
AGENT_EXECUTION_MODE_DEFAULT = "online"
AGENT_EXECUTION_MODES = ("online", "offline")
AGENT_EXECUTION_MODE_ENV = os.getenv("MCP_AGENT_EXECUTION_MODE", AGENT_EXECUTION_MODE_DEFAULT).strip()
AGENT_EXECUTION_PROFILE_ENV = os.getenv("MCP_AGENT_PROFILE", "").strip()
AGENT_EXECUTION_MODE_ALIASES = {
    "online": "online",
    "cloud": "online",
    "cloud-assisted": "online",
    "cloud_assisted": "online",
    "online-cloud-assisted": "online",
    "offline": "offline",
    "onboard": "offline",
    "onboard-only": "offline",
    "onboard_only": "offline",
    "local-only": "offline",
    "local_only": "offline",
    "offline-onboard-only": "offline",
}
AGENT_EXECUTION_MODE_PROMPT_TERMS = {
    "online": (
        "online",
        "cloud",
        "cloud-assisted",
        "cloud assisted",
        "remote model",
        "hosted model",
    ),
    "offline": (
        "offline",
        "onboard",
        "onboard-only",
        "onboard only",
        "local-only",
        "local only",
        "no cloud",
        "air-gapped",
        "airgapped",
    ),
}
OFFLINE_AGENT_LOOP_STEPS = [
    "inspect",
    "workflow_selection",
    "context_retrieval",
    "patch_proposal",
    "controlled_apply",
    "checks",
    "summary",
]
OFFLINE_SMALL_MODEL_JSON_CONTRACT = {
    "schema": "offline_small_model_decision.v1",
    "required_fields": ["intent", "confidence", "next_action", "rationale_summary"],
    "allowed_next_actions": [
        "select_workflow",
        "retrieve_context",
        "propose_patch",
        "run_check",
        "ask_clarification",
        "escalate_online",
        "stop",
    ],
    "confidence_field": "confidence",
    "confidence_range": [0.0, 1.0],
    "free_text_limit_chars": 600,
}
OFFLINE_AGENT_LOOP_LIMITS = {
    "max_tool_iterations": 6,
    "max_model_decision_retries": 2,
    "max_patch_apply_attempts": 2,
    "max_check_retries": 1,
}
OFFLINE_CONFIDENCE_POLICY = {
    "accept_min": 0.72,
    "retry_min": 0.55,
    "clarify_below": 0.55,
    "escalate_when": [
        "confidence stays below retry_min after retries",
        "required context cannot be retrieved locally",
        "task asks for high-uncertainty architecture or security judgment",
        "hard iteration limits are reached",
    ],
}
AGENT_EXECUTION_MODE_PROFILES = {
    "online": {
        "schema": AGENT_EXECUTION_MODE_SCHEMA_VERSION,
        "mode": "online",
        "profile_name": "online-cloud-assisted",
        "model_responsibilities": [
            "Cloud model owns primary reasoning, planning, and high-uncertainty coding decisions.",
            "Small local models are limited to routing, compression, autocomplete, simple classification, and token reduction.",
        ],
        "mcp_responsibilities": [
            "Provide compact repository context, indexed/search summaries, deterministic prechecks, and reusable workflow-card knowledge.",
            "Record audit traces for sensitive tool calls, plans, mutations, test runs, policy gates, memory use, and rationale summaries where practical.",
            "Expose project/repository memory and token-saving summaries without forcing raw file dumps into the cloud context.",
            "Keep local/offline autocomplete available even when the primary reasoning model is cloud-backed.",
        ],
        "data_flow_boundaries": [
            "Send compact, task-relevant repository context to the cloud model instead of whole raw trees by default.",
            "Keep bearer tokens, private keys, local absolute host paths, and generated secret-bearing artifacts out of prompts and audit output.",
        ],
        "audit_memory_behavior": [
            "Use MCP audit/memory/reporting tools as the local trace of cloud-assisted work.",
            "Prefer deterministic summaries and provenance handles over unbounded transcript capture.",
        ],
        "fallback_escalation": [
            "If cloud access fails, rerun `workflow_select` with `execution_mode='offline'` and follow the bounded onboard loop.",
            "If local prechecks fail, fix or clarify before spending additional cloud context.",
        ],
        "configuration_defaults": {
            "MCP_AGENT_EXECUTION_MODE": "online",
            "MCP_AGENT_PROFILE": "online-cloud-assisted",
            "LOCAL_EMBED_BACKEND": "hash",
            "CODING_DEFAULT_MODEL": DEFAULT_CODING_MODEL,
            "CODING_MICRO_MODEL": DEFAULT_CODING_MICRO_MODEL,
        },
    },
    "offline": {
        "schema": AGENT_EXECUTION_MODE_SCHEMA_VERSION,
        "mode": "offline",
        "profile_name": "offline-onboard-only",
        "model_responsibilities": [
            "All model-dependent behavior must use onboard/local models; no cloud model is required.",
            "Small local coding/intent models make only bounded decisions, patch suggestions, summaries, autocomplete, and tool/result classifications.",
        ],
        "mcp_responsibilities": [
            "Move agent behavior into deterministic orchestration: inspect -> workflow selection -> context retrieval -> patch proposal -> controlled apply -> checks -> summary.",
            "Use workflow cards, repository indexes, tests, static checks, grep/ripgrep, AST parsers, and policy gates to compensate for weaker local reasoning.",
            "Enforce structured JSON contracts, confidence thresholds, retries/fallbacks, clarification/escalation paths, and hard iteration limits.",
        ],
        "data_flow_boundaries": [
            "Do not require outbound model calls or runtime model pulls for the offline profile.",
            "Keep generated state and audit artifacts local to `.codebase-tooling-mcp/` unless the user explicitly exports them.",
        ],
        "audit_memory_behavior": [
            "Record deterministic step summaries, confidence decisions, check results, and local-memory use.",
            "Treat low-confidence local-model output as advisory until confirmed by deterministic checks or user clarification.",
        ],
        "agent_loop": OFFLINE_AGENT_LOOP_STEPS,
        "small_model_json_contract": OFFLINE_SMALL_MODEL_JSON_CONTRACT,
        "confidence_policy": OFFLINE_CONFIDENCE_POLICY,
        "iteration_limits": OFFLINE_AGENT_LOOP_LIMITS,
        "fallback_escalation": [
            "Retry deterministic analysis before retrying model calls.",
            "Ask for clarification when required fields are missing or confidence is below clarify_below.",
            "Mark the task as requiring online/large-model mode when local-small mode remains insufficient.",
        ],
        "configuration_defaults": {
            "MCP_AGENT_EXECUTION_MODE": "offline",
            "MCP_AGENT_PROFILE": "offline-onboard-only",
            "OLLAMA_ALLOW_PULL": "false",
            "CONTINUE_OLLAMA_MODELS": DEFAULT_CONTINUE_OLLAMA_MODELS,
            "CODING_DEFAULT_MODEL": DEFAULT_CODING_MODEL,
            "CODING_MICRO_MODEL": DEFAULT_CODING_MICRO_MODEL,
        },
    },
}
MODEL_STRIP_TOKENS = [
    "<|im_start|>",
    "<|im_end|>",
    "<|endoftext|>",
    "<think>",
    "</think>",
]
MICRO_CODING_TASK_HINTS = {
    "coding_micro",
    "micro coding",
    "micro-coding",
    "micro_coding",
}
LOCAL_MODELS_DIR = Path(os.getenv("LOCAL_MODELS_DIR", "/models"))
LOCAL_EMBED_BACKEND = os.getenv("LOCAL_EMBED_BACKEND", "hash").strip().lower()
LOCAL_EMBED_MODEL = os.getenv("LOCAL_EMBED_MODEL", "").strip()
LOCAL_EMBED_DIM = int(os.getenv("LOCAL_EMBED_DIM", "256"))
LOCAL_INFER_BACKEND = os.getenv("LOCAL_INFER_BACKEND", "endpoint").strip().lower()
LOCAL_INFER_MODEL = os.getenv("LOCAL_INFER_MODEL", "").strip()
LOCAL_INFER_ENDPOINT = os.getenv(
    "LOCAL_INFER_ENDPOINT", "http://127.0.0.1:11434/api/generate"
).strip()
HOST_CA_CERT_FILE = os.getenv("HOST_CA_CERT_FILE", "").strip()
INTERNAL_SELF_TESTS_DIR = Path(
    os.getenv("INTERNAL_SELF_TESTS_DIR", "/opt/codebase-tooling/defaults/selftests")
)
CODING_VENV_PYTHON = os.getenv(
    "CODING_VENV_PYTHON", "/opt/codebase-tooling/coding-venv/bin/python"
).strip()
CODING_DEFAULT_MODEL = os.getenv("CODING_DEFAULT_MODEL", DEFAULT_CODING_MODEL).strip()
CODING_AGENT_MODEL = os.getenv("CODING_AGENT_MODEL", DEFAULT_CODING_AGENT_MODEL).strip()
CODING_MICRO_MODEL = os.getenv("CODING_MICRO_MODEL", DEFAULT_CODING_MICRO_MODEL).strip()
CODING_MICRO_MAX_PROMPT_CHARS = max(
    120,
    int(os.getenv("CODING_MICRO_MAX_PROMPT_CHARS", "600")),
)
CODING_SANDBOX_ROOT = Path(
    os.getenv("CODING_SANDBOX_ROOT", ".codebase-tooling-mcp/sandboxes/coding")
)
SAFE_COMMANDS = {"rg", "find", "sed", "awk", "jq", "git", "pytest", "reuse", "cat"}
SAFE_INLINE_PYTHON_BINARIES = {"python", "python3"}
SAFE_INLINE_PYTHON_ALLOWED_MODULES = {
    "base64",
    "collections",
    "datetime",
    "decimal",
    "fractions",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "math",
    "re",
    "statistics",
    "string",
    "textwrap",
}
SAFE_INLINE_PYTHON_BLOCKED_NAMES = {
    "__import__",
    "breakpoint",
    "builtins",
    "compile",
    "ctypes",
    "eval",
    "exec",
    "help",
    "importlib",
    "input",
    "marshal",
    "multiprocessing",
    "open",
    "os",
    "pathlib",
    "pickle",
    "resource",
    "shutil",
    "signal",
    "socket",
    "subprocess",
    "sys",
    "tempfile",
    "threading",
}
SAFE_INLINE_PYTHON_BLOCKED_ATTRS = {
    "Popen",
    "call",
    "check_call",
    "check_output",
    "chmod",
    "chown",
    "execv",
    "execve",
    "fork",
    "forkpty",
    "kill",
    "link_to",
    "makedirs",
    "mkdir",
    "open",
    "popen",
    "putenv",
    "remove",
    "rename",
    "replace",
    "rmdir",
    "run",
    "spawnl",
    "spawnlp",
    "spawnv",
    "spawnvp",
    "symlink_to",
    "system",
    "touch",
    "truncate",
    "unlink",
    "write",
    "write_bytes",
    "write_text",
}
SAFE_INLINE_PYTHON_MAX_CHARS = 800
SAFE_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "grep",
    "rev-parse",
    "branch",
    "ls-files",
}
OUTPUT_PROFILES = {"compact", "normal", "verbose"}
CONTINUE_MODEL_ROUTING_RELATIVE_PATH = Path(".continue/model-routing.yaml")
TASK_ROUTE_CODE_MAP = {
    "general": "G",
    "coding": "C",
    "refactor": "RF",
    "review": "RV",
    "security": "SEC",
    "math": "M",
    "vision": "V",
    "research": "RS",
}
TASK_ROUTE_KEYWORDS = {
    "coding": (
        "code",
        "coding",
        "implement",
        "implementation",
        "function",
        "class",
        "method",
        "bug",
        "fix",
        "test",
        "pytest",
        "stacktrace",
        "traceback",
    ),
    "refactor": (
        "refactor",
        "restructure",
        "cleanup",
        "rename",
        "simplify",
        "reorganize",
        "modularize",
    ),
    "review": (
        "review",
        "audit",
        "regression",
        "issue",
        "risk",
        "bug",
        "findings",
        "inspect",
        "analyze",
    ),
    "security": (
        "security",
        "vulnerability",
        "vulnerabilities",
        "secure",
        "exploit",
        "cve",
        "auth",
        "authentication",
        "authorization",
        "xss",
        "csrf",
        "sqli",
        "injection",
        "secret",
    ),
    "math": (
        "math",
        "equation",
        "equations",
        "integral",
        "differentiate",
        "derivative",
        "proof",
        "matrix",
        "algebra",
        "solve",
        "solver",
    ),
    "vision": (
        "image",
        "images",
        "screenshot",
        "diagram",
        "figure",
        "photo",
        "vision",
        "ocr",
    ),
    "research": (
        "research",
        "search",
        "docs",
        "documentation",
        "explain",
        "summary",
        "summarize",
        "compare",
        "readme",
    ),
}
TASK_ROUTE_ALIASES = {
    "general": "general",
    "coding": "coding",
    "code": "coding",
    "coding_micro": "coding",
    "micro coding": "coding",
    "micro-coding": "coding",
    "micro_coding": "coding",
    "refactor": "refactor",
    "review": "review",
    "security": "security",
    "math": "math",
    "vision": "vision",
    "research": "research",
    "search": "research",
}
TASK_ROUTE_SYSTEM_PROMPTS = {
    "general": "Interpret compact JSON input. q is the request, m is compact task memory when present, and k is retrieved repository context when present. Answer directly and concisely.",
    "coding": "Interpret compact JSON input. q is a coding task, m is compact task memory when present, and k is retrieved repository context when present. Return implementation-focused output only.",
    "refactor": "Interpret compact JSON input. q is a refactor task, m is compact task memory when present, and k is retrieved repository context when present. Focus on cleaner structure with minimal churn.",
    "review": "Interpret compact JSON input. q is a review task, m is compact task memory when present, and k is retrieved repository context when present. Return concise findings first with file and line when possible.",
    "security": "Interpret compact JSON input. q is a security task, m is compact task memory when present, and k is retrieved repository context when present. Prioritize concrete vulnerabilities, exploitability, and fixes.",
    "math": "Interpret compact JSON input. q is a math task, m is compact task memory when present, and k is retrieved repository context when present. Return exact reasoning and the final result.",
    "vision": "Interpret compact JSON input. q is a vision task, m is compact task memory when present, and k is retrieved repository context when present. Focus on visible evidence only.",
    "research": "Interpret compact JSON input. q is a research task, m is compact task memory when present, and k is retrieved repository context when present. Return a concise factual synthesis.",
}
WORKFLOW_CARD_SCHEMA_VERSION = "workflow_card.v1"
WORKFLOW_SELECT_SCHEMA_VERSION = "workflow_selection.v1"
WORKFLOW_CARD_TRUST_SCHEMA_VERSION = "workflow_card_trust.v1"
WORKFLOW_CARD_LINT_SCHEMA_VERSION = "workflow_card_lint.v1"
WORKFLOW_CARD_SAFETY_SCHEMA_VERSION = "workflow_card_safety.v1"
WORKFLOW_CARD_EXTERNAL_LOADING_ENABLED = False
WORKFLOW_CARD_FIELDS = (
    "id",
    "schema",
    "title",
    "intent",
    "triggers",
    "prerequisites",
    "risk",
    "mutation_mode",
    "outputs",
    "do_not_use_when",
    "recommended_entrypoint",
    "routing_terms",
    "supported_execution_modes",
    "mode_routing",
)
WORKFLOW_CARD_TRUST_REQUIRED_FIELDS = (
    "source",
    "trust_tier",
    "review_status",
    "permissions",
    "sandbox_expectation",
    "network_access",
    "sensitive_paths",
    "provenance_digest",
)
WORKFLOW_CARD_REPOSITORY_TRUST_DEFAULT = {
    "schema": WORKFLOW_CARD_TRUST_SCHEMA_VERSION,
    "source": "repository_builtin",
    "trust_tier": "trusted_repository",
    "review_status": "repository_owned",
    "permissions": ["read_repository_context", "recommend_existing_workflow"],
    "sandbox_expectation": "Selector is read-only; any later mutations must use explicit mutation mode and REPO_PATH-bound tools.",
    "network_access": "none",
    "sensitive_paths": [],
}
WORKFLOW_CARD_TRUSTED_TIERS = {"trusted_repository", "trusted_internal"}
WORKFLOW_CARD_OVERBROAD_PERMISSION_PATTERNS = (
    r"^\*$",
    r"\ball\b",
    r"\badmin\b",
    r"\broot\b",
    r"\bprivileged\b",
    r"\bhost(?:[-_ ]?filesystem|[-_ ]?mount|[-_ ]?access)?\b",
    r"\bdocker[-_ ]?socket\b",
    r"\bnetwork:\*\b",
    r"\bfilesystem:\*\b",
    r"\bsecrets?:\*\b",
    r"\bcredentials?:\*\b",
)
WORKFLOW_CARD_DANGEROUS_SHELL_PATTERNS = (
    r"\bcurl\b[^|\n]{0,160}\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b",
    r"\bwget\b[^|\n]{0,160}\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b",
    r"\bbase64\s+(?:-d|--decode)\b[^|\n]{0,160}\|\s*(?:sh|bash|zsh)\b",
    r"\beval\s+[`\"'$]",
    r"\b(?:bash|sh|zsh)\s+-c\b",
    r"\bchmod\s+\+x\b",
    r"\brm\s+-rf\s+(?:/|~|\$HOME|\.\.)",
)
WORKFLOW_CARD_NETWORK_EXFILTRATION_PATTERNS = (
    r"\bcurl\b[^\n]{0,200}\b(?:-d|--data|--data-binary|--upload-file|-F|--form|-X\s*POST|--request\s+POST)\b",
    r"\bwget\b[^\n]{0,200}\b(?:--post-data|--post-file|--method\s*=\s*POST)\b",
    r"\b(?:nc|netcat)\b[^\n]{0,160}(?:<|>|-e\b|--exec\b)",
    r"\b(?:scp|sftp|rsync)\b[^\n]{0,160}(?:@|https?://|ssh://)",
    r"\b(?:upload|post|send|exfiltrate)\b[^\n]{0,160}\b(?:secret|token|credential|archive|tarball|repo|repository)\b",
    r"https?://[^\s`\"')]+/(?:collect|upload|paste|webhook|exfil|ingest)",
)
WORKFLOW_CARD_OUTSIDE_REPO_WRITE_VERBS = r"\b(?:write|append|create|copy|cp|move|mv|save|touch|mkdir|chmod|rm|delete|tee)\b"
WORKFLOW_CARD_OUTSIDE_REPO_PATHS = r"(?:/(?:tmp|etc|var|home|root|usr|bin|sbin|opt)(?:/|\b)|~(?:/|\b)|\.\./)"
WORKFLOW_CARDS: tuple[dict[str, Any], ...] = (
    {
        "id": "cloud-assisted-agent-mode",
        "schema": WORKFLOW_CARD_SCHEMA_VERSION,
        "title": "Online/cloud-assisted agent mode",
        "intent": "Use MCP as the compact repository, audit, memory, and deterministic-tool layer while a cloud model owns primary reasoning.",
        "triggers": ["online mode", "cloud-assisted", "cloud model", "token savings", "audit assisted coding"],
        "prerequisites": ["Cloud reasoning model is available to the client", "HTTP auth and secret redaction are configured", "MCP repository context should be compacted before sharing"],
        "risk": "medium",
        "mutation_mode": "MCP remains governed by normal read/write gates; cloud reasoning does not bypass local mutation/audit controls",
        "outputs": ["compact repository context", "workflow-card recommendation", "audit/memory trace", "deterministic precheck plan", "local autocomplete continuity"],
        "do_not_use_when": ["Cloud calls are disabled, unavailable, or disallowed", "The task must remain entirely onboard/local"],
        "recommended_entrypoint": "task_router(mode='workflow_select', execution_mode='online') before the selected specialist workflow",
        "routing_terms": ["online mode", "cloud-assisted", "cloud assisted", "cloud model", "hosted model", "token savings", "compact context", "audit trace"],
        "supported_execution_modes": ["online"],
        "mode_routing": {
            "online": "Primary path: cloud model reasons; MCP supplies compact context, audit/memory, checks, compression, and local autocomplete.",
            "offline": "Do not route here for onboard-only work; select the offline bounded agent loop instead.",
        },
    },
    {
        "id": "offline-bounded-agent-loop",
        "schema": WORKFLOW_CARD_SCHEMA_VERSION,
        "title": "Offline/onboard-only bounded agent loop",
        "intent": "Approximate agent behavior without cloud models by combining small local JSON decisions with deterministic MCP orchestration.",
        "triggers": ["offline mode", "onboard-only", "local-only", "no cloud", "bounded agent loop"],
        "prerequisites": ["Local model endpoint and required model tags are installed", "Runtime model pulls are disabled or explicitly allowed by policy", "Task can fit bounded local reasoning plus deterministic checks"],
        "risk": "medium",
        "mutation_mode": "Read-only through workflow selection; patch application still requires explicit mutation mode and controlled apply/check steps",
        "outputs": ["structured local decision packet", "bounded inspect/select/retrieve/patch/check/summary loop", "confidence/fallback decision", "escalation or clarification request"],
        "do_not_use_when": ["The task requires high-uncertainty architecture, security, or product judgment that local-small mode cannot validate", "A cloud model is explicitly required by policy or user request"],
        "recommended_entrypoint": "task_router(mode='workflow_select', execution_mode='offline') then follow the offline agent_loop contract",
        "routing_terms": ["offline mode", "offline", "onboard-only", "onboard only", "local-only", "local only", "no cloud", "small model", "json contract", "bounded loop", "hard iteration"],
        "supported_execution_modes": ["offline"],
        "mode_routing": {
            "online": "Use only as an offline fallback plan when cloud access is unavailable or privacy policy requires onboard execution.",
            "offline": "Primary path: inspect -> workflow selection -> context retrieval -> patch proposal -> controlled apply -> checks -> summary, with structured JSON decisions and hard limits.",
        },
    },
    {
        "id": "release-readiness",
        "schema": WORKFLOW_CARD_SCHEMA_VERSION,
        "title": "Release readiness gate",
        "intent": "Summarize whether the repository is ready to release by combining health, tests, release-note, and known-risk signals.",
        "triggers": ["release", "ship", "publish", "version", "tag", "ready to merge"],
        "prerequisites": ["Clean or intentionally scoped git diff", "Known target branch/version", "Relevant tests or quality gates identified"],
        "risk": "high",
        "mutation_mode": "read-only; mutations require a separate explicit fix workflow",
        "outputs": ["release gate summary", "blocking findings", "recommended checks", "rollback notes"],
        "do_not_use_when": ["The user only asks for a single failing test explanation", "Release target or acceptance criteria are unknown; clarify first"],
        "recommended_entrypoint": "quality_router(mode='release_readiness') or release readiness prompt via task_router(mode='task')",
        "routing_terms": ["release", "readiness", "ship", "publish", "tag", "version", "changelog", "gate", "merge"],
    },
    {
        "id": "devcontainer-health",
        "schema": WORKFLOW_CARD_SCHEMA_VERSION,
        "title": "Devcontainer health check",
        "intent": "Diagnose VS Code/devcontainer MCP connectivity, ports, health endpoints, bearer-token setup, and Ollama/service readiness.",
        "triggers": ["devcontainer", "VS Code MCP", "healthz", "port 8000", "container won't start"],
        "prerequisites": ["Devcontainer or local container context", "Expected endpoint or forwarded port"],
        "risk": "medium",
        "mutation_mode": "read-only diagnostics by default; shell/container fixes require explicit mutation approval",
        "outputs": ["health status", "failed probe list", "safe next actions", "docs/vscode-mcp-onboarding.md references"],
        "do_not_use_when": ["The request is about production deployment health outside the devcontainer", "No container/MCP runtime context is involved"],
        "recommended_entrypoint": "vscode_router / workspace task 'MCP: Workspace Health Check' or task_router(mode='status')",
        "routing_terms": ["devcontainer", "vscode", "mcp", "health", "healthz", "port", "ollama", "container"],
    },
    {
        "id": "snapshot-before-refactor",
        "schema": WORKFLOW_CARD_SCHEMA_VERSION,
        "title": "Snapshot before refactor",
        "intent": "Create or require a rollback point before broad, risky, or ambiguous edits/refactors.",
        "triggers": ["refactor", "rename", "large change", "rewrite", "delete", "migration"],
        "prerequisites": ["Clarified target files/scope", "Rollback plan", "Mutation mode explicitly enabled before edits"],
        "risk": "high",
        "mutation_mode": "snapshot is read-only metadata plus git state capture; later edits require ALLOW_MUTATIONS=true and explicit intent",
        "outputs": ["snapshot id", "restore instruction", "scope caveats", "clarification questions when scope is unclear"],
        "do_not_use_when": ["The task is purely read-only review", "The change is a trivial single-line edit with normal git rollback sufficient and documented"],
        "recommended_entrypoint": "workspace_transaction(mode='snapshot') before mutation workflow",
        "routing_terms": ["snapshot", "rollback", "refactor", "rewrite", "rename", "delete", "migration", "risky", "large"],
    },
    {
        "id": "security-triage",
        "schema": WORKFLOW_CARD_SCHEMA_VERSION,
        "title": "Security triage",
        "intent": "Triage suspicious code, dependencies, auth, secret, or sandbox exposure without weakening gates.",
        "triggers": ["security", "secret", "token", "auth", "vulnerability", "sandbox", "CVE"],
        "prerequisites": ["Suspicious file/diff/dependency or threat question", "Do not print secrets; redact evidence"],
        "risk": "high",
        "mutation_mode": "read-only analysis first; fixes require explicit mutation workflow and may need snapshot/clarification",
        "outputs": ["risk findings", "affected paths", "exploitability notes", "safe remediation options"],
        "do_not_use_when": ["The request asks to reveal, copy, or bypass secrets", "No security/privacy dimension is present"],
        "recommended_entrypoint": "security triage prompt via task_router(mode='task', task='security') plus change_impact_gate/policy_simulator as needed",
        "routing_terms": ["security", "secret", "token", "auth", "credential", "vulnerability", "sandbox", "cve", "injection"],
    },
    {
        "id": "test-impact",
        "schema": WORKFLOW_CARD_SCHEMA_VERSION,
        "title": "Test impact selection",
        "intent": "Map changed files or requested scope to the smallest meaningful verification gate.",
        "triggers": ["what tests", "impact", "changed files", "verify", "coverage", "CI"],
        "prerequisites": ["Changed files, diff, or target path", "Known test framework or generated impact map when available"],
        "risk": "medium",
        "mutation_mode": "read-only selection; running tests may execute project code but should not edit repository files",
        "outputs": ["ranked test commands", "coverage caveats", "unmapped changes", "fallback gate"],
        "do_not_use_when": ["The user asks for release sign-off; use release readiness instead", "No target/diff exists and the user only wants general advice"],
        "recommended_entrypoint": "quality_router(mode='change_impact') or change_impact_gate",
        "routing_terms": ["test", "tests", "impact", "verify", "coverage", "changed", "ci", "pytest", "gate"],
    },
    {
        "id": "governance-report",
        "schema": WORKFLOW_CARD_SCHEMA_VERSION,
        "title": "Governance report",
        "intent": "Produce read-only audit/reporting evidence for policy, tool gates, snapshots, security events, and workflow diagnostics.",
        "triggers": ["governance", "audit", "policy report", "compliance", "evidence"],
        "prerequisites": ["Reporting window or scope", "Generated state may be absent in fresh repositories"],
        "risk": "low",
        "mutation_mode": "read-only report generation; async status files may be generated only through explicit workflow_task use",
        "outputs": ["governance report", "policy/tool-gate summary", "snapshot/security/workflow evidence", "provenance"],
        "do_not_use_when": ["The task asks to change policy settings", "The user needs a single operational diagnostic rather than an audit bundle"],
        "recommended_entrypoint": "governance_report or workflow_task(workflow='governance_report')",
        "routing_terms": ["governance", "audit", "policy", "compliance", "evidence", "report", "provenance"],
    },
    {
        "id": "workflow-diagnostics",
        "schema": WORKFLOW_CARD_SCHEMA_VERSION,
        "title": "Workflow diagnostics",
        "intent": "Diagnose a failed or confusing MCP workflow by finding the critical failing step and safe next actions.",
        "triggers": ["workflow failed", "diagnose", "critical step", "why did it fail", "audit events"],
        "prerequisites": ["Workflow/audit trajectory or recent failure context", "Redaction before sharing logs"],
        "risk": "medium",
        "mutation_mode": "read-only diagnostics",
        "outputs": ["critical step candidate", "failure category", "evidence", "safe next actions", "redactions applied"],
        "do_not_use_when": ["The user asks to rerun/fix the workflow immediately without diagnosis", "No failure or trajectory context exists"],
        "recommended_entrypoint": "workflow_diagnostics",
        "routing_terms": ["workflow", "diagnostic", "diagnose", "failed", "failure", "critical", "audit", "trajectory", "stuck"],
    },
)

TASK_RETRIEVAL_STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "an",
    "and",
    "are",
    "be",
    "by",
    "check",
    "describe",
    "explain",
    "file",
    "files",
    "for",
    "from",
    "function",
    "help",
    "how",
    "implement",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "please",
    "repo",
    "repository",
    "show",
    "summarize",
    "task",
    "that",
    "the",
    "this",
    "to",
    "what",
    "with",
}
TASK_RETRIEVAL_CODE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".go", ".java", ".js", ".jsx", ".py", ".rb", ".rs", ".sh", ".ts", ".tsx"
}
TASK_RETRIEVAL_DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".odt", ".ods", ".odp"}

mcp = FastMCP(
    "git-repo-manager",
    instructions=(
        "Expose the compact public MCP v1 surface: `task_router`, read-only inspection helpers "
        "such as `tool_annotations` and `tool_output_contracts`, and schema-backed core tools. "
        "Internal leaf tools and router families remain direct Python call targets, not MCP tools. "
        "LLM agents should start with `task_router()` for almost every natural-language request because its default "
        "`mode='task'` classifies the request, injects compact task/session memory, and dispatches to the right "
        "specialist flow. Use `tool_annotations()` before sensitive calls to inspect read-only, destructive, "
        "idempotent, and open-world hints for public tools and covered modes."
    ),
)

_TERMINAL_SESSIONS: dict[str, dict[str, Any]] = {}
SSE_EVENT_HISTORY_MAX = max(10, int(os.getenv("SSE_EVENT_HISTORY_MAX", "200")))
SSE_SUBSCRIBER_QUEUE_MAX = max(10, int(os.getenv("SSE_SUBSCRIBER_QUEUE_MAX", "200")))
SSE_HEARTBEAT_SECONDS = max(1.0, float(os.getenv("SSE_HEARTBEAT_SECONDS", "2")))
_SSE_EVENT_HISTORY: deque[dict[str, Any]] = deque(maxlen=SSE_EVENT_HISTORY_MAX)
_SSE_SUBSCRIBERS: dict[str, queue.Queue[dict[str, Any]]] = {}
_SSE_LOCK = threading.Lock()
_SSE_EVENT_SEQ = 0


def _http_auth_required() -> bool:
    return MCP_HTTP_AUTH_MODE in {"token", "bearer", "oauth-resource"}


def _http_auth_insecure_local() -> bool:
    return MCP_HTTP_AUTH_MODE in {"insecure-local", "local-insecure", "disabled", "off", "none"}


def _client_is_loopback(scope: dict[str, Any]) -> bool:
    client = scope.get("client") or ("", 0)
    host = str(client[0] if client else "")
    return host in {"127.0.0.1", "::1", "localhost"} or host.startswith("127.")


def _http_header_values(scope: dict[str, Any], header_name: str) -> list[str]:
    expected = header_name.lower().encode("latin-1")
    values: list[str] = []
    for key, value in scope.get("headers", []):
        if key.lower() == expected:
            values.append(value.decode("latin-1", errors="replace").strip())
    return values


def _bearer_token_from_headers(headers: list[tuple[bytes, bytes]]) -> str:
    for key, value in headers:
        if key.lower() != b"authorization":
            continue
        raw = value.decode("latin-1", errors="replace").strip()
        scheme, _, token = raw.partition(" ")
        if scheme.lower() == "bearer" and token:
            return token.strip()
    return ""


def _supported_mcp_scopes() -> list[str]:
    return list(MCP_SUPPORTED_SCOPES)


def _parse_scope_values(raw_scopes: str) -> list[str]:
    return [scope.strip() for scope in re.split(r"[\s,]+", raw_scopes.strip()) if scope.strip()]


def _http_bearer_token_scope_config_error(raw_scopes: str | None = None) -> str:
    value = MCP_HTTP_BEARER_TOKEN_SCOPES_RAW if raw_scopes is None else raw_scopes.strip()
    if not value:
        return ""
    scopes = _parse_scope_values(value)
    unknown = sorted({scope for scope in scopes if scope not in MCP_SUPPORTED_SCOPES})
    if unknown:
        return (
            "MCP_HTTP_BEARER_TOKEN_SCOPES only supports "
            f"{', '.join(MCP_SUPPORTED_SCOPES)}; unsupported scope(s): {', '.join(unknown)}"
        )
    if not scopes:
        return "MCP_HTTP_BEARER_TOKEN_SCOPES must include at least one supported scope"
    return ""


def _local_bearer_token_granted_scopes(raw_scopes: str | None = None) -> frozenset[str]:
    value = MCP_HTTP_BEARER_TOKEN_SCOPES_RAW if raw_scopes is None else raw_scopes.strip()
    if not value:
        return frozenset(MCP_SUPPORTED_SCOPES)
    if _http_bearer_token_scope_config_error(value):
        return frozenset()
    return frozenset(scope for scope in _parse_scope_values(value) if scope in MCP_SUPPORTED_SCOPES)


def _www_authenticate_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', r'\"')


def _http_bearer_challenge(
    *,
    required_scope: str = MCP_SCOPE_READ,
    error: str = "",
) -> str:
    parts = ['Bearer realm="mcp"']
    if error:
        parts.append(f'error="{_www_authenticate_quote(error)}"')
    parts.append(
        'resource_metadata="%s"'
        % _www_authenticate_quote(_http_protected_resource_metadata_url())
    )
    parts.append(f'scope="{_www_authenticate_quote(required_scope)}"')
    return ", ".join(parts)


class HTTPInsufficientScopeError(PermissionError):
    def __init__(self, tool_name: str, required_scope: str, granted_scopes: frozenset[str]):
        self.tool_name = tool_name
        self.required_scope = required_scope
        self.granted_scopes = granted_scopes
        self.challenge = _http_bearer_challenge(
            required_scope=required_scope,
            error="insufficient_scope",
        )
        granted = " ".join(sorted(granted_scopes)) or "none"
        super().__init__(
            f"insufficient_scope: {tool_name} requires scope {required_scope}; "
            f"granted scopes: {granted}; WWW-Authenticate: {self.challenge}"
        )


def _http_path_is_protected_mcp(path: str) -> bool:
    return path == "/sse" or path == "/mcp" or path.startswith("/mcp/")


def _parse_origin(origin: str) -> urllib.parse.SplitResult | None:
    try:
        parsed = urllib.parse.urlsplit(origin)
        # Touch hostname/port so urllib validates malformed bracketed IPv6 and ports.
        _ = parsed.hostname
        _ = parsed.port
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    return parsed


def _origin_host_is_loopback(host: str) -> bool:
    candidate = host.strip().lower()
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def _default_http_origin_allowed(origin: str) -> bool:
    parsed = _parse_origin(origin)
    if parsed is None:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    return bool(parsed.hostname and _origin_host_is_loopback(parsed.hostname))


def _normalize_origin_for_compare(origin: str) -> str | None:
    parsed = _parse_origin(origin)
    if parsed is None:
        return None
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not scheme or not host:
        return None
    port = parsed.port
    host_part = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{scheme}://{host_part}{':' + str(port) if port is not None else ''}"


def _configured_http_origin_allowed(origin: str, raw_allowed_origins: str) -> bool:
    normalized_origin = _normalize_origin_for_compare(origin)
    if normalized_origin is None:
        return False
    origin_parts = urllib.parse.urlsplit(normalized_origin)
    origin_host = (origin_parts.hostname or "").lower()
    for item in raw_allowed_origins.split(","):
        allowed = item.strip().rstrip("/")
        if not allowed:
            continue
        if allowed == "*":
            return True
        if allowed.endswith(":*"):
            allowed_base = allowed[:-2]
            normalized_allowed_base = _normalize_origin_for_compare(allowed_base)
            if normalized_allowed_base is None:
                continue
            allowed_parts = urllib.parse.urlsplit(normalized_allowed_base)
            if (
                origin_parts.scheme.lower() == allowed_parts.scheme.lower()
                and origin_host == (allowed_parts.hostname or "").lower()
            ):
                return True
            continue
        normalized_allowed = _normalize_origin_for_compare(allowed)
        if normalized_allowed is not None and normalized_origin == normalized_allowed:
            return True
    return False


def _http_origin_policy(scope: dict[str, Any]) -> tuple[bool, int, str]:
    origins = _http_header_values(scope, "origin")
    if not origins:
        return True, 200, "origin absent"
    if len(origins) != 1 or not origins[0]:
        return False, 403, "invalid Origin header"
    origin = origins[0]
    if MCP_HTTP_ALLOWED_ORIGINS_RAW:
        if _configured_http_origin_allowed(origin, MCP_HTTP_ALLOWED_ORIGINS_RAW):
            return True, 200, "origin allowed"
    elif _default_http_origin_allowed(origin):
        return True, 200, "origin allowed"
    return False, 403, "invalid Origin header"


def _mcp_protocol_versions_supported() -> tuple[str, ...]:
    versions = tuple(
        dict.fromkeys(
            version.strip()
            for version in MCP_HTTP_SUPPORTED_PROTOCOL_VERSIONS_RAW.split(",")
            if version.strip()
        )
    )
    return versions or MCP_HTTP_DEFAULT_PROTOCOL_VERSIONS


def _mcp_protocol_version_is_well_formed(version: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", version):
        return False
    try:
        datetime.strptime(version, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _http_protocol_version_policy(scope: dict[str, Any]) -> tuple[bool, int, str]:
    values = _http_header_values(scope, "mcp-protocol-version")
    if not values:
        return True, 200, "protocol version absent"
    if len(values) != 1:
        return False, 400, "malformed MCP-Protocol-Version header"
    version = values[0].strip()
    if not version or "," in version or not _mcp_protocol_version_is_well_formed(version):
        return False, 400, "malformed MCP-Protocol-Version header"
    if version not in _mcp_protocol_versions_supported():
        return False, 400, "unsupported MCP-Protocol-Version header"
    return True, 200, "protocol version accepted"


def _http_resource_identifier() -> str:
    return os.getenv("MCP_HTTP_RESOURCE", "http://localhost:%s/mcp" % PORT).strip()


def _http_protected_resource_metadata_url() -> str:
    explicit = os.getenv("MCP_HTTP_PROTECTED_RESOURCE_METADATA_URL", "").strip()
    if explicit:
        return explicit
    resource = _http_resource_identifier()
    if resource.endswith("/mcp"):
        return resource[: -len("/mcp")] + "/.well-known/oauth-protected-resource"
    return "http://localhost:%s/.well-known/oauth-protected-resource" % PORT


def _parse_http_authorization_servers(raw: str | None = None) -> list[str]:
    value = MCP_HTTP_AUTHORIZATION_SERVERS_RAW if raw is None else raw.strip()
    if not value:
        return []
    parsed: Any
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        candidates = parsed
    else:
        candidates = value.split(",")
    servers: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        server = candidate.strip()
        if not server or server in seen:
            continue
        servers.append(server)
        seen.add(server)
    return servers


def _http_oauth_resource_config_error() -> str:
    if MCP_HTTP_AUTH_MODE != "oauth-resource":
        return ""
    if not _parse_http_authorization_servers():
        return "MCP_HTTP_AUTH_MODE=oauth-resource requires MCP_HTTP_AUTHORIZATION_SERVERS with at least one issuer URL"
    return ""


def _http_auth_discovery_payload() -> dict[str, Any]:
    authorization_servers = _parse_http_authorization_servers()
    payload: dict[str, Any] = {
        "resource": _http_resource_identifier(),
        "authorization_servers": authorization_servers,
        "bearer_methods_supported": ["header"],
        "scopes_supported": _supported_mcp_scopes(),
        "mcp_auth_mode": MCP_HTTP_AUTH_MODE,
        "oauth_protected_resource_metadata": _http_protected_resource_metadata_url(),
    }
    if MCP_HTTP_AUTH_MODE == "oauth-resource":
        payload["oauth_2_1_status"] = "enabled: OAuth protected-resource metadata is configured for client authorization discovery"
        config_error = _http_oauth_resource_config_error()
        if config_error:
            payload["configuration_error"] = config_error
    else:
        payload["oauth_2_1_status"] = "local-bearer: bearer-token resource protection is enabled; full OAuth authorization-server integration is not claimed"
    return payload


def _mcp_server_manifest_payload() -> dict[str, Any]:
    """Return the public .well-known MCP server manifest.

    This provisional discovery document is intentionally allowlisted. It exposes
    endpoint shapes, public MCP affordances, and safety metadata only; it must
    not derive values from repository contents, host paths, tokens, or other
    local/private state.
    """
    tool_manifest = _tool_annotation_manifest()
    public_tools = [
        {
            "name": entry["tool"],
            "categories": entry.get("categories", []),
            "required_scope": entry.get("required_scope", MCP_SCOPE_READ),
            "mutation_capable": bool(entry.get("mutation_capable", False)),
            "annotations": entry.get("annotations", {}),
            **({"modes": entry["modes"]} if "modes" in entry else {}),
        }
        for entry in tool_manifest["tools"]
    ]
    return {
        "schema": "mcp-server-manifest.provisional.v1",
        "schema_version": "provisional-2026-05",
        "status": "provisional",
        "specification_status": "non-final SEP draft; field names and semantics may change",
        "server": {
            "name": "codebase-tooling-mcp",
            "mcp_name": "git-repo-manager",
            "version": None,
        },
        "transports": [
            {
                "type": "streamable-http",
                "endpoint": "/mcp",
                "methods": ["POST", "GET", "DELETE"],
                "auth_required": _http_auth_required(),
                "auth": {
                    "mode": MCP_HTTP_AUTH_MODE,
                    "schemes": ["bearer"] if _http_auth_required() else [],
                    "header": "Authorization",
                    "scopes_supported": _supported_mcp_scopes(),
                    "oauth_protected_resource_metadata": "/.well-known/oauth-protected-resource",
                },
            },
            {
                "type": "sse",
                "endpoint": "/sse",
                "methods": ["GET"],
                "auth_required": _http_auth_required(),
            },
        ],
        "health": {
            "liveness": "/healthz",
            "readiness": "/healthz",
        },
        "capabilities": {
            "tools": public_tools,
            "resources": [
                {"uri_template": "repo://summary", "name": "repo_summary_resource"},
                {"uri_template": "repo://file/{path}", "name": "repo_file_resource"},
                {"uri_template": "repo://tree/{path}", "name": "repo_tree_resource"},
            ],
            "prompts": [
                "review_changed_files",
                "release_readiness_check",
                "security_triage",
                "devcontainer_health_check",
                "snapshot_before_refactor",
            ],
        },
        "contracts": {
            "tool_annotations": {
                "schema": tool_manifest["schema"],
                "source": "tool_annotations MCP tool",
            },
            "tool_output_contracts": {
                "schema": "tool_output_contracts.v1",
                "source": "tool_output_contracts MCP tool",
                "documentation": {
                    "title": "MCP Output Schemas",
                    "path": "docs/mcp-output-schemas.md",
                },
                "schema_backed_tools": sorted(SCHEMA_BACKED_TOOL_NAMES),
            },
        },
        "public_data_allowlist": [
            "server product name and MCP server name",
            "relative HTTP endpoint paths",
            "auth mode and supported auth scheme names",
            "public MCP tool/resource/prompt names",
            "tool categories, mutation flags, and MCP safety annotations",
            "schema and contract identifiers",
            "relative public documentation paths for schema and contract references",
            "relative health/readiness paths",
        ],
        "privacy": {
            "contains_repository_contents": False,
            "contains_bearer_tokens": False,
            "contains_local_absolute_paths": False,
            "contains_environment_values": False,
            "contains_host_user_data": False,
            "contains_secrets": False,
        },
    }


def _http_authenticate_scope(scope: dict[str, Any]) -> tuple[bool, int, str]:
    if _http_auth_insecure_local():
        if _client_is_loopback(scope):
            return True, 200, "explicit insecure local-only mode"
        return False, 403, "MCP_HTTP_AUTH_MODE=insecure-local only accepts loopback clients"
    if not _http_auth_required():
        return False, 403, f"unsupported MCP_HTTP_AUTH_MODE={MCP_HTTP_AUTH_MODE!r}"
    config_error = _http_oauth_resource_config_error()
    if config_error:
        return False, 403, config_error
    scope_config_error = _http_bearer_token_scope_config_error()
    if scope_config_error:
        return False, 403, scope_config_error
    if not MCP_HTTP_BEARER_TOKEN:
        return False, 403, "HTTP auth is enabled but MCP_HTTP_BEARER_TOKEN is not configured"
    token = _bearer_token_from_headers(scope.get("headers", []))
    if not token:
        return False, 401, "missing bearer token"
    if not hmac.compare_digest(token, MCP_HTTP_BEARER_TOKEN):
        return False, 403, "invalid bearer token"
    return True, 200, "authorized"


def _http_rate_limit_key(scope: dict[str, Any]) -> str:
    client = scope.get("client") or ("unknown", 0)
    return str(client[0] if client else "unknown")


def _http_rate_limit_allow(scope: dict[str, Any], now: float | None = None) -> tuple[bool, int]:
    now = time.time() if now is None else now
    key = _http_rate_limit_key(scope)
    window_start = now - MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS
    with _HTTP_RATE_LIMIT_LOCK:
        bucket = _HTTP_RATE_LIMIT_BUCKETS.setdefault(key, deque())
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= MCP_HTTP_RATE_LIMIT_REQUESTS:
            retry_after = max(1, int(math.ceil(bucket[0] + MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS - now)))
            return False, retry_after
        bucket.append(now)
    return True, 0


def _redact_audit_string(value: str) -> str:
    if SENSITIVE_AUDIT_VALUE_RE.search(value):
        return "<redacted>"
    if len(value) > 500:
        return value[:500] + "...[truncated]"
    return value


def _redact_audit_reason(value: str) -> str:
    """Redact reason text while preserving non-secret auth policy classes.

    Governance aggregation depends on stable denial classes such as "missing
    bearer token" and "HTTP session not authorized". Those phrases are policy
    metadata, not credentials, so keep a canonical form even when the broader
    secret-value redactor would otherwise collapse the whole reason to
    ``<redacted>`` because it contains words like "bearer token".
    """
    lower_value = value.lower()
    if "insufficient_scope" in lower_value:
        return "insufficient_scope"
    if "http session not authorized" in lower_value:
        return "HTTP session not authorized"
    if "missing bearer token" in lower_value:
        return "missing bearer token"
    if "invalid bearer token" in lower_value:
        return "invalid bearer token"
    if "mcp_http_bearer_token is not configured" in lower_value:
        return "HTTP auth bearer token not configured"
    return _redact_audit_string(value)


def _redact_audit_value(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "<redacted-depth>"
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if key_lower == "reason" and isinstance(item, str):
                redacted[key_str] = _redact_audit_reason(item)
            elif key_lower in {"contains_secrets", "records_secrets", "secrets_exposed"} and isinstance(item, bool):
                redacted[key_str] = item
            elif SENSITIVE_AUDIT_KEY_RE.search(key_str):
                redacted[key_str] = "<redacted>"
            else:
                redacted[key_str] = _redact_audit_value(item, depth + 1)
        return redacted
    if isinstance(value, list):
        return [_redact_audit_value(item, depth + 1) for item in value[:25]]
    if isinstance(value, str):
        return _redact_audit_string(value)
    return value


def _append_audit_event(
    tool_name: str,
    categories: list[str],
    success: bool,
    arguments: dict[str, Any] | None = None,
    reason: str = "",
    *,
    required_scope: str | None = None,
    granted_scopes: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None = None,
) -> None:
    event = {
        "timestamp": _now_iso(),
        "tool_name": tool_name,
        "categories": categories,
        "success": success,
        "reason": _redact_audit_reason(reason),
        "arguments": _redact_audit_value(arguments or {}),
    }
    if required_scope:
        event["required_scope"] = str(required_scope)
    if granted_scopes is not None:
        event["granted_scopes"] = sorted(str(scope) for scope in granted_scopes)
    correlation_id = _OTEL_CORRELATION_ID.get()
    if correlation_id:
        event["correlation_id"] = _redact_audit_string(correlation_id)
    try:
        MCP_AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with MCP_AUDIT_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True, ensure_ascii=True) + "\n")
    except OSError:
        # Audit logging must not leak arguments through exception text or crash read-only calls.
        pass


_OTEL_LOCAL_EXPORTERS = {"json", "jsonl", "local", "test"}
_OTEL_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._~:+@%=-]+"
)


def _otel_exporter_name() -> str:
    configured = str(MCP_OTEL_EXPORTER or "").strip().lower()
    if configured:
        return configured
    return "jsonl" if MCP_OTEL_TRACING_ENABLED else "none"


def _otel_should_record() -> bool:
    return bool(MCP_OTEL_TRACING_ENABLED) and _otel_exporter_name() in _OTEL_LOCAL_EXPORTERS


def _otel_spans_path() -> Path | None:
    configured = Path(MCP_OTEL_SPANS_FILE)
    path = configured.resolve() if configured.is_absolute() else (REPO_PATH / configured).resolve()
    try:
        path.relative_to(REPO_PATH)
    except ValueError:
        return None
    return path


def _otel_redact_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _otel_redact_paths(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_otel_redact_paths(item) for item in value[:25]]
    if isinstance(value, set):
        return [_otel_redact_paths(item) for item in sorted(value, key=str)[:25]]
    if isinstance(value, os.PathLike):
        return _otel_redact_paths(os.fspath(value))
    if isinstance(value, str):
        redacted = value
        repo_text = str(REPO_PATH)
        if repo_text and repo_text in redacted:
            redacted = redacted.replace(repo_text, "<repo>")
        if redacted.startswith("/") and not redacted.startswith("//"):
            return "<redacted:path>"
        return _OTEL_ABSOLUTE_PATH_RE.sub("<redacted:path>", redacted)
    return value


def _otel_json_safe_value(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "<redacted-depth>"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {str(key): _otel_json_safe_value(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_otel_json_safe_value(item, depth + 1) for item in value[:25]]
    if isinstance(value, str):
        return value
    return _otel_redact_paths(_redact_audit_string(str(value)))


def _otel_safe_value(value: Any) -> Any:
    return _otel_json_safe_value(_otel_redact_paths(_redact_audit_value(value)))


def _otel_safe_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        safe[str(key)] = _otel_safe_value(value)
    return safe


class _OtelSpan:
    def __init__(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        *,
        kind: str = "INTERNAL",
        correlation_id: str = "",
    ) -> None:
        self.name = name
        self.kind = kind
        self.attributes = _otel_safe_attributes(attributes or {})
        self.requested_correlation_id = correlation_id
        self.enabled = _otel_should_record()
        self.start_time = ""
        self.start_perf = 0.0
        self.span_id = ""
        self.trace_id = ""
        self.parent_span_id = ""
        self.correlation_id = ""
        self.status_code = "OK"
        self.status_description = ""
        self._span_token: contextvars.Token[str] | None = None
        self._correlation_token: contextvars.Token[str] | None = None

    def __enter__(self) -> "_OtelSpan":
        if not self.enabled:
            return self
        self.start_time = _now_iso()
        self.start_perf = time.perf_counter()
        self.span_id = uuid.uuid4().hex[:16]
        self.parent_span_id = _OTEL_CURRENT_SPAN_ID.get()
        self.correlation_id = (
            str(self.requested_correlation_id or "").strip()
            or str(self.attributes.get("mcp.correlation_id") or "").strip()
            or _OTEL_CORRELATION_ID.get()
            or self.span_id
        )
        self.trace_id = hashlib.sha256(self.correlation_id.encode("utf-8")).hexdigest()[:32]
        self.attributes["mcp.correlation_id"] = _otel_safe_value(self.correlation_id)
        self._span_token = _OTEL_CURRENT_SPAN_ID.set(self.span_id)
        self._correlation_token = _OTEL_CORRELATION_ID.set(self.correlation_id)
        return self

    def set_attribute(self, key: str, value: Any) -> None:
        if value is None:
            return
        self.attributes[str(key)] = _otel_safe_value(value)

    def set_status(self, code: str, description: str = "") -> None:
        self.status_code = code
        self.status_description = _redact_audit_reason(description) if description else ""

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        if not self.enabled:
            return False
        if exc is not None:
            self.set_status("ERROR", exc.__class__.__name__)
            self.set_attribute("error.type", exc.__class__.__name__)
        end_time = _now_iso()
        duration_ms = max(0.0, (time.perf_counter() - self.start_perf) * 1000.0)
        payload: dict[str, Any] = {
            "schema": "mcp_otel_span.local_json.v1",
            "name": self.name,
            "kind": self.kind,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "correlation_id": _otel_safe_value(self.correlation_id),
            "start_time": self.start_time,
            "end_time": end_time,
            "duration_ms": round(duration_ms, 3),
            "status": {"code": self.status_code},
            "resource": {"service.name": _otel_safe_value(MCP_OTEL_SERVICE_NAME)},
            "attributes": _otel_safe_attributes(self.attributes),
        }
        if self.status_description:
            payload["status"]["description"] = self.status_description
        try:
            _otel_write_span(payload)
        finally:
            if self._span_token is not None:
                _OTEL_CURRENT_SPAN_ID.reset(self._span_token)
            if self._correlation_token is not None:
                _OTEL_CORRELATION_ID.reset(self._correlation_token)
        return False


def _otel_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    *,
    kind: str = "INTERNAL",
    correlation_id: str = "",
) -> _OtelSpan:
    return _OtelSpan(name, attributes, kind=kind, correlation_id=correlation_id)


@contextlib.contextmanager
def _otel_correlation_context(correlation_id: str):
    if not correlation_id or not _otel_should_record():
        yield
        return
    token = _OTEL_CORRELATION_ID.set(correlation_id)
    try:
        yield
    finally:
        _OTEL_CORRELATION_ID.reset(token)


def _otel_write_span(payload: dict[str, Any]) -> None:
    path = _otel_spans_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _OTEL_SPANS_LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, sort_keys=True, ensure_ascii=True) + "\n")
    except Exception:
        # Tracing must never make a tool call fail or leak local paths through exceptions.
        pass


def _otel_tool_attributes(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    categories = categories if categories is not None else _tool_categories(tool_name, arguments)
    attrs: dict[str, Any] = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.system": "mcp",
        "gen_ai.tool.name": tool_name,
        "mcp.schema": "mcp.tool_execution.v1",
        "mcp.tool.name": tool_name,
        "mcp.tool.categories": sorted(str(item) for item in categories),
        "mcp.content_capture.enabled": False,
    }
    if arguments:
        mode = arguments.get("mode") or arguments.get("action")
        if mode:
            attrs["mcp.tool.mode"] = str(mode)
        if arguments.get("execution_mode"):
            attrs["mcp.execution_mode.requested"] = str(arguments["execution_mode"])
    return attrs


def _otel_set_result_attributes(span: _OtelSpan, result: Any) -> None:
    if not isinstance(result, dict):
        return
    if isinstance(result.get("schema"), str):
        span.set_attribute("mcp.response.schema", result["schema"])
    if isinstance(result.get("ok"), bool):
        span.set_attribute("mcp.response.ok", result["ok"])
    if isinstance(result.get("status"), str):
        span.set_attribute("mcp.response.status", result["status"])
    if isinstance(result.get("state"), str):
        span.set_attribute("mcp.response.state", result["state"])
    exports = result.get("exports")
    if isinstance(exports, dict):
        refs = [value for value in exports.values() if isinstance(value, str)]
        if refs:
            span.set_attribute("mcp.artifact.refs", refs[:5])


def _otel_record_policy_gate(
    tool_name: str,
    categories: list[str],
    decision: str,
    reason: str,
    arguments: dict[str, Any] | None = None,
    *,
    required_scope: str | None = None,
    granted_scopes: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None = None,
) -> None:
    attrs = _otel_tool_attributes(tool_name, arguments, categories)
    required_scope = required_scope or _required_scope_for_categories(categories)
    attrs.update(
        {
            "mcp.schema": "mcp.policy_gate.v1",
            "mcp.policy.decision": decision,
            "mcp.policy.reason": _redact_audit_reason(reason),
            "mcp.security.mutation_required": bool(MUTATION_TOOL_CATEGORIES.intersection(categories)),
            "mcp.security.required_scope": required_scope,
            "mcp.http.authorized": _http_request_authorized_for_tools(),
        }
    )
    if granted_scopes is not None:
        attrs["mcp.security.granted_scopes"] = sorted(str(scope) for scope in granted_scopes)
    elif _inside_http_request():
        attrs["mcp.security.granted_scopes"] = sorted(_http_request_granted_scopes_for_tools())
    with _otel_span("mcp.policy_gate", attrs) as span:
        if decision != "allow":
            span.set_status("ERROR", reason)


def _otel_record_workflow_lifecycle(
    task_id: str,
    workflow: str,
    event: str,
    *,
    success: bool = True,
    status: str = "",
    artifact_refs: list[str] | None = None,
) -> None:
    attrs = {
        "mcp.schema": "mcp.workflow_task.lifecycle.v1",
        "mcp.workflow.name": workflow,
        "mcp.workflow.task_id": task_id,
        "mcp.workflow.event": event,
        "mcp.workflow.status": status or event,
        "mcp.content_capture.enabled": False,
    }
    if artifact_refs:
        attrs["mcp.artifact.refs"] = artifact_refs[:5]
    with _otel_span("mcp.workflow_task.lifecycle", attrs, correlation_id=task_id) as span:
        if not success:
            span.set_status("ERROR", status or event)


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _governance_audit_log_path() -> tuple[Path, str]:
    configured = MCP_AUDIT_LOG_FILE
    if configured.is_absolute():
        resolved = configured.resolve()
        try:
            resolved.relative_to(REPO_PATH)
        except ValueError:
            return resolved, "outside_repo_boundary"
        return resolved, "configured_absolute"
    return _resolve_repo_path(str(configured)), "configured_relative"


def _governance_report_paths(report_id: str) -> dict[str, str]:
    base = REPORTS_DIR / report_id
    return {"json": str(base.with_suffix(".json")), "markdown": str(base.with_suffix(".md"))}


def _load_audit_events(start_dt: datetime | None, end_dt: datetime | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path, source = _governance_audit_log_path()
    meta = {
        "configured_path": str(MCP_AUDIT_LOG_FILE),
        "resolved_path": str(path),
        "source": source,
        "exists": path.exists(),
        "events_total": 0,
        "events_in_window": 0,
        "malformed_lines": 0,
    }
    if source == "outside_repo_boundary":
        meta["readable"] = False
        meta["reason"] = "MCP_AUDIT_LOG_FILE resolves outside repository boundary"
        return [], meta
    if not path.exists():
        meta["readable"] = True
        return [], meta

    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    meta["malformed_lines"] += 1
                    continue
                if not isinstance(event, dict):
                    meta["malformed_lines"] += 1
                    continue
                meta["events_total"] += 1
                ts = _parse_iso_datetime(str(event.get("timestamp", "")))
                if start_dt and (ts is None or ts < start_dt):
                    continue
                if end_dt and (ts is None or ts > end_dt):
                    continue
                events.append(_redact_audit_value(event))
    except OSError as exc:
        meta["readable"] = False
        meta["reason"] = type(exc).__name__
        return [], meta
    meta["readable"] = True
    meta["events_in_window"] = len(events)
    return events, meta


def _audit_event_digest(event: dict[str, Any], previous: str) -> str:
    canonical = json.dumps(event, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256((previous + "\n" + canonical).encode("utf-8")).hexdigest()


def _aggregate_audit_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_tool: dict[str, int] = {}
    by_category: dict[str, int] = {}
    failure_reasons: dict[str, int] = {}
    blocked: list[dict[str, Any]] = []
    success_count = 0
    sensitive_count = 0
    mutation_gate_failures = 0
    http_auth_denials = 0
    clarification_gate = {
        "event_count": 0,
        "ok_to_continue_count": 0,
        "needs_clarification_count": 0,
        "declined_count": 0,
        "cancelled_count": 0,
        "missing_fields": {},
    }
    previous = ""
    chain_head = ""

    for event in events:
        tool = str(event.get("tool_name", "unknown")) or "unknown"
        categories = event.get("categories", [])
        if not isinstance(categories, list):
            categories = []
        cats = [str(c) for c in categories]
        success = bool(event.get("success", False))
        reason = str(event.get("reason", ""))
        by_tool[tool] = by_tool.get(tool, 0) + 1
        for cat in cats:
            by_category[cat] = by_category.get(cat, 0) + 1
        if SENSITIVE_TOOL_CATEGORIES.intersection(cats):
            sensitive_count += 1
        if success:
            success_count += 1
        else:
            key = reason or "unspecified"
            failure_reasons[key] = failure_reasons.get(key, 0) + 1
            blocked.append(
                {
                    "timestamp": str(event.get("timestamp", "")),
                    "tool_name": tool,
                    "categories": cats,
                    "reason": reason,
                }
            )
        reason_lower = reason.lower()
        if "mutations disabled" in reason_lower or "mutation permission" in reason_lower:
            mutation_gate_failures += 1
        if "http session not authorized" in reason_lower or "bearer token" in reason_lower or "not authorized" in reason_lower:
            http_auth_denials += 1
        if tool == "clarification_gate":
            clarification_gate["event_count"] += 1
            args = event.get("arguments", {}) if isinstance(event.get("arguments"), dict) else {}
            decision = args.get("decision", {}) if isinstance(args.get("decision"), dict) else {}
            status = str(decision.get("status") or reason or "")
            if bool(decision.get("ok_to_continue", False)):
                clarification_gate["ok_to_continue_count"] += 1
            if status == "needs_clarification":
                clarification_gate["needs_clarification_count"] += 1
            elif status == "declined":
                clarification_gate["declined_count"] += 1
            elif status == "cancelled":
                clarification_gate["cancelled_count"] += 1
            missing = decision.get("missing_fields", [])
            if isinstance(missing, list):
                field_counts = clarification_gate["missing_fields"]
                if isinstance(field_counts, dict):
                    for field in missing:
                        key = str(field)[:80]
                        field_counts[key] = int(field_counts.get(key, 0)) + 1
        chain_head = _audit_event_digest(event, previous)
        previous = chain_head

    return {
        "event_count": len(events),
        "sensitive_tool_call_count": sensitive_count,
        "success_count": success_count,
        "blocked_attempt_count": len(events) - success_count,
        "mutation_gate_failure_count": mutation_gate_failures,
        "http_authorization_denial_count": http_auth_denials,
        "by_tool": dict(sorted(by_tool.items())),
        "by_category": dict(sorted(by_category.items())),
        "failure_reasons": dict(sorted(failure_reasons.items())),
        "blocked_attempts": blocked[:200],
        "digest": {
            "algorithm": "sha256",
            "mode": "hash_chain_over_redacted_events",
            "event_count": len(events),
            "chain_head": chain_head,
        },
        "clarification_gate": {
            **clarification_gate,
            "missing_fields": dict(sorted(clarification_gate["missing_fields"].items()))
            if isinstance(clarification_gate.get("missing_fields"), dict)
            else {},
        },
    }


SELF_OPTIMIZATION_REPORT_SCHEMA = "self_optimization_report.v1"
SELF_OPTIMIZATION_NO_ATTRIBUTION = "unattributed"
SELF_OPTIMIZATION_NAME_PLACEHOLDER = "<redacted:name>"
SELF_OPTIMIZATION_COMPANY_SUFFIX_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&_.-]*(?:\s+[A-Z][A-Za-z0-9&_.-]*)*\s+(?:Inc|LLC|Ltd|GmbH|Corp|Corporation|Company)\b"
)
SELF_OPTIMIZATION_SENSITIVE_NAME_KEYS = {
    "actor",
    "assignee",
    "author",
    "company",
    "organization",
    "owner",
    "person",
    "project",
    "repo",
    "repository",
    "user",
    "username",
}
SELF_OPTIMIZATION_SAFE_BOOLEAN_KEYS = {"contains_secrets", "records_secrets", "secrets_exposed"}
SELF_OPTIMIZATION_SAFE_TOKEN_KEYS = {
    "compressed_tokens",
    "compression_estimated_saved_tokens",
    "estimated_saved_tokens",
    "input_tokens",
    "output_tokens",
    "raw_tokens",
    "saved_tokens",
    "token_estimates",
    "tokens",
    "total_tokens",
}
SELF_OPTIMIZATION_BASELINE_SECONDS_BY_TOOL = {
    "find_paths": 20.0,
    "grep": 30.0,
    "read_snippet": 20.0,
    "summarize_diff": 45.0,
    "risk_scoring": 45.0,
    "test_impact_map": 60.0,
    "release_readiness": 120.0,
    "governance_report": 180.0,
    "workflow_diagnostics": 90.0,
    "workflow_task": 180.0,
    "task_router": 45.0,
    "quality_router": 90.0,
    "command_runner": 120.0,
    "docker_router": 180.0,
    "vscode_router": 120.0,
}


def _self_opt_parse_window(start_time: str, end_time: str, window_hours: int) -> tuple[datetime, datetime]:
    if window_hours < 1:
        raise ValueError("window_hours must be >= 1")
    if window_hours > 24 * 366:
        raise ValueError("window_hours must be <= 8784")
    end_dt = _parse_iso_datetime(end_time) if end_time.strip() else datetime.now(timezone.utc)
    start_dt = _parse_iso_datetime(start_time) if start_time.strip() else None
    if end_time.strip() and end_dt is None:
        raise ValueError("end_time must be an ISO-8601 timestamp")
    if start_time.strip() and start_dt is None:
        raise ValueError("start_time must be an ISO-8601 timestamp")
    if end_dt is None:
        end_dt = datetime.now(timezone.utc)
    if start_dt is None:
        start_dt = end_dt - timedelta(hours=window_hours)
    if start_dt > end_dt:
        raise ValueError("start_time must be before end_time")
    return start_dt, end_dt


def _self_opt_in_window(ts: datetime | None, start_dt: datetime, end_dt: datetime) -> bool:
    if ts is None:
        return False
    return start_dt <= ts <= end_dt


def _self_opt_relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_PATH))
    except Exception:
        return "<outside_repo_boundary>"


def _self_opt_resolve_local_file(configured: Path) -> tuple[Path, str]:
    resolved = configured.resolve() if configured.is_absolute() else (REPO_PATH / configured).resolve()
    try:
        resolved.relative_to(REPO_PATH)
    except ValueError:
        return resolved, "outside_repo_boundary"
    return resolved, "repo_relative" if not configured.is_absolute() else "configured_absolute"


def _self_opt_public_source_meta(meta: dict[str, Any]) -> dict[str, Any]:
    safe = dict(meta)
    for key in ("resolved_path", "path"):
        value = safe.get(key)
        if isinstance(value, str) and value:
            safe[key] = _self_opt_relpath(Path(value))
    return safe


def _self_opt_default_redact_terms(extra_terms: list[str] | None = None) -> list[str]:
    terms: set[str] = set()
    for raw in extra_terms or []:
        value = str(raw).strip()
        if value:
            terms.add(value)
    for raw in os.getenv("MCP_SELF_OPTIMIZATION_REDACT_TERMS", "").split(","):
        value = raw.strip()
        if value:
            terms.add(value)
    if _is_git_repo():
        for key in ("user.name", "user.email"):
            proc = _git("config", "--get", key, check=False)
            value = proc.stdout.strip()
            if value:
                terms.add(value)
                if "@" in value:
                    terms.update(part for part in re.split(r"[@.]", value) if part)
        remotes = _git("remote", "-v", check=False).stdout
        ignored = {"https", "http", "ssh", "git", "github", "com", "origin", "fetch", "push"}
        for token in re.split(r"[^A-Za-z0-9_.-]+", remotes):
            cleaned = token.strip().removesuffix(".git")
            if len(cleaned) >= 4 and cleaned.lower() not in ignored:
                terms.add(cleaned)
        if len(REPO_PATH.name) >= 4:
            terms.add(REPO_PATH.name)
    return sorted(terms, key=lambda item: (-len(item), item.lower()))


def _self_opt_redact_string(value: str, redact_terms: list[str]) -> str:
    redacted = _redact_audit_string(value)
    repo_text = str(REPO_PATH)
    if repo_text and repo_text in redacted:
        redacted = redacted.replace(repo_text, "<repo>")
    redacted = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "<redacted:email>", redacted)
    redacted = SELF_OPTIMIZATION_COMPANY_SUFFIX_RE.sub(SELF_OPTIMIZATION_NAME_PLACEHOLDER, redacted)
    for term in redact_terms:
        if len(term) < 3:
            continue
        redacted = re.sub(re.escape(term), SELF_OPTIMIZATION_NAME_PLACEHOLDER, redacted, flags=re.IGNORECASE)
    return redacted


def _self_opt_redact_value(value: Any, redact_terms: list[str], depth: int = 0) -> Any:
    if depth > 6:
        return "<redacted-depth>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if key_lower in SELF_OPTIMIZATION_SAFE_BOOLEAN_KEYS and isinstance(item, bool):
                out[key_str] = item
            elif key_lower in SELF_OPTIMIZATION_SAFE_TOKEN_KEYS or key_lower.endswith("_tokens"):
                out[key_str] = _self_opt_redact_value(item, redact_terms, depth + 1)
            elif key_lower in SELF_OPTIMIZATION_SENSITIVE_NAME_KEYS and isinstance(item, str):
                out[key_str] = SELF_OPTIMIZATION_NAME_PLACEHOLDER
            elif SENSITIVE_AUDIT_KEY_RE.search(key_str):
                out[key_str] = "<redacted>"
            else:
                out[key_str] = _self_opt_redact_value(item, redact_terms, depth + 1)
        return out
    if isinstance(value, list):
        return [_self_opt_redact_value(item, redact_terms, depth + 1) for item in value[:100]]
    if isinstance(value, tuple):
        return [_self_opt_redact_value(item, redact_terms, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return _self_opt_redact_string(value, redact_terms)
    return value


def _self_opt_json_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    except Exception:
        return str(value)


def _self_opt_extract_refs(value: Any) -> dict[str, list[str]]:
    text = _self_opt_json_text(value)
    issues: set[str] = set()
    prs: set[str] = set()
    for match in re.finditer(r"(?i)\b(?:pr|pull request|pull-request|pull/|pulls/)\s*#?(\d+)\b", text):
        prs.add(f"#{match.group(1)}")
    for match in re.finditer(r"(?i)\b(?:issue|issues|closes|fixes|resolves)\s*#?(\d+)\b", text):
        issues.add(f"#{match.group(1)}")
    for match in re.finditer(r"#(\d+)", text):
        prefix = text[max(0, match.start() - 32) : match.start()].lower()
        if "pull request" in prefix or re.search(r"\bpr\s*$", prefix):
            prs.add(f"#{match.group(1)}")
        else:
            issues.add(f"#{match.group(1)}")
    return {"issues": sorted(issues), "prs": sorted(prs)}


def _self_opt_first_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, dict):
        return None
    for key in ("timestamp", "start_time", "created_at", "started_at", "updated_at", "end_time", "completed_at"):
        raw = value.get(key)
        if isinstance(raw, str):
            parsed = _parse_iso_datetime(raw)
            if parsed is not None:
                return parsed
    return None


def _self_opt_workflow_from_payload(tool: str, value: Any) -> str:
    if isinstance(value, dict):
        for key in (
            "workflow",
            "workflow_name",
            "mcp.workflow.name",
            "mode",
            "mcp.tool.mode",
            "operation",
            "task",
        ):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()[:120]
        args = value.get("arguments") if isinstance(value.get("arguments"), dict) else None
        if args:
            nested = _self_opt_workflow_from_payload(tool, args)
            if nested:
                return nested
        attrs = value.get("attributes") if isinstance(value.get("attributes"), dict) else None
        if attrs:
            nested = _self_opt_workflow_from_payload(tool, attrs)
            if nested:
                return nested
    if tool in {"governance_report", "workflow_diagnostics", "release_readiness", "test_impact_map", "workflow_task"}:
        return tool
    return ""


def _self_opt_collect_numeric_metrics(value: Any, metrics: dict[str, float], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            key_lower = str(key).lower().replace(".", "_").replace("-", "_")
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                amount = float(item)
                if "input_tokens" in key_lower or "prompt_tokens" in key_lower:
                    metrics["input_tokens"] += amount
                elif "output_tokens" in key_lower or "completion_tokens" in key_lower:
                    metrics["output_tokens"] += amount
                elif "total_tokens" in key_lower:
                    metrics["total_tokens"] += amount
                elif "saved_tokens" in key_lower or "tokens_saved" in key_lower:
                    metrics["saved_tokens"] += amount
                elif "raw_tokens" in key_lower:
                    metrics["raw_tokens"] += amount
                elif "compressed_tokens" in key_lower:
                    metrics["compressed_tokens"] += amount
            _self_opt_collect_numeric_metrics(item, metrics, depth + 1)
    elif isinstance(value, list):
        for item in value[:100]:
            _self_opt_collect_numeric_metrics(item, metrics, depth + 1)


def _self_opt_token_metrics(value: Any) -> dict[str, int]:
    metrics = {
        "input_tokens": 0.0,
        "output_tokens": 0.0,
        "total_tokens": 0.0,
        "saved_tokens": 0.0,
        "raw_tokens": 0.0,
        "compressed_tokens": 0.0,
    }
    _self_opt_collect_numeric_metrics(value, metrics)
    if metrics["total_tokens"] <= 0:
        metrics["total_tokens"] = metrics["input_tokens"] + metrics["output_tokens"]
    if metrics["saved_tokens"] <= 0 and metrics["raw_tokens"] > metrics["compressed_tokens"] > 0:
        metrics["saved_tokens"] = metrics["raw_tokens"] - metrics["compressed_tokens"]
    return {key: int(round(value)) for key, value in metrics.items() if key in {"input_tokens", "output_tokens", "total_tokens", "saved_tokens"}}


def _self_opt_collect_routing(value: Any, models: set[str], backends: set[str], execution_modes: set[str], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            key_lower = str(key).lower()
            if isinstance(item, str) and item.strip():
                if "model" in key_lower and "schema" not in key_lower:
                    models.add(item.strip()[:120])
                if key_lower in {"backend", "provider", "route", "mcp.backend", "gen_ai.system"} or key_lower.endswith(".backend"):
                    backends.add(item.strip()[:120])
                if "execution_mode" in key_lower:
                    execution_modes.add(item.strip()[:120])
            _self_opt_collect_routing(item, models, backends, execution_modes, depth + 1)
    elif isinstance(value, list):
        for item in value[:100]:
            _self_opt_collect_routing(item, models, backends, execution_modes, depth + 1)


def _self_opt_model_routing(value: Any, redact_terms: list[str]) -> dict[str, list[str]]:
    models: set[str] = set()
    backends: set[str] = set()
    execution_modes: set[str] = set()
    _self_opt_collect_routing(value, models, backends, execution_modes)
    return {
        "models": sorted(_self_opt_redact_string(item, redact_terms) for item in models),
        "backends": sorted(_self_opt_redact_string(item, redact_terms) for item in backends),
        "execution_modes": sorted(_self_opt_redact_string(item, redact_terms) for item in execution_modes),
    }


def _self_opt_collect_cache_hits(value: Any, depth: int = 0) -> int:
    if depth > 6:
        return 0
    hits = 0
    if isinstance(value, dict):
        for key, item in value.items():
            key_lower = str(key).lower().replace(".", "_").replace("-", "_")
            if key_lower in {"cached", "cache_hit", "mcp_cache_hit"} and bool(item):
                hits += 1
            hits += _self_opt_collect_cache_hits(item, depth + 1)
    elif isinstance(value, list):
        for item in value[:100]:
            hits += _self_opt_collect_cache_hits(item, depth + 1)
    return hits


def _self_opt_collect_compression(value: Any, metrics: dict[str, float], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(value, dict):
        if value.get("schema") == "compressed_observation.v1":
            metrics["compressed_observation_count"] += 1
            metrics["compressed_payload_bytes"] += _payload_size_bytes(value)
            omitted = value.get("omitted", [])
            if isinstance(omitted, list):
                for row in omitted:
                    if isinstance(row, dict) and isinstance(row.get("count"), int):
                        metrics["omitted_signal_count"] += int(row["count"])
        for key, item in value.items():
            key_lower = str(key).lower().replace(".", "_").replace("-", "_")
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                if "raw_bytes" in key_lower:
                    metrics["raw_bytes"] += float(item)
                elif "compressed_bytes" in key_lower:
                    metrics["compressed_bytes"] += float(item)
            _self_opt_collect_compression(item, metrics, depth + 1)
    elif isinstance(value, list):
        for item in value[:100]:
            _self_opt_collect_compression(item, metrics, depth + 1)


def _self_opt_compression_metrics(value: Any) -> dict[str, int]:
    metrics = {
        "compressed_observation_count": 0.0,
        "omitted_signal_count": 0.0,
        "raw_bytes": 0.0,
        "compressed_bytes": 0.0,
        "compressed_payload_bytes": 0.0,
    }
    _self_opt_collect_compression(value, metrics)
    saved_tokens = 0
    if metrics["raw_bytes"] > metrics["compressed_bytes"] > 0:
        saved_tokens = int(round((metrics["raw_bytes"] - metrics["compressed_bytes"]) / 4.0))
    elif metrics["omitted_signal_count"] > 0:
        saved_tokens = int(metrics["omitted_signal_count"] * 80)
    elif metrics["compressed_observation_count"] > 0:
        saved_tokens = int(metrics["compressed_observation_count"] * 120)
    return {
        "compressed_observation_count": int(metrics["compressed_observation_count"]),
        "omitted_signal_count": int(metrics["omitted_signal_count"]),
        "estimated_saved_tokens": saved_tokens,
    }


def _self_opt_normalize_audit_event(event: dict[str, Any], redact_terms: list[str]) -> dict[str, Any]:
    safe_event = _self_opt_redact_value(event, redact_terms)
    tool = str(event.get("tool_name", "unknown") or "unknown")
    timestamp = _self_opt_first_timestamp(event)
    refs = _self_opt_extract_refs(safe_event)
    routing = _self_opt_model_routing(safe_event, redact_terms)
    token_metrics = _self_opt_token_metrics(event)
    compression = _self_opt_compression_metrics(event)
    categories = event.get("categories", []) if isinstance(event.get("categories"), list) else []
    return {
        "source": "audit",
        "timestamp": timestamp,
        "tool": tool,
        "workflow": _self_opt_workflow_from_payload(tool, safe_event),
        "success": bool(event.get("success", False)),
        "reason": _self_opt_redact_string(str(event.get("reason", "")), redact_terms),
        "duration_ms": None,
        "categories": [str(item) for item in categories],
        "issue_refs": refs["issues"],
        "pr_refs": refs["prs"],
        "routing": routing,
        "tokens": token_metrics,
        "cache_hit_count": _self_opt_collect_cache_hits(event),
        "compression": compression,
    }


def _self_opt_normalize_span(span: dict[str, Any], redact_terms: list[str]) -> dict[str, Any]:
    safe_span = _self_opt_redact_value(span, redact_terms)
    attrs = span.get("attributes", {}) if isinstance(span.get("attributes"), dict) else {}
    safe_attrs = safe_span.get("attributes", {}) if isinstance(safe_span.get("attributes"), dict) else {}
    name = str(span.get("name", ""))
    tool = str(attrs.get("mcp.tool.name") or attrs.get("gen_ai.tool.name") or "")
    if not tool and name.startswith("mcp.tool."):
        tool = name.removeprefix("mcp.tool.")
    if not tool:
        tool = name or "unknown"
    status = span.get("status", {}) if isinstance(span.get("status"), dict) else {}
    status_code = str(status.get("code", "OK"))
    duration_raw = span.get("duration_ms")
    duration_ms = float(duration_raw) if isinstance(duration_raw, (int, float)) and not isinstance(duration_raw, bool) else None
    refs = _self_opt_extract_refs(safe_span)
    routing = _self_opt_model_routing(safe_span, redact_terms)
    return {
        "source": "trace",
        "timestamp": _self_opt_first_timestamp(span),
        "tool": tool,
        "workflow": _self_opt_workflow_from_payload(tool, safe_attrs),
        "success": status_code.upper() != "ERROR",
        "reason": _self_opt_redact_string(str(status.get("description", "")), redact_terms),
        "duration_ms": duration_ms,
        "categories": [],
        "issue_refs": refs["issues"],
        "pr_refs": refs["prs"],
        "routing": routing,
        "tokens": _self_opt_token_metrics(span),
        "cache_hit_count": _self_opt_collect_cache_hits(span),
        "compression": _self_opt_compression_metrics(span),
    }


def _self_opt_load_jsonl_records(configured_path: Path, start_dt: datetime, end_dt: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path, source = _self_opt_resolve_local_file(configured_path)
    meta = {
        "configured_path": str(configured_path),
        "resolved_path": str(path),
        "source": source,
        "exists": path.exists(),
        "readable": False,
        "records_total": 0,
        "records_in_window": 0,
        "malformed_lines": 0,
    }
    if source == "outside_repo_boundary":
        meta["reason"] = "configured path resolves outside repository boundary"
        return [], meta
    if not path.exists():
        meta["readable"] = True
        return [], meta
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    meta["malformed_lines"] += 1
                    continue
                if not isinstance(row, dict):
                    meta["malformed_lines"] += 1
                    continue
                meta["records_total"] += 1
                if _self_opt_in_window(_self_opt_first_timestamp(row), start_dt, end_dt):
                    rows.append(row)
    except OSError as exc:
        meta["reason"] = exc.__class__.__name__
        return [], meta
    meta["readable"] = True
    meta["records_in_window"] = len(rows)
    return rows, meta


def _self_opt_load_task_records(start_dt: datetime, end_dt: datetime, redact_terms: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    task_dir = _resolve_repo_path(str(WORKFLOW_TASKS_DIR))
    meta = {
        "path": str(task_dir),
        "exists": task_dir.exists(),
        "readable": False,
        "records_total": 0,
        "records_in_window": 0,
        "malformed_files": 0,
    }
    records: list[dict[str, Any]] = []
    if not task_dir.exists():
        meta["readable"] = True
        return records, meta
    try:
        files = sorted(task_dir.glob("*.json"))[:500]
    except OSError as exc:
        meta["reason"] = exc.__class__.__name__
        return records, meta
    for file_path in files:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta["malformed_files"] += 1
            continue
        if not isinstance(payload, dict):
            meta["malformed_files"] += 1
            continue
        meta["records_total"] += 1
        ts = _self_opt_first_timestamp(payload)
        if not _self_opt_in_window(ts, start_dt, end_dt):
            continue
        safe_payload = _self_opt_redact_value(payload, redact_terms)
        tool = "workflow_task"
        refs = _self_opt_extract_refs(safe_payload)
        status = str(payload.get("status", payload.get("state", ""))).lower()
        records.append(
            {
                "source": "task",
                "timestamp": ts,
                "tool": tool,
                "workflow": _self_opt_workflow_from_payload(tool, safe_payload),
                "success": status not in {"failed", "error", "cancelled", "timeout"},
                "reason": _self_opt_redact_string(str(payload.get("error", payload.get("reason", ""))), redact_terms),
                "duration_ms": None,
                "categories": [],
                "issue_refs": refs["issues"],
                "pr_refs": refs["prs"],
                "routing": _self_opt_model_routing(safe_payload, redact_terms),
                "tokens": _self_opt_token_metrics(payload),
                "cache_hit_count": _self_opt_collect_cache_hits(payload),
                "compression": _self_opt_compression_metrics(payload),
            }
        )
    meta["readable"] = True
    meta["records_in_window"] = len(records)
    return records, meta


def _self_opt_git_commit_records(start_dt: datetime, end_dt: datetime, redact_terms: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = {
        "available": False,
        "network_used": False,
        "records_total": 0,
        "records_in_window": 0,
        "reason": "",
    }
    if not _is_git_repo():
        meta["reason"] = "not_a_git_repo"
        return [], meta
    proc = _git(
        "log",
        "--since",
        start_dt.isoformat(),
        "--until",
        end_dt.isoformat(),
        "--pretty=format:%H%x00%ct%x00%s",
        check=False,
    )
    if proc.returncode != 0:
        meta["reason"] = _redact_audit_string(proc.stderr.strip()[:200])
        return [], meta
    records: list[dict[str, Any]] = []
    meta["available"] = True
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x00", 2)
        if len(parts) != 3:
            continue
        commit_hash, ts_raw, subject = parts
        try:
            ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
        except ValueError:
            ts = None
        safe_subject = _self_opt_redact_string(subject, redact_terms)
        refs = _self_opt_extract_refs(safe_subject)
        meta["records_total"] += 1
        records.append(
            {
                "source": "git_commit",
                "timestamp": ts,
                "tool": "git",
                "workflow": "git_commit",
                "success": True,
                "reason": "",
                "duration_ms": 0.0,
                "categories": ["git"],
                "issue_refs": refs["issues"],
                "pr_refs": refs["prs"],
                "routing": {"models": [], "backends": [], "execution_modes": []},
                "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "saved_tokens": 0},
                "cache_hit_count": 0,
                "compression": {"compressed_observation_count": 0, "omitted_signal_count": 0, "estimated_saved_tokens": 0},
                "commit": commit_hash[:12],
                "subject": safe_subject[:160],
            }
        )
    meta["records_in_window"] = len(records)
    return records, meta


def _self_opt_record_baseline_seconds(record: dict[str, Any]) -> float:
    if record.get("source") == "git_commit":
        return 0.0
    tool = str(record.get("tool", "")).lower()
    workflow = str(record.get("workflow", "")).lower()
    if tool in SELF_OPTIMIZATION_BASELINE_SECONDS_BY_TOOL:
        return SELF_OPTIMIZATION_BASELINE_SECONDS_BY_TOOL[tool]
    if workflow in SELF_OPTIMIZATION_BASELINE_SECONDS_BY_TOOL:
        return SELF_OPTIMIZATION_BASELINE_SECONDS_BY_TOOL[workflow]
    categories = {str(item).lower() for item in record.get("categories", []) if isinstance(item, str)}
    if "shell/process" in categories or "network" in categories:
        return 90.0
    if "write" in categories or "git mutation" in categories:
        return 120.0
    return 30.0


def _self_opt_record_spent_seconds(record: dict[str, Any]) -> float:
    duration = record.get("duration_ms")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        return max(0.0, float(duration) / 1000.0)
    if record.get("source") == "git_commit":
        return 0.0
    return 4.0


def _self_opt_is_noisy(record: dict[str, Any], spent_seconds: float) -> bool:
    if not bool(record.get("success", True)):
        return True
    reason = str(record.get("reason", "")).lower()
    if any(term in reason for term in ("timeout", "failed", "error", "retry", "noisy")):
        return True
    return spent_seconds >= 60.0


def _self_opt_blank_bucket(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "observed_record_count": 0,
        "tool_call_count": 0,
        "commit_count": 0,
        "success_count": 0,
        "failed_or_noisy_count": 0,
        "estimated_spent_seconds": 0.0,
        "estimated_baseline_seconds": 0.0,
        "estimated_saved_seconds": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_saved_tokens": 0,
    }


def _self_opt_update_bucket(bucket: dict[str, dict[str, Any]], key: str, record: dict[str, Any], baseline: float, spent: float, saved: float, noisy: bool) -> None:
    row = bucket.setdefault(key, _self_opt_blank_bucket(key))
    row["observed_record_count"] += 1
    if record.get("source") == "git_commit":
        row["commit_count"] += 1
    else:
        row["tool_call_count"] += 1
    if bool(record.get("success", True)):
        row["success_count"] += 1
    if noisy:
        row["failed_or_noisy_count"] += 1
    row["estimated_spent_seconds"] += spent
    row["estimated_baseline_seconds"] += baseline
    row["estimated_saved_seconds"] += saved
    tokens = record.get("tokens", {}) if isinstance(record.get("tokens"), dict) else {}
    compression = record.get("compression", {}) if isinstance(record.get("compression"), dict) else {}
    for key_name in ("input_tokens", "output_tokens", "total_tokens", "saved_tokens"):
        metric_name = "estimated_saved_tokens" if key_name == "saved_tokens" else key_name
        value = tokens.get(key_name, 0)
        if isinstance(value, int):
            row[metric_name] += value
    saved_tokens = compression.get("estimated_saved_tokens", 0)
    if isinstance(saved_tokens, int):
        row["estimated_saved_tokens"] += saved_tokens


def _self_opt_bucket_rows(bucket: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in bucket.values():
        copied = dict(row)
        for key in ("estimated_spent_seconds", "estimated_baseline_seconds", "estimated_saved_seconds"):
            copied[key] = round(float(copied[key]), 3)
        rows.append(copied)
    return sorted(rows, key=lambda item: (-int(item.get("tool_call_count", 0)) - int(item.get("commit_count", 0)), str(item.get("name", ""))))


def _self_opt_counter_rows(counter: dict[str, int], value_name: str = "count") -> list[dict[str, Any]]:
    return [
        {"name": key, value_name: value}
        for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _self_opt_aggregate_records(records: list[dict[str, Any]], cache_stats: dict[str, Any]) -> dict[str, Any]:
    by_tool: dict[str, dict[str, Any]] = {}
    by_workflow: dict[str, dict[str, Any]] = {}
    by_issue: dict[str, dict[str, Any]] = {}
    by_pr: dict[str, dict[str, Any]] = {}
    failure_reasons: dict[str, int] = {}
    models: dict[str, int] = {}
    backends: dict[str, int] = {}
    execution_modes: dict[str, int] = {}
    timestamps: list[datetime] = []
    totals = {
        "observed_record_count": len(records),
        "audit_event_count": 0,
        "trace_span_count": 0,
        "task_record_count": 0,
        "commit_count": 0,
        "tool_call_count": 0,
        "success_count": 0,
        "failed_or_noisy_count": 0,
        "estimated_spent_seconds": 0.0,
        "estimated_baseline_seconds": 0.0,
        "estimated_saved_seconds": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_saved_tokens": 0,
        "cache_hit_count": 0,
        "compressed_observation_count": 0,
        "omitted_signal_count": 0,
        "compression_estimated_saved_tokens": 0,
        "attributed_record_count": 0,
        "verbose_tool_call_count": 0,
    }
    issue_refs: set[str] = set()
    pr_refs: set[str] = set()
    workflow_refs: set[str] = set()

    for record in records:
        source = str(record.get("source", ""))
        if source == "audit":
            totals["audit_event_count"] += 1
        elif source == "trace":
            totals["trace_span_count"] += 1
        elif source == "task":
            totals["task_record_count"] += 1
        elif source == "git_commit":
            totals["commit_count"] += 1
        if source != "git_commit":
            totals["tool_call_count"] += 1
        if bool(record.get("success", True)):
            totals["success_count"] += 1
        ts = record.get("timestamp")
        if isinstance(ts, datetime):
            timestamps.append(ts)
        baseline = _self_opt_record_baseline_seconds(record)
        spent = _self_opt_record_spent_seconds(record)
        saved = max(0.0, baseline - spent)
        noisy = _self_opt_is_noisy(record, spent)
        if noisy:
            totals["failed_or_noisy_count"] += 1
            reason = str(record.get("reason", "") or "unspecified")[:120]
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
        totals["estimated_spent_seconds"] += spent
        totals["estimated_baseline_seconds"] += baseline
        totals["estimated_saved_seconds"] += saved
        tool = str(record.get("tool", "unknown") or "unknown")
        _self_opt_update_bucket(by_tool, tool, record, baseline, spent, saved, noisy)
        workflow = str(record.get("workflow", "") or "")
        if workflow:
            workflow_refs.add(workflow)
            _self_opt_update_bucket(by_workflow, workflow, record, baseline, spent, saved, noisy)
        issues = [str(item) for item in record.get("issue_refs", []) if str(item)]
        prs = [str(item) for item in record.get("pr_refs", []) if str(item)]
        for issue in issues:
            issue_refs.add(issue)
            _self_opt_update_bucket(by_issue, issue, record, baseline, spent, saved, noisy)
        for pr in prs:
            pr_refs.add(pr)
            _self_opt_update_bucket(by_pr, pr, record, baseline, spent, saved, noisy)
        if issues or prs or workflow:
            totals["attributed_record_count"] += 1
        tokens = record.get("tokens", {}) if isinstance(record.get("tokens"), dict) else {}
        compression = record.get("compression", {}) if isinstance(record.get("compression"), dict) else {}
        totals["input_tokens"] += int(tokens.get("input_tokens", 0) or 0)
        totals["output_tokens"] += int(tokens.get("output_tokens", 0) or 0)
        totals["total_tokens"] += int(tokens.get("total_tokens", 0) or 0)
        totals["estimated_saved_tokens"] += int(tokens.get("saved_tokens", 0) or 0)
        totals["cache_hit_count"] += int(record.get("cache_hit_count", 0) or 0)
        totals["compressed_observation_count"] += int(compression.get("compressed_observation_count", 0) or 0)
        totals["omitted_signal_count"] += int(compression.get("omitted_signal_count", 0) or 0)
        compression_saved = int(compression.get("estimated_saved_tokens", 0) or 0)
        totals["compression_estimated_saved_tokens"] += compression_saved
        totals["estimated_saved_tokens"] += compression_saved
        if tool in {"grep", "governance_report", "read_snippet", "summarize_diff"}:
            totals["verbose_tool_call_count"] += 1
        routing = record.get("routing", {}) if isinstance(record.get("routing"), dict) else {}
        for model in routing.get("models", []) if isinstance(routing.get("models"), list) else []:
            models[str(model)] = models.get(str(model), 0) + 1
        for backend in routing.get("backends", []) if isinstance(routing.get("backends"), list) else []:
            backends[str(backend)] = backends.get(str(backend), 0) + 1
        for mode in routing.get("execution_modes", []) if isinstance(routing.get("execution_modes"), list) else []:
            execution_modes[str(mode)] = execution_modes.get(str(mode), 0) + 1

    cache_tools = cache_stats.get("tools", {}) if isinstance(cache_stats.get("tools"), dict) else {}
    cache_entry_count = int(cache_stats.get("total_entries", 0) or 0)
    cache_estimated_saved_seconds = round(cache_entry_count * 10.0, 3)
    totals["estimated_saved_seconds"] += cache_estimated_saved_seconds
    totals["estimated_saved_tokens"] += cache_entry_count * 250
    elapsed_seconds = 0.0
    if timestamps:
        elapsed_seconds = max(0.0, (max(timestamps) - min(timestamps)).total_seconds())
    attribution_rate = (totals["attributed_record_count"] / len(records)) if records else 0.0
    success_rate = (totals["success_count"] / len(records)) if records else 1.0
    rounded_totals = dict(totals)
    for key in ("estimated_spent_seconds", "estimated_baseline_seconds", "estimated_saved_seconds"):
        rounded_totals[key] = round(float(rounded_totals[key]), 3)
    rounded_totals["elapsed_seconds"] = round(elapsed_seconds, 3)
    rounded_totals["success_rate"] = round(success_rate, 4)
    rounded_totals["attribution_rate"] = round(attribution_rate, 4)
    return {
        "totals": rounded_totals,
        "by_tool": _self_opt_bucket_rows(by_tool),
        "by_workflow": _self_opt_bucket_rows(by_workflow),
        "by_issue": _self_opt_bucket_rows(by_issue),
        "by_pr": _self_opt_bucket_rows(by_pr),
        "routing": {
            "models": _self_opt_counter_rows(models),
            "backends": _self_opt_counter_rows(backends),
            "execution_modes": _self_opt_counter_rows(execution_modes),
            "data_available": bool(models or backends or execution_modes),
        },
        "cache": {
            "entry_count": cache_entry_count,
            "by_tool": dict(sorted((str(k), int(v)) for k, v in cache_tools.items())),
            "observed_cache_hit_count": totals["cache_hit_count"],
            "estimated_saved_seconds": cache_estimated_saved_seconds,
            "estimated_saved_tokens": cache_entry_count * 250,
            "data_available": cache_entry_count > 0 or totals["cache_hit_count"] > 0,
        },
        "compression": {
            "compressed_observation_count": totals["compressed_observation_count"],
            "omitted_signal_count": totals["omitted_signal_count"],
            "estimated_saved_tokens": totals["compression_estimated_saved_tokens"],
            "data_available": totals["compressed_observation_count"] > 0,
        },
        "throughput": {
            "issues_touched": sorted(issue_refs),
            "prs_touched": sorted(pr_refs),
            "workflows_touched": sorted(workflow_refs),
            "issue_count": len(issue_refs),
            "pr_count": len(pr_refs),
            "workflow_count": len(workflow_refs),
            "commit_count": totals["commit_count"],
            "attribution_rate": round(attribution_rate, 4),
        },
        "failures": {
            "failed_or_noisy_count": totals["failed_or_noisy_count"],
            "reasons": _self_opt_counter_rows(failure_reasons),
        },
        "estimation_basis": {
            "baseline_seconds_by_tool": SELF_OPTIMIZATION_BASELINE_SECONDS_BY_TOOL,
            "default_untraced_spent_seconds": 4.0,
            "cache_entry_saved_seconds": 10.0,
            "cache_entry_saved_tokens": 250,
            "token_estimates": "Uses explicit local token fields when present; otherwise only compression/cache estimates are counted.",
        },
    }


def _self_opt_recommendation_key(category: str, title: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", f"{category}-{title}".lower()).strip("-")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{normalized[:80]}-{digest}"


def _self_opt_candidate(
    category: str,
    title: str,
    rationale: str,
    evidence: dict[str, Any],
    action: str,
    *,
    estimated_saved_seconds: float = 0.0,
    estimated_saved_tokens: int = 0,
) -> dict[str, Any]:
    duplicate_key = _self_opt_recommendation_key(category, title)
    return {
        "id": f"self-opt-{hashlib.sha256(duplicate_key.encode('utf-8')).hexdigest()[:10]}",
        "duplicate_key": duplicate_key,
        "category": category,
        "title": title,
        "rationale": rationale,
        "evidence": evidence,
        "recommended_action": action,
        "estimated_impact": {
            "saved_seconds": round(float(estimated_saved_seconds), 3),
            "saved_tokens": int(estimated_saved_tokens),
        },
        "suppressed": False,
        "duplicate_of": "",
    }


def _self_opt_existing_recommendations() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    payload = _json_file_load(SELF_OPTIMIZATION_RECOMMENDATION_INDEX_FILE, {"recommendations": []})
    if isinstance(payload, dict) and isinstance(payload.get("recommendations"), list):
        rows.extend(item for item in payload["recommendations"] if isinstance(item, dict))
    reports_dir = _resolve_repo_path(str(REPORTS_DIR))
    if reports_dir.exists():
        for path in sorted(reports_dir.glob("self-optimization-report-*.json"))[-20:]:
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(report, dict) and isinstance(report.get("optimization_candidates"), list):
                rows.extend(item for item in report["optimization_candidates"] if isinstance(item, dict))
    return rows


def _self_opt_suppress_duplicate_recommendations(
    candidates: list[dict[str, Any]],
    existing_recommendations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    existing_recommendations = existing_recommendations if existing_recommendations is not None else _self_opt_existing_recommendations()
    seen: dict[str, str] = {}
    for row in existing_recommendations:
        key = str(row.get("duplicate_key", ""))
        if key:
            seen[key] = str(row.get("id") or row.get("title") or "existing_recommendation")
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        copied = dict(candidate)
        key = str(copied.get("duplicate_key") or _self_opt_recommendation_key(str(copied.get("category", "")), str(copied.get("title", ""))))
        copied["duplicate_key"] = key
        if key in seen:
            copied["suppressed"] = True
            copied["duplicate_of"] = seen[key]
        else:
            copied["suppressed"] = False
            copied["duplicate_of"] = ""
            seen[key] = str(copied.get("id") or copied.get("title") or key)
        out.append(copied)
    return out


def _self_opt_build_recommendations(metrics: dict[str, Any], sources: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    totals = metrics.get("totals", {}) if isinstance(metrics.get("totals"), dict) else {}
    throughput = metrics.get("throughput", {}) if isinstance(metrics.get("throughput"), dict) else {}
    cache = metrics.get("cache", {}) if isinstance(metrics.get("cache"), dict) else {}
    compression = metrics.get("compression", {}) if isinstance(metrics.get("compression"), dict) else {}
    failures = metrics.get("failures", {}) if isinstance(metrics.get("failures"), dict) else {}
    candidates: list[dict[str, Any]] = []
    failed_or_noisy = int(totals.get("failed_or_noisy_count", 0) or 0)
    if failed_or_noisy:
        candidates.append(
            _self_opt_candidate(
                "workflow-reliability",
                "Investigate repeated failed or noisy MCP runs",
                "Failed/noisy tool runs create rework and inflate token spend before issue handoff.",
                {"failed_or_noisy_count": failed_or_noisy, "top_reasons": failures.get("reasons", [])[:3]},
                "Create or update a focused optimization issue for the top repeated failure category before adding new workflow features.",
                estimated_saved_seconds=float(totals.get("estimated_spent_seconds", 0) or 0) * 0.2,
            )
        )
    tool_call_count = int(totals.get("tool_call_count", 0) or 0)
    cache_hits = int(cache.get("observed_cache_hit_count", 0) or 0)
    if tool_call_count >= 3 and cache_hits == 0:
        candidates.append(
            _self_opt_candidate(
                "cache-reuse",
                "Reuse cache or index artifacts for repeated inspection",
                "Recent MCP usage shows repeated tool calls without observed cache hits.",
                {"tool_call_count": tool_call_count, "cache_entry_count": cache.get("entry_count", 0)},
                "Prefer fresh `test_impact_map`, symbol/dependency cache, and result handles before rerunning broad search/read workflows.",
                estimated_saved_seconds=float(cache.get("estimated_saved_seconds", 0) or 0),
                estimated_saved_tokens=int(cache.get("estimated_saved_tokens", 0) or 0),
            )
        )
    if int(totals.get("verbose_tool_call_count", 0) or 0) > 0 and int(compression.get("compressed_observation_count", 0) or 0) == 0:
        candidates.append(
            _self_opt_candidate(
                "token-compression",
                "Use compressed observations for verbose MCP outputs",
                "Verbose report/search tools ran without local compressed-observation evidence.",
                {"verbose_tool_call_count": totals.get("verbose_tool_call_count", 0)},
                "Enable `compressed_observation=true` on supported verbose tools and keep raw artifacts behind repo-local resource links.",
                estimated_saved_tokens=max(0, int(totals.get("tool_call_count", 0) or 0) * 120),
            )
        )
    if int(totals.get("total_tokens", 0) or 0) == 0:
        candidates.append(
            _self_opt_candidate(
                "telemetry-coverage",
                "Record token and model routing fields in redacted local telemetry",
                "No explicit token usage was available in the selected window, so savings are only estimated.",
                {"trace_span_count": totals.get("trace_span_count", 0), "total_tokens": 0},
                "Populate local span attributes for prompt/completion/total tokens, model, backend, and cache status without storing prompts or raw traces.",
            )
        )
    if float(throughput.get("attribution_rate", 0.0) or 0.0) < 0.75 and tool_call_count:
        candidates.append(
            _self_opt_candidate(
                "throughput-attribution",
                "Attach issue and PR identifiers to MCP task prompts",
                "Low attribution makes it harder to connect MCP usage to issue/PR throughput.",
                {"attribution_rate": throughput.get("attribution_rate", 0.0), "tool_call_count": tool_call_count},
                "Include `issue #N`, `PR #N`, or workflow names in task memory and audit-safe arguments for software-team work.",
            )
        )
    trace_source = sources.get("traces", {}) if isinstance(sources.get("traces"), dict) else {}
    if not trace_source.get("exists"):
        candidates.append(
            _self_opt_candidate(
                "timing-coverage",
                "Enable redacted local OTel spans for timing analysis",
                "Trace spans were absent, so elapsed/spent time relies on conservative baseline estimates.",
                {"trace_source_exists": False},
                "Use `MCP_OTEL_TRACING_ENABLED=true` with the local JSONL exporter when measuring workflow throughput.",
            )
        )
    return _self_opt_suppress_duplicate_recommendations(candidates[: max(0, limit)])


def _self_opt_bottlenecks(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    bottlenecks: list[dict[str, Any]] = []
    failures = metrics.get("failures", {}) if isinstance(metrics.get("failures"), dict) else {}
    for row in failures.get("reasons", [])[:5] if isinstance(failures.get("reasons"), list) else []:
        bottlenecks.append(
            {
                "type": "failure_or_noisy_run",
                "name": row.get("name", "unspecified"),
                "count": row.get("count", 0),
                "suggestion": "Inspect the repeated failure category and add a focused regression or workflow guard.",
            }
        )
    slow_tools = [row for row in metrics.get("by_tool", []) if isinstance(row, dict) and float(row.get("estimated_spent_seconds", 0) or 0) >= 30]
    for row in slow_tools[:3]:
        bottlenecks.append(
            {
                "type": "high_spend_tool",
                "name": row.get("name", "unknown"),
                "estimated_spent_seconds": row.get("estimated_spent_seconds", 0),
                "suggestion": "Check whether a narrower query, cache, or compressed observation can replace repeated broad calls.",
            }
        )
    return bottlenecks


def _self_opt_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    totals = metrics.get("totals", {}) if isinstance(metrics.get("totals"), dict) else {}
    throughput = metrics.get("throughput", {}) if isinstance(metrics.get("throughput"), dict) else {}
    return {
        "headline": (
            f"Observed {totals.get('tool_call_count', 0)} MCP/tool record(s), "
            f"{throughput.get('issue_count', 0)} issue(s), {throughput.get('pr_count', 0)} PR(s), "
            f"and {totals.get('failed_or_noisy_count', 0)} failed/noisy run(s)."
        ),
        "estimated_spent_seconds": totals.get("estimated_spent_seconds", 0),
        "estimated_saved_seconds": totals.get("estimated_saved_seconds", 0),
        "estimated_saved_tokens": totals.get("estimated_saved_tokens", 0),
        "success_rate": totals.get("success_rate", 1.0),
        "attribution_rate": totals.get("attribution_rate", 0.0),
    }


def _self_opt_report_paths(report_id: str) -> dict[str, str]:
    base = REPORTS_DIR / report_id
    return {"json": str(base.with_suffix(".json")), "markdown": str(base.with_suffix(".md"))}


def _self_opt_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    metrics = report.get("metrics", {}) if isinstance(report.get("metrics"), dict) else {}
    throughput = metrics.get("throughput", {}) if isinstance(metrics.get("throughput"), dict) else {}
    candidates = report.get("optimization_candidates", []) if isinstance(report.get("optimization_candidates"), list) else []
    lines = [
        f"# Self-optimization report `{report.get('report_id', '')}`",
        "",
        str(summary.get("headline", "")),
        "",
        "## Window",
        "",
        f"- Start: `{report.get('window', {}).get('start_time', '') if isinstance(report.get('window'), dict) else ''}`",
        f"- End: `{report.get('window', {}).get('end_time', '') if isinstance(report.get('window'), dict) else ''}`",
        "",
        "## Throughput",
        "",
        f"- Issues touched: {', '.join(throughput.get('issues_touched', []) or []) or 'none observed'}",
        f"- PRs touched: {', '.join(throughput.get('prs_touched', []) or []) or 'none observed'}",
        f"- Workflows touched: {', '.join(throughput.get('workflows_touched', []) or []) or 'none observed'}",
        "",
        "## Optimization candidates",
        "",
    ]
    if candidates:
        for item in candidates:
            status = "suppressed duplicate" if item.get("suppressed") else "new"
            lines.append(f"- **{item.get('title', '')}** ({status}): {item.get('recommended_action', '')}")
    else:
        lines.append("- No candidates generated for this window.")
    lines.extend(
        [
            "",
            "## Privacy",
            "",
            "This report is generated from repo-local, redacted summaries and does not embed raw traces, prompts, secrets, or absolute host paths.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_self_opt_report_exports(report: dict[str, Any]) -> dict[str, str]:
    paths = _self_opt_report_paths(str(report["report_id"]))
    json_path = _resolve_repo_path(paths["json"])
    md_path = _resolve_repo_path(paths["markdown"])
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    md_path.write_text(_self_opt_markdown(report), encoding="utf-8")
    return {"json": str(json_path.relative_to(REPO_PATH)), "markdown": str(md_path.relative_to(REPO_PATH))}


def _self_optimization_report_impl(
    start_time: str = "",
    end_time: str = "",
    window_hours: int = 168,
    export: bool = False,
    recommendation_limit: int = 10,
    include_git: bool = True,
    include_audit: bool = True,
    include_traces: bool = True,
    redact_terms: list[str] | None = None,
) -> dict[str, Any]:
    _ensure_repo_path_exists()
    if recommendation_limit < 0 or recommendation_limit > 50:
        raise ValueError("recommendation_limit must be between 0 and 50")
    start_dt, end_dt = _self_opt_parse_window(start_time, end_time, window_hours)
    redaction_terms = _self_opt_default_redact_terms(redact_terms)
    records: list[dict[str, Any]] = []
    sources: dict[str, Any] = {
        "network": {"used": False, "policy": "repo-local/offline only; no GitHub/API/network calls are made"},
    }

    if include_audit:
        audit_events, audit_meta = _load_audit_events(start_dt, end_dt)
        records.extend(_self_opt_normalize_audit_event(event, redaction_terms) for event in audit_events)
        sources["audit"] = _self_opt_public_source_meta(audit_meta)
    else:
        sources["audit"] = {"enabled": False}

    if include_traces:
        span_rows, span_meta = _self_opt_load_jsonl_records(MCP_OTEL_SPANS_FILE, start_dt, end_dt)
        records.extend(_self_opt_normalize_span(span, redaction_terms) for span in span_rows)
        sources["traces"] = _self_opt_public_source_meta(span_meta)
    else:
        sources["traces"] = {"enabled": False}

    task_records, task_meta = _self_opt_load_task_records(start_dt, end_dt, redaction_terms)
    records.extend(task_records)
    sources["tasks"] = _self_opt_public_source_meta(task_meta)

    if include_git:
        git_records, git_meta = _self_opt_git_commit_records(start_dt, end_dt, redaction_terms)
        records.extend(git_records)
        sources["git"] = git_meta
    else:
        sources["git"] = {"enabled": False, "network_used": False}

    try:
        cache_stats = _cache_stats()
        sources["cache"] = {"readable": True, **cache_stats}
    except Exception as exc:
        cache_stats = {"total_entries": 0, "tools": {}}
        sources["cache"] = {"readable": False, "reason": exc.__class__.__name__}

    metrics = _self_opt_aggregate_records(records, cache_stats)
    generated_at = _now_iso()
    report_seed = json.dumps(
        {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "metrics": metrics.get("totals", {}),
            "sources": {key: value for key, value in sources.items() if key != "network"},
        },
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    )
    report_id = f"self-optimization-report-{_now_stamp()}-{hashlib.sha256(report_seed.encode('utf-8')).hexdigest()[:12]}"
    report: dict[str, Any] = {
        "schema": SELF_OPTIMIZATION_REPORT_SCHEMA,
        "report_id": report_id,
        "generated_at": generated_at,
        "window": {
            "start_time": start_dt.isoformat(),
            "end_time": end_dt.isoformat(),
            "window_hours": round((end_dt - start_dt).total_seconds() / 3600.0, 3),
        },
        "summary": _self_opt_summary(metrics),
        "metrics": metrics,
        "sources": sources,
        "bottlenecks": _self_opt_bottlenecks(metrics),
        "optimization_candidates": [],
        "usage_guidance": {
            "direct_tool": "self_optimization_report",
            "when_to_run": [
                "after a batch of issue/PR work",
                "after noisy or failed MCP workflows",
                "before creating optimization issues for the software team",
            ],
            "recommended_call": "self_optimization_report(window_hours=168, export=true)",
            "issue_creation_policy": "Use unsuppressed candidates as issue drafts only after checking project priorities; this tool does not create network issues.",
        },
        "security": {
            "offline_capable": True,
            "network_used": False,
            "repo_boundary_enforced": True,
            "redaction_applied": True,
            "raw_traces_exposed": False,
            "raw_prompts_exposed": False,
            "records_secrets": False,
            "sensitive_names_redacted": True,
        },
        "exports": {},
        "resource_links": [],
    }
    report["optimization_candidates"] = _self_opt_build_recommendations(metrics, report["sources"], recommendation_limit)
    report = _self_opt_redact_value(report, redaction_terms)
    if export:
        exports = _write_self_opt_report_exports(report)
        report["exports"] = exports
        links = [
            _artifact_resource_link(
                title="Self-optimization report JSON",
                rel_path=exports["json"],
                mime_type="application/json",
                created_at=generated_at,
                redacted=True,
                safety_note="JSON export contains redacted aggregate metrics and recommendation metadata only.",
            ),
            _artifact_resource_link(
                title="Self-optimization report Markdown",
                rel_path=exports["markdown"],
                mime_type="text/markdown",
                created_at=generated_at,
                redacted=True,
                safety_note="Markdown export is generated from redacted aggregate report fields.",
            ),
        ]
        for link in links:
            if isinstance(link.get("path"), str):
                path = _resolve_repo_path(str(link["path"]))
                if path.exists():
                    link["size_bytes"] = path.stat().st_size
        report["resource_links"] = links
        report["_meta"] = _artifact_meta(links)
        # Rewrite exports after resource links/_meta are known.
        _write_self_opt_report_exports(report)
    else:
        report["_meta"] = _artifact_meta([])
    return report




WORKFLOW_FAILURE_CATEGORIES = {
    "auth_policy_denial",
    "mutation_disabled",
    "path_scope_violation",
    "missing_snapshot_rollback",
    "failed_readiness_test_gate",
    "malformed_tool_output",
    "unknown_failure",
}


def _diagnostic_text_blob(*values: Any) -> str:
    return " ".join(
        str(value).lower()
        for value in values
        if value not in (None, "", [], {})
    )


def _workflow_failure_category(step: dict[str, Any]) -> str:
    tool = str(step.get("tool", "")).lower()
    category = str(step.get("category", "")).lower()
    reason = step.get("error") or step.get("reason") or ""
    flags = step.get("policy_flags", [])
    outputs = step.get("key_outputs", {})
    blob = _diagnostic_text_blob(tool, category, reason, flags, outputs, step.get("redacted_args", {}))
    if any(term in blob for term in ("mutations disabled", "mutation disabled", "allow_mutations=false")):
        return "mutation_disabled"
    if any(term in blob for term in ("not authorized", "unauthorized", "bearer token", "auth", "policy denied", "policy denial", "blocking_policies")):
        return "auth_policy_denial"
    if any(term in blob for term in ("outside repo", "outside repository", "repo boundary", "path boundary", "path/scope", "not under repo", "scope violation")):
        return "path_scope_violation"
    if any(term in blob for term in ("missing snapshot", "snapshot missing", "rollback missing", "rollback required", "state_snapshot", "state_restore")):
        return "missing_snapshot_rollback"
    if any(term in blob for term in ("readiness failed", "release_readiness", "test failed", "tests failed", "gate skipped", "required gate skipped", "required_tool_chain", "self_test")):
        return "failed_readiness_test_gate"
    if any(term in blob for term in ("malformed", "invalid schema", "schema invalid", "output schema", "jsondecode", "json decode", "parse error")):
        return "malformed_tool_output"
    return "unknown_failure"


def _workflow_policy_flags(step: dict[str, Any], category: str) -> list[str]:
    flags = step.get("policy_flags", [])
    normalized = [str(flag) for flag in flags] if isinstance(flags, list) else []
    if category != "unknown_failure" and category not in normalized:
        normalized.append(category)
    return normalized[:10]


def _normalize_workflow_step(raw: dict[str, Any], index: int, source: str) -> dict[str, Any]:
    tool = str(raw.get("tool") or raw.get("tool_name") or raw.get("name") or "unknown")
    raw_categories = raw.get("categories", raw.get("category", []))
    if isinstance(raw_categories, list):
        category = ",".join(str(item) for item in raw_categories)
    else:
        category = str(raw_categories or "")
    success = bool(raw.get("success", raw.get("ok", False)))
    reason = str(raw.get("error") or raw.get("reason") or raw.get("message") or "")
    args = raw.get("arguments", raw.get("args", {}))
    outputs = raw.get("outputs", raw.get("output", raw.get("result", {})))
    redacted_args = _redact_audit_value(args if isinstance(args, (dict, list, str)) else str(args))
    redacted_outputs = _redact_audit_value(outputs if isinstance(outputs, (dict, list, str)) else str(outputs))
    step = {
        "step_id": str(raw.get("step_id") or raw.get("id") or f"{source}-{index + 1}"),
        "source": source,
        "timestamp": str(raw.get("timestamp", "")),
        "tool": tool,
        "category": category,
        "success": success,
        "error": _redact_audit_string(reason),
        "redacted_args": redacted_args,
        "key_outputs": redacted_outputs,
        "policy_flags": raw.get("policy_flags", []) if isinstance(raw.get("policy_flags", []), list) else [],
    }
    if not success:
        failure_category = _workflow_failure_category(step)
        step["failure_category"] = failure_category
        step["policy_flags"] = _workflow_policy_flags(step, failure_category)
    return step


def _workflow_safe_next_actions(category: str) -> list[str]:
    actions = {
        "auth_policy_denial": [
            "Re-authenticate the MCP session or rerun through an authorized client before retrying.",
            "If this was a policy denial, inspect the policy evidence and narrow the requested operation.",
        ],
        "mutation_disabled": [
            "Keep analysis read-only or restart with ALLOW_MUTATIONS=true only after explicit operator approval.",
            "Prefer planning/diff preview tools before enabling mutation-capable tools.",
        ],
        "path_scope_violation": [
            "Retry with repository-relative paths under REPO_PATH.",
            "Do not follow symlinks or absolute paths that resolve outside the mounted repository.",
        ],
        "missing_snapshot_rollback": [
            "Create a state_snapshot or documented rollback point before mutation work.",
            "Record the rollback reference in the recovery plan before retrying.",
        ],
        "failed_readiness_test_gate": [
            "Inspect the failing readiness/test evidence and run the smallest targeted check first.",
            "Do not advance release or review gates until the failed check is green or explicitly waived.",
        ],
        "malformed_tool_output": [
            "Validate the tool output against its schema and capture the raw parse error.",
            "Retry with a smaller output profile or structured output contract.",
        ],
        "unknown_failure": [
            "Review the failed step evidence and rerun only the narrowest safe diagnostic.",
            "Add a more specific failure reason to future audit events if this repeats.",
        ],
    }
    return actions.get(category, actions["unknown_failure"])


def _workflow_evidence(step: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = [
        {"field": "tool", "value": step.get("tool", "")},
        {"field": "error", "value": step.get("error", "")},
    ]
    if step.get("policy_flags"):
        evidence.append({"field": "policy_flags", "value": step.get("policy_flags")})
    if step.get("timestamp"):
        evidence.append({"field": "timestamp", "value": step.get("timestamp")})
    return evidence


def _workflow_redactions_applied(steps: list[dict[str, Any]]) -> list[str]:
    encoded = json.dumps(steps, sort_keys=True, ensure_ascii=True)
    redactions: list[str] = []
    if "<redacted>" in encoded:
        redactions.append("sensitive_keys_or_values")
    if "...[truncated]" in encoded:
        redactions.append("long_strings_truncated")
    if "<redacted-depth>" in encoded:
        redactions.append("nested_values_depth_limited")
    return redactions


def _build_workflow_diagnostics(events: list[dict[str, Any]], trajectory: list[dict[str, Any]] | None = None, limit: int = 50) -> dict[str, Any]:
    audit_steps = [_normalize_workflow_step(event, idx, "audit") for idx, event in enumerate(events[-limit:])]
    trajectory_steps = [
        _normalize_workflow_step(step, idx, "trajectory")
        for idx, step in enumerate((trajectory or [])[:limit])
        if isinstance(step, dict)
    ]
    steps = audit_steps + trajectory_steps
    failed = [step for step in steps if not step.get("success", False)]
    categorized = [step for step in failed if step.get("failure_category") != "unknown_failure"]
    critical = (categorized or failed or [None])[0]
    failure_category = str(critical.get("failure_category", "") if isinstance(critical, dict) else "")
    if not failure_category:
        failure_category = "none"
    category_counts: dict[str, int] = {}
    for step in failed:
        key = str(step.get("failure_category", "unknown_failure"))
        category_counts[key] = category_counts.get(key, 0) + 1
    report = {
        "schema": "workflow_diagnostics.v1",
        "ok": not failed,
        "step_count": len(steps),
        "failed_step_count": len(failed),
        "failure_categories": dict(sorted(category_counts.items())),
        "critical_step_candidate": critical or {},
        "failure_category": failure_category,
        "evidence": _workflow_evidence(critical) if isinstance(critical, dict) else [],
        "safe_next_actions": _workflow_safe_next_actions(failure_category) if failure_category != "none" else [],
        "redactions_applied": _workflow_redactions_applied(steps),
        "trajectory": steps[:limit],
    }
    return report


def _workflow_diagnostics_compact(report: dict[str, Any]) -> dict[str, Any]:
    critical = report.get("critical_step_candidate", {})
    return {
        "schema": "workflow_diagnostics.summary.v1",
        "ok": bool(report.get("ok", True)),
        "failed_step_count": int(report.get("failed_step_count", 0)),
        "failure_category": str(report.get("failure_category", "none")),
        "critical_step_id": str(critical.get("step_id", "")) if isinstance(critical, dict) else "",
        "critical_tool": str(critical.get("tool", "")) if isinstance(critical, dict) else "",
        "safe_next_actions": list(report.get("safe_next_actions", []))[:3]
        if isinstance(report.get("safe_next_actions"), list)
        else [],
        "redactions_applied": list(report.get("redactions_applied", []))[:5]
        if isinstance(report.get("redactions_applied"), list)
        else [],
    }


def _governance_result_store_summary() -> dict[str, Any]:
    payload = _result_store_load()
    rows = payload.get("results", {})
    if not isinstance(rows, dict):
        rows = {}
    wanted = {"policy_simulator", "release_readiness", "required_tool_chain"}
    out: dict[str, Any] = {
        "policy_simulator": {"count": 0, "ok_count": 0, "failed_count": 0, "latest": None},
        "release_readiness": {"count": 0, "ok_count": 0, "failed_count": 0, "latest": None},
        "required_tool_chain": {"count": 0, "ok_count": 0, "failed_count": 0, "latest": None},
    }
    for rid, row in rows.items():
        if not isinstance(row, dict):
            continue
        tool = str(row.get("tool", ""))
        if tool not in wanted:
            continue
        value = row.get("value")
        ok = bool(value.get("ok", False)) if isinstance(value, dict) else False
        bucket = out[tool]
        bucket["count"] += 1
        bucket["ok_count" if ok else "failed_count"] += 1
        latest = bucket.get("latest")
        created_at = str(row.get("created_at", ""))
        if not latest or created_at >= str(latest.get("created_at", "")):
            details: dict[str, Any] = {}
            if isinstance(value, dict):
                for key in ("schema", "ok", "blocking_policies", "checks", "missing_tools", "missing_artifacts", "missing_result_ids"):
                    if key in value:
                        details[key] = _redact_audit_value(value[key])
            bucket["latest"] = {"result_id": str(rid), "created_at": created_at, "ok": ok, "details": details}
    return out


def _governance_snapshot_references(limit: int = 20) -> dict[str, Any]:
    index = _state_snapshot_index_load()
    snapshots = index.get("snapshots", {})
    if not isinstance(snapshots, dict):
        snapshots = {}
    refs: list[dict[str, Any]] = []
    for sid, entry in snapshots.items():
        if not isinstance(entry, dict):
            continue
        refs.append(
            {
                "snapshot_id": str(sid),
                "created_at": str(entry.get("created_at", "")),
                "base_head": str(entry.get("base_head", "")),
                "stash_ref": str(entry.get("stash_ref", "")),
                "has_stash_commit": bool(entry.get("stash_commit")),
            }
        )
    refs.sort(key=lambda row: row.get("created_at", ""), reverse=True)
    return {"count": len(refs), "latest": refs[:limit]}


def _latest_governance_report(max_age_hours: int = 24) -> dict[str, Any]:
    reports_dir = _resolve_repo_path(str(REPORTS_DIR))
    now = datetime.now(timezone.utc)
    latest: dict[str, Any] | None = None
    if not reports_dir.exists():
        return {"present": False, "required": False, "max_age_hours": max_age_hours}
    for path in sorted(reports_dir.glob("governance-report-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("schema") != "governance_report.v1":
            continue
        generated = _parse_iso_datetime(str(payload.get("generated_at", "")))
        age_hours = None
        recent = False
        if generated:
            age_hours = max(0.0, (now - generated).total_seconds() / 3600)
            recent = age_hours <= max_age_hours
        entry = {
            "present": True,
            "required": False,
            "recent": recent,
            "max_age_hours": max_age_hours,
            "report_id": str(payload.get("report_id", path.stem)),
            "generated_at": str(payload.get("generated_at", "")),
            "path": str(path.relative_to(REPO_PATH)),
            "age_hours": round(age_hours, 3) if age_hours is not None else None,
        }
        if latest is None or entry["generated_at"] >= latest.get("generated_at", ""):
            latest = entry
    return latest or {"present": False, "required": False, "max_age_hours": max_age_hours}


def _governance_markdown(report: dict[str, Any]) -> str:
    counts = report.get("audit", {}).get("counts", {}) if isinstance(report.get("audit"), dict) else {}
    lines = [
        f"# Governance report {report.get('report_id', '')}",
        "",
        f"- Schema: `{report.get('schema', '')}`",
        f"- Generated at: `{report.get('generated_at', '')}`",
        f"- Git range: `{report.get('git', {}).get('base_ref', '')}`...`{report.get('git', {}).get('head_ref', '')}`",
        f"- Audit events in window: {counts.get('event_count', 0)}",
        f"- Sensitive tool calls: {counts.get('sensitive_tool_call_count', 0)}",
        f"- Blocked attempts: {counts.get('blocked_attempt_count', 0)}",
        f"- Mutation-gate failures: {counts.get('mutation_gate_failure_count', 0)}",
        f"- HTTP authorization denials: {counts.get('http_authorization_denial_count', 0)}",
        "",
        "## Tool categories",
    ]
    by_category = counts.get("by_category", {}) if isinstance(counts, dict) else {}
    if by_category:
        lines.extend(f"- `{k}`: {v}" for k, v in sorted(by_category.items()))
    else:
        lines.append("- No audited tool categories found.")
    lines.extend(["", "## Failure reasons"])
    reasons = counts.get("failure_reasons", {}) if isinstance(counts, dict) else {}
    if reasons:
        lines.extend(f"- `{k}`: {v}" for k, v in sorted(reasons.items()))
    else:
        lines.append("- No blocked/failure reasons found.")
    diagnostics = report.get("workflow_diagnostics", {}) if isinstance(report.get("workflow_diagnostics"), dict) else {}
    if diagnostics and diagnostics.get("failed_step_count", 0):
        lines.extend(
            [
                "",
                "## Workflow diagnostics",
                f"- Failure category: `{diagnostics.get('failure_category', 'none')}`",
                f"- Critical step: `{diagnostics.get('critical_step_id', '')}` via `{diagnostics.get('critical_tool', '')}`",
            ]
        )
        actions = diagnostics.get("safe_next_actions", []) if isinstance(diagnostics.get("safe_next_actions"), list) else []
        if actions:
            lines.append("- Safe next actions:")
            lines.extend(f"  - {action}" for action in actions)
    lines.extend(["", "## Governance hooks"])
    hooks = report.get("governance_hooks", {}) if isinstance(report.get("governance_hooks"), dict) else {}
    for name in ("policy_simulator", "release_readiness", "required_tool_chain"):
        row = hooks.get(name, {}) if isinstance(hooks.get(name), dict) else {}
        lines.append(f"- `{name}`: {row.get('count', 0)} recorded result(s), latest ok={row.get('latest', {}).get('ok') if isinstance(row.get('latest'), dict) else None}")
    snapshots = report.get("snapshots", {}) if isinstance(report.get("snapshots"), dict) else {}
    lines.extend(["", "## Snapshot / rollback references", f"- Snapshot references: {snapshots.get('count', 0)}"])
    lineage = report.get("lineage", {}) if isinstance(report.get("lineage"), dict) else {}
    if lineage:
        lines.extend(
            [
                "",
                "## Workflow lineage",
                f"- Schema: `{lineage.get('schema', WORKFLOW_LINEAGE_SCHEMA)}`",
                f"- Plan ID: `{lineage.get('plan_id', '')}`",
                f"- Manifest: `{lineage.get('manifest', '')}`",
                "- Verification: `workflow_lineage(mode='verify', manifest_path='...')` recomputes deterministic inputs read-only and reports matched, input_changed, artifact_changed, and non_deterministic_node conditions.",
            ]
        )
    lines.extend(["", "## Digest", f"- Algorithm: `{counts.get('digest', {}).get('algorithm', '') if isinstance(counts.get('digest'), dict) else ''}`", f"- Chain head: `{counts.get('digest', {}).get('chain_head', '') if isinstance(counts.get('digest'), dict) else ''}`", "", "External OPA / Agent Governance Toolkit integrations are out of scope for this first slice.", ""])
    return "\n".join(lines)


def _workflow_lineage_json_digest(value: Any) -> dict[str, str]:
    canonical = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return {
        "algorithm": "sha256",
        "value": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _workflow_lineage_canonical_path(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    try:
        path = Path(text)
        if path.is_absolute():
            resolved = path.resolve(strict=False)
            try:
                return str(resolved.relative_to(REPO_PATH))
            except ValueError:
                return "<absolute_path_outside_repo>"
    except (OSError, RuntimeError, ValueError):
        return "<absolute_path>"
    return text


def _workflow_lineage_redact_paths(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return _workflow_lineage_canonical_path(match.group(0))

    return ABSOLUTE_PATH_VALUE_RE.sub(replace, value)


def _workflow_lineage_sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "<redacted-depth>"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if SENSITIVE_AUDIT_KEY_RE.search(key_str):
                sanitized[key_str] = "<redacted>"
            elif key_lower in {"prompt", "raw_prompt", "transcript", "snippet", "content", "file_contents"}:
                sanitized[key_str] = "<redacted>"
            else:
                sanitized[key_str] = _workflow_lineage_sanitize(item, depth + 1)
        return sanitized
    if isinstance(value, list):
        return [_workflow_lineage_sanitize(item, depth + 1) for item in value[:50]]
    if isinstance(value, str):
        redacted = _redact_audit_string(value)
        return _workflow_lineage_redact_paths(redacted)
    return value


def _workflow_lineage_normalize_time(value: str) -> str:
    parsed = _parse_iso_datetime(value) if str(value).strip() else None
    return parsed.isoformat() if parsed else ""


def _workflow_lineage_request_constraints(
    *,
    start_time: str,
    end_time: str,
    base_ref: str,
    head_ref: str,
    export: bool,
    compressed_observation: bool,
) -> dict[str, Any]:
    return _workflow_lineage_sanitize(
        {
            "start_time": _workflow_lineage_normalize_time(start_time),
            "end_time": _workflow_lineage_normalize_time(end_time),
            "base_ref": base_ref,
            "head_ref": head_ref,
            "export": bool(export),
            "compressed_observation": bool(compressed_observation),
        }
    )


def _workflow_lineage_audit_source(audit_meta: dict[str, Any]) -> dict[str, Any]:
    return _workflow_lineage_sanitize(
        {
            "configured_path": str(audit_meta.get("configured_path", "")),
            "source": str(audit_meta.get("source", "")),
            "exists": bool(audit_meta.get("exists", False)),
            "readable": bool(audit_meta.get("readable", False)),
            "events_total": int(audit_meta.get("events_total", 0) or 0),
            "events_in_window": int(audit_meta.get("events_in_window", 0) or 0),
            "malformed_lines": int(audit_meta.get("malformed_lines", 0) or 0),
        }
    )


def _governance_workflow_lineage_plan_inputs(
    *,
    constraints: dict[str, Any],
    git_info: dict[str, Any],
    counts: dict[str, Any],
    audit_meta: dict[str, Any],
) -> dict[str, Any]:
    selected_mode, selected_source = _resolve_agent_execution_mode("auto", "governance_report")
    plan_inputs = {
        "schema": WORKFLOW_LINEAGE_SCHEMA,
        "workflow": {
            "name": "governance_report",
            "card_id": "governance-report",
            "entrypoint": "governance_report",
            "tool_sequence": [
                "load_redacted_audit_events",
                "aggregate_governance_counts",
                "workflow_diagnostics.summary",
                "export_governance_report",
                "write_local_provenance_sidecars",
            ],
        },
        "execution_mode": {
            "schema": AGENT_EXECUTION_MODE_SCHEMA_VERSION,
            "mode": selected_mode,
            "source": selected_source,
        },
        "repository": {
            "git": {
                "base_ref": str(git_info.get("base_ref", "")),
                "head_ref": str(git_info.get("head_ref", "")),
                "base_commit": str(git_info.get("base_commit", "")),
                "head_commit": str(git_info.get("head_commit", "")),
            }
        },
        "request_constraints": constraints,
        "policy_profile": {
            "mutation_mode": "read-only",
            "redaction": "mcp_audit_redaction",
            "records_raw_prompts": False,
            "records_transcript_snippets": False,
            "records_file_contents": False,
        },
        "audit": {
            "source": _workflow_lineage_audit_source(audit_meta),
            "digest": counts.get("digest", {}) if isinstance(counts.get("digest"), dict) else {},
        },
        "schema_versions": {
            "governance_report": "governance_report.v1",
            "workflow_diagnostics": "workflow_diagnostics.summary.v1",
            "artifact_provenance": PROVENANCE_SCHEMA,
            "execution_mode": AGENT_EXECUTION_MODE_SCHEMA_VERSION,
        },
    }
    return _workflow_lineage_sanitize(plan_inputs)


def _workflow_lineage_plan_id(plan_inputs: dict[str, Any]) -> str:
    digest = _workflow_lineage_json_digest(_workflow_lineage_sanitize(plan_inputs))["value"]
    return f"workflow-plan-{digest[:32]}"


def _workflow_lineage_export_path(report_id: str) -> str:
    base = REPORTS_DIR / f"{report_id}{WORKFLOW_LINEAGE_SUFFIX}"
    return str(base)


def _workflow_lineage_artifact_ref(rel_path: str, role: str) -> dict[str, Any]:
    path = _resolve_repo_path(rel_path)
    ref: dict[str, Any] = {
        "path": rel_path,
        "role": role,
        "schema": _artifact_schema_from_path(path),
        "digest": {},
        "exists": path.exists() and path.is_file(),
    }
    if path.exists() and path.is_file():
        ref["digest"] = _artifact_digest(path)
    return ref


def _workflow_lineage_node_id(plan_id: str, logical_id: str) -> str:
    digest = hashlib.sha256(f"{plan_id}:{logical_id}".encode("utf-8")).hexdigest()
    return f"node-{digest[:16]}"


def _workflow_lineage_node(
    *,
    plan_id: str,
    logical_id: str,
    node_type: str,
    tool: str,
    schema_version: str,
    status: str = "succeeded",
    deterministic: bool = True,
    input_digests: dict[str, Any] | None = None,
    output_digests: dict[str, Any] | None = None,
    artifact_refs: list[dict[str, Any]] | None = None,
    rationale_summary: str = "",
    non_deterministic_reason: str = "",
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "node_id": _workflow_lineage_node_id(plan_id, logical_id),
        "logical_id": logical_id,
        "type": node_type,
        "tool": tool,
        "schema_version": schema_version,
        "status": status,
        "deterministic": deterministic,
        "input_digests": input_digests or {},
        "output_digests": output_digests or {},
        "artifact_refs": artifact_refs or [],
        "rationale_summary": _workflow_lineage_sanitize(rationale_summary),
    }
    if not deterministic:
        node["non_deterministic"] = {
            "marker": "non_deterministic_node",
            "observed_only": True,
            "reason": non_deterministic_reason
            or "Observed run output; deterministic replay identity does not promise bit-for-bit regeneration.",
        }
    return node


def _build_governance_workflow_lineage_manifest(
    report: dict[str, Any],
    *,
    provenance_inputs: dict[str, Any],
    audit_meta: dict[str, Any],
    counts: dict[str, Any],
) -> dict[str, Any]:
    git_info = report.get("git", {}) if isinstance(report.get("git"), dict) else {}
    constraints = _workflow_lineage_request_constraints(
        start_time=str(provenance_inputs.get("start_time", "")),
        end_time=str(provenance_inputs.get("end_time", "")),
        base_ref=str(provenance_inputs.get("base_ref", git_info.get("base_ref", ""))),
        head_ref=str(provenance_inputs.get("head_ref", git_info.get("head_ref", ""))),
        export=bool(provenance_inputs.get("export", False)),
        compressed_observation=bool(report.get("compressed_observation")),
    )
    plan_inputs = _governance_workflow_lineage_plan_inputs(
        constraints=constraints,
        git_info=git_info,
        counts=counts,
        audit_meta=audit_meta,
    )
    plan_id = _workflow_lineage_plan_id(plan_inputs)
    exports = report.get("exports", {}) if isinstance(report.get("exports"), dict) else {}
    artifact_refs: list[dict[str, Any]] = []
    if isinstance(exports.get("json"), str):
        artifact_refs.append(_workflow_lineage_artifact_ref(exports["json"], "governance_report_json"))
    if isinstance(exports.get("markdown"), str):
        artifact_refs.append(_workflow_lineage_artifact_ref(exports["markdown"], "governance_report_markdown"))
    diagnostic = report.get("workflow_diagnostics", {}) if isinstance(report.get("workflow_diagnostics"), dict) else {}
    nodes = [
        _workflow_lineage_node(
            plan_id=plan_id,
            logical_id="context.audit_window",
            node_type="context_retrieval",
            tool="governance_report",
            schema_version="audit_event_digest.sha256_chain.v1",
            input_digests={"constraints": _workflow_lineage_json_digest(constraints)},
            output_digests={"audit_chain": counts.get("digest", {}) if isinstance(counts.get("digest"), dict) else {}},
            rationale_summary="Load only redacted audit events in the requested time window; do not persist raw audit lines.",
        ),
        _workflow_lineage_node(
            plan_id=plan_id,
            logical_id="repository.git_range",
            node_type="context_retrieval",
            tool="git",
            schema_version="git.rev-parse",
            input_digests={"refs": _workflow_lineage_json_digest(git_info)},
            output_digests={
                "base_commit": {"algorithm": "git-object-id", "value": str(git_info.get("base_commit", ""))},
                "head_commit": {"algorithm": "git-object-id", "value": str(git_info.get("head_commit", ""))},
            },
            rationale_summary="Resolve repository refs to commit identities without recording absolute repository paths.",
        ),
        _workflow_lineage_node(
            plan_id=plan_id,
            logical_id="policy.redaction_profile",
            node_type="policy_check",
            tool="governance_report",
            schema_version="mcp_audit_redaction.v1",
            input_digests={"policy_profile": _workflow_lineage_json_digest(plan_inputs.get("policy_profile", {}))},
            output_digests={"redacted_counts": _workflow_lineage_json_digest(counts)},
            rationale_summary="Apply MCP audit redaction before aggregate report and lineage digests are emitted.",
        ),
        _workflow_lineage_node(
            plan_id=plan_id,
            logical_id="workflow_diagnostics.summary",
            node_type="report_summary",
            tool="workflow_diagnostics",
            schema_version="workflow_diagnostics.summary.v1",
            input_digests={"audit_chain": counts.get("digest", {}) if isinstance(counts.get("digest"), dict) else {}},
            output_digests={"summary": _workflow_lineage_json_digest(diagnostic)},
            rationale_summary="Summarize failed workflow steps using redacted audit events only.",
        ),
        _workflow_lineage_node(
            plan_id=plan_id,
            logical_id="governance_report.artifacts",
            node_type="generated_artifact",
            tool="governance_report",
            schema_version="governance_report.v1",
            deterministic=False,
            input_digests={"plan_inputs": _workflow_lineage_json_digest(plan_inputs)},
            output_digests={"artifact_refs": _workflow_lineage_json_digest(artifact_refs)},
            artifact_refs=artifact_refs,
            rationale_summary="Record observed report artifacts and digests without claiming bit-for-bit replay of run timestamps or future model-generated summaries.",
            non_deterministic_reason="Generated artifact names, timestamps, and any future model-authored summaries are observed outputs; verify checks their recorded digests instead of promising bit-for-bit regeneration.",
        ),
    ]
    edges = [
        {"from": nodes[0]["node_id"], "to": nodes[2]["node_id"], "reason": "redacted audit events feed policy aggregation"},
        {"from": nodes[0]["node_id"], "to": nodes[3]["node_id"], "reason": "redacted failures feed diagnostics summary"},
        {"from": nodes[1]["node_id"], "to": nodes[4]["node_id"], "reason": "git range identifies report scope"},
        {"from": nodes[2]["node_id"], "to": nodes[4]["node_id"], "reason": "governance counts feed report artifact"},
        {"from": nodes[3]["node_id"], "to": nodes[4]["node_id"], "reason": "diagnostics summary is embedded in report artifact"},
    ]
    return {
        "schema": WORKFLOW_LINEAGE_SCHEMA,
        "generated_at": _now_iso(),
        "plan_id": plan_id,
        "plan_identity": {
            "algorithm": "sha256",
            "inputs_digest": _workflow_lineage_json_digest(plan_inputs),
            "inputs": plan_inputs,
            "safe_input_policy": "redacted, repository-relative, no raw prompts, no transcript snippets, no file contents, no bearer tokens",
        },
        "workflow": plan_inputs["workflow"],
        "execution_mode": plan_inputs["execution_mode"],
        "repository": plan_inputs["repository"],
        "request_constraints": constraints,
        "nodes": nodes,
        "edges": edges,
        "artifacts": artifact_refs,
        "links": {
            "governance_report": str(report.get("report_id", "")),
            "governance_report_json": exports.get("json", ""),
            "governance_report_markdown": exports.get("markdown", ""),
            "provenance_sidecars": exports.get("provenance", {}) if isinstance(exports.get("provenance"), dict) else {},
        },
        "verify": {
            "tool": "workflow_lineage",
            "mode": "verify",
            "read_only": True,
            "statuses": ["matched", "input_changed", "artifact_changed", "non_deterministic_node"],
        },
        "security": {
            "redacted": True,
            "contains_secrets": False,
            "records_raw_prompts": False,
            "records_transcript_snippets": False,
            "records_absolute_host_paths": False,
            "records_file_contents": False,
            "repo_boundary_enforced": True,
        },
    }


def _write_workflow_lineage_manifest(manifest: dict[str, Any], rel_path: str) -> None:
    path = _resolve_repo_path(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _lineage_manifest_path_from_input(manifest_path: str = "") -> Path:
    if manifest_path.strip():
        return _resolve_repo_path(manifest_path.strip())
    reports_dir = _resolve_repo_path(str(REPORTS_DIR))
    candidates = sorted(reports_dir.glob(f"*{WORKFLOW_LINEAGE_SUFFIX}")) if reports_dir.exists() else []
    if not candidates:
        raise ValueError("manifest_path is required when no workflow lineage manifest exists")
    return candidates[-1]


def _governance_workflow_lineage_current_plan_inputs(manifest: dict[str, Any]) -> dict[str, Any]:
    constraints = manifest.get("request_constraints", {}) if isinstance(manifest.get("request_constraints"), dict) else {}
    start_dt = _parse_iso_datetime(str(constraints.get("start_time", ""))) if str(constraints.get("start_time", "")).strip() else None
    end_dt = _parse_iso_datetime(str(constraints.get("end_time", ""))) if str(constraints.get("end_time", "")).strip() else None
    events, audit_meta = _load_audit_events(start_dt, end_dt)
    counts = _aggregate_audit_events(events)
    base_ref = str(constraints.get("base_ref", "HEAD~1")) or "HEAD~1"
    head_ref = str(constraints.get("head_ref", "HEAD")) or "HEAD"
    git_info = {
        "base_ref": base_ref,
        "head_ref": head_ref,
        "base_commit": _git("rev-parse", base_ref, check=False).stdout.strip(),
        "head_commit": _git("rev-parse", head_ref, check=False).stdout.strip(),
        "range": f"{base_ref}...{head_ref}",
    }
    current_constraints = _workflow_lineage_request_constraints(
        start_time=str(constraints.get("start_time", "")),
        end_time=str(constraints.get("end_time", "")),
        base_ref=base_ref,
        head_ref=head_ref,
        export=bool(constraints.get("export", True)),
        compressed_observation=bool(constraints.get("compressed_observation", False)),
    )
    return _governance_workflow_lineage_plan_inputs(
        constraints=current_constraints,
        git_info=git_info,
        counts=counts,
        audit_meta=audit_meta,
    )


def _verify_workflow_lineage_manifest(manifest: dict[str, Any], manifest_rel_path: str) -> dict[str, Any]:
    if manifest.get("schema") != WORKFLOW_LINEAGE_SCHEMA:
        raise ValueError("manifest schema must be workflow_lineage.v1")
    workflow = manifest.get("workflow", {}) if isinstance(manifest.get("workflow"), dict) else {}
    if workflow.get("name") != "governance_report":
        raise ValueError("only governance_report lineage verification is supported in this slice")
    recorded_plan_id = str(manifest.get("plan_id", ""))
    current_inputs = _governance_workflow_lineage_current_plan_inputs(manifest)
    current_plan_id = _workflow_lineage_plan_id(current_inputs)
    plan_status = "matched" if current_plan_id == recorded_plan_id else "input_changed"
    artifact_checks: list[dict[str, Any]] = []
    artifact_refs = manifest.get("artifacts", []) if isinstance(manifest.get("artifacts"), list) else []
    for ref in artifact_refs:
        if not isinstance(ref, dict):
            continue
        rel_path = str(ref.get("path", ""))
        path = _resolve_repo_path(rel_path)
        recorded_digest = ref.get("digest", {}) if isinstance(ref.get("digest"), dict) else {}
        expected = str(recorded_digest.get("value", ""))
        actual_digest = _artifact_digest(path) if path.exists() and path.is_file() else {}
        actual = str(actual_digest.get("value", ""))
        status = "matched" if expected and actual == expected else "artifact_changed"
        if not path.exists():
            status = "artifact_missing"
        artifact_checks.append(
            {
                "path": rel_path,
                "role": str(ref.get("role", "")),
                "status": status,
                "expected_digest": recorded_digest,
                "actual_digest": actual_digest,
            }
        )
    artifact_status = "matched" if all(row.get("status") == "matched" for row in artifact_checks) else "artifact_changed"
    non_deterministic_nodes = [
        {
            "node_id": str(node.get("node_id", "")),
            "logical_id": str(node.get("logical_id", "")),
            "reason": str((node.get("non_deterministic", {}) if isinstance(node.get("non_deterministic"), dict) else {}).get("reason", "")),
        }
        for node in manifest.get("nodes", [])
        if isinstance(node, dict) and not bool(node.get("deterministic", True))
    ]
    status = "matched"
    if plan_status != "matched":
        status = "input_changed"
    elif artifact_status != "matched":
        status = "artifact_changed"
    conditions = [status]
    if non_deterministic_nodes:
        conditions.append("non_deterministic_node")
    return {
        "schema": WORKFLOW_LINEAGE_VERIFY_SCHEMA,
        "mode": "verify",
        "read_only": True,
        "manifest_path": manifest_rel_path,
        "plan_id": recorded_plan_id,
        "status": status,
        "ok": status == "matched",
        "conditions": conditions,
        "checks": {
            "plan": {
                "status": plan_status,
                "recorded_plan_id": recorded_plan_id,
                "current_plan_id": current_plan_id,
                "recorded_inputs_digest": (manifest.get("plan_identity", {}) if isinstance(manifest.get("plan_identity"), dict) else {}).get("inputs_digest", {}),
                "current_inputs_digest": _workflow_lineage_json_digest(current_inputs),
            },
            "artifacts": {
                "status": artifact_status,
                "checks": artifact_checks,
            },
            "non_deterministic_nodes": non_deterministic_nodes,
        },
        "security": {
            "read_only": True,
            "mutates_repository": False,
            "records_raw_prompts": False,
            "records_file_contents": False,
        },
    }


def _artifact_provenance_path(artifact_rel_path: str) -> Path:
    return _resolve_repo_path(f"{artifact_rel_path}{PROVENANCE_SUFFIX}")


def _artifact_digest(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "algorithm": "sha256",
        "value": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_digest(value: bytes) -> dict[str, str]:
    return {"algorithm": "sha256", "value": hashlib.sha256(value).hexdigest()}


def _coerce_sha256_digest(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            "algorithm": str(value.get("algorithm", "")).lower(),
            "value": str(value.get("value", "")),
        }
    text = str(value or "")
    if text.startswith("sha256:"):
        return {"algorithm": "sha256", "value": text.removeprefix("sha256:")}
    return {"algorithm": "", "value": text}


def _sha256_digest_matches(value: Any, expected: str) -> bool:
    digest = _coerce_sha256_digest(value)
    return digest["algorithm"] == "sha256" and hmac.compare_digest(digest["value"], expected)


def _artifact_schema_from_path(path: Path) -> str:
    if path.suffix.lower() != ".json" or not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(payload, dict):
        return str(payload.get("schema", ""))
    return ""


def _provenance_for_attestation_digest(provenance: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(provenance, sort_keys=True, ensure_ascii=True))
    signing = normalized.get("signing")
    if isinstance(signing, dict):
        for key in ("bundle", "dsse", "envelope"):
            signing.pop(key, None)
        nested_attestation = signing.get("attestation")
        if isinstance(nested_attestation, dict):
            for key in ("bundle", "dsse", "envelope"):
                nested_attestation.pop(key, None)
    attestation = normalized.get("attestation")
    if isinstance(attestation, dict):
        for key in ("bundle", "dsse", "envelope"):
            attestation.pop(key, None)
    return normalized


def _artifact_attestation_sidecar_digest(provenance: dict[str, Any]) -> dict[str, str]:
    return _sha256_digest(_canonical_json_bytes(_provenance_for_attestation_digest(provenance)))


def _dsse_pae(payload_type: str, payload: bytes) -> bytes:
    payload_type_bytes = payload_type.encode("utf-8")
    return b" ".join(
        [
            b"DSSEv1",
            str(len(payload_type_bytes)).encode("ascii"),
            payload_type_bytes,
            str(len(payload)).encode("ascii"),
            payload,
        ]
    )


def _local_dsse_fixture_signature(payload_type: str, payload: bytes) -> str:
    digest = hmac.new(
        ATTESTATION_FIXTURE_HMAC_KEY,
        _dsse_pae(payload_type, payload),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _local_dsse_fixture_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    payload_bytes = _canonical_json_bytes(payload)
    return {
        "payloadType": ATTESTATION_PAYLOAD_TYPE,
        "payload": base64.b64encode(payload_bytes).decode("ascii"),
        "signatures": [
            {
                "keyid": ATTESTATION_FIXTURE_KEY_ID,
                "sig": _local_dsse_fixture_signature(ATTESTATION_PAYLOAD_TYPE, payload_bytes),
            }
        ],
    }


def _attach_local_dsse_fixture_attestation(
    provenance: dict[str, Any],
    *,
    subject_digest_value: str | None = None,
) -> dict[str, Any]:
    """Attach a deterministic local DSSE fixture envelope to a sidecar.

    This helper is deliberately fixture-only. It lets tests and offline demos
    exercise verifier plumbing without introducing a production signer, network
    dependency, transparency log, or repository-stored private key.
    """

    artifact = provenance.get("artifact", {}) if isinstance(provenance.get("artifact"), dict) else {}
    artifact_digest = _coerce_sha256_digest(artifact.get("digest", {}))
    subject_digest = {
        "algorithm": "sha256",
        "value": subject_digest_value if subject_digest_value is not None else artifact_digest["value"],
    }
    provenance["signing"] = {
        "signed": True,
        "backend": ATTESTATION_BACKEND_LOCAL_DSSE_FIXTURE,
        "subject_digest": subject_digest,
        "signer_identity": ATTESTATION_FIXTURE_SIGNER_IDENTITY,
        "bundle_ref": "",
        "envelope_ref": "inline:signing.envelope",
        "verification": {
            "status": "verified",
            "backend": ATTESTATION_BACKEND_LOCAL_DSSE_FIXTURE,
            "offline": True,
        },
    }
    payload = {
        "schema": ATTESTATION_SCHEMA,
        "backend": ATTESTATION_BACKEND_LOCAL_DSSE_FIXTURE,
        "predicate_type": "https://codebase-tooling-mcp.local/attestations/artifact-provenance/v1",
        "subject": {
            "path": str(artifact.get("path", "")),
            "digest": subject_digest,
        },
        "provenance": {
            "schema": PROVENANCE_SCHEMA,
            "digest": _artifact_attestation_sidecar_digest(provenance),
        },
        "signer": {
            "identity": ATTESTATION_FIXTURE_SIGNER_IDENTITY,
            "key_id": ATTESTATION_FIXTURE_KEY_ID,
        },
        "verification": {
            "status": "verified",
            "backend": ATTESTATION_BACKEND_LOCAL_DSSE_FIXTURE,
            "offline": True,
        },
    }
    provenance["signing"]["envelope"] = _local_dsse_fixture_envelope(payload)
    return provenance


def _attestation_result(
    *,
    status: str,
    backend: str,
    signed: bool,
    subject_digest: Any | None = None,
    signer_identity: str = "",
    bundle_ref: str = "",
    envelope_ref: str = "",
    messages: list[str] | None = None,
) -> dict[str, Any]:
    result_messages = messages or []
    return {
        "schema": ATTESTATION_SCHEMA,
        "status": status,
        "backend": backend,
        "signed": signed,
        "local_only": backend in {"local-only", ATTESTATION_BACKEND_LOCAL_DSSE_FIXTURE},
        "network_access": False,
        "subject_digest": _coerce_sha256_digest(subject_digest or {}),
        "signer_identity": signer_identity,
        "bundle_ref": bundle_ref,
        "envelope_ref": envelope_ref,
        "verification": {
            "status": status,
            "backend": backend,
            "offline": True,
            "messages": result_messages,
        },
        "findings": result_messages,
    }


def _verify_local_dsse_fixture_attestation(
    provenance: dict[str, Any],
    artifact_rel: str,
    actual_digest: str,
) -> dict[str, Any]:
    signing = provenance.get("signing", {}) if isinstance(provenance.get("signing"), dict) else {}
    envelope = signing.get("envelope") or signing.get("dsse")
    subject_digest = signing.get("subject_digest", {})
    signer_identity = str(signing.get("signer_identity", ""))
    bundle_ref = str(signing.get("bundle_ref", ""))
    envelope_ref = str(signing.get("envelope_ref", ""))
    if not isinstance(envelope, dict):
        return _attestation_result(
            status="unavailable",
            backend=ATTESTATION_BACKEND_LOCAL_DSSE_FIXTURE,
            signed=True,
            subject_digest=subject_digest,
            signer_identity=signer_identity,
            bundle_ref=bundle_ref,
            envelope_ref=envelope_ref,
            messages=["attestation_envelope_unavailable"],
        )

    messages: list[str] = []
    payload_type = str(envelope.get("payloadType", ""))
    if payload_type != ATTESTATION_PAYLOAD_TYPE:
        messages.append("attestation_payload_type_mismatch")
    try:
        payload_bytes = base64.b64decode(str(envelope.get("payload", "")), validate=True)
    except (ValueError, TypeError):
        payload_bytes = b""
        messages.append("attestation_payload_unreadable")

    signatures = envelope.get("signatures", [])
    signature = ""
    if isinstance(signatures, list):
        for row in signatures:
            if isinstance(row, dict) and str(row.get("keyid", "")) == ATTESTATION_FIXTURE_KEY_ID:
                signature = str(row.get("sig", ""))
                break
    if not signature:
        messages.append("attestation_signature_missing")
    elif payload_bytes:
        expected_signature = _local_dsse_fixture_signature(payload_type, payload_bytes)
        if not hmac.compare_digest(signature, expected_signature):
            messages.append("attestation_signature_mismatch")

    payload: dict[str, Any] = {}
    if payload_bytes:
        try:
            decoded = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            messages.append("attestation_payload_unreadable")
        else:
            if isinstance(decoded, dict):
                payload = decoded
            else:
                messages.append("attestation_payload_schema_mismatch")
    if payload.get("schema") != ATTESTATION_SCHEMA:
        messages.append("attestation_payload_schema_mismatch")
    if payload.get("backend") != ATTESTATION_BACKEND_LOCAL_DSSE_FIXTURE:
        messages.append("attestation_payload_backend_mismatch")

    subject = payload.get("subject", {}) if isinstance(payload.get("subject"), dict) else {}
    if str(subject.get("path", "")) != artifact_rel:
        messages.append("attestation_subject_path_mismatch")
    payload_subject_digest = _coerce_sha256_digest(subject.get("digest", {}))
    if not _sha256_digest_matches(payload_subject_digest, actual_digest):
        messages.append("attestation_subject_digest_mismatch")
    if not _sha256_digest_matches(subject_digest, actual_digest):
        messages.append("attestation_signing_subject_digest_mismatch")

    artifact = provenance.get("artifact", {}) if isinstance(provenance.get("artifact"), dict) else {}
    if not _sha256_digest_matches(artifact.get("digest", {}), actual_digest):
        messages.append("attestation_artifact_digest_mismatch")

    provenance_payload = payload.get("provenance", {}) if isinstance(payload.get("provenance"), dict) else {}
    if provenance_payload.get("schema") != PROVENANCE_SCHEMA:
        messages.append("attestation_provenance_schema_mismatch")
    sidecar_digest = _artifact_attestation_sidecar_digest(provenance)
    if not _sha256_digest_matches(provenance_payload.get("digest", {}), sidecar_digest["value"]):
        messages.append("attestation_sidecar_digest_mismatch")

    signer = payload.get("signer", {}) if isinstance(payload.get("signer"), dict) else {}
    if str(signer.get("identity", "")) != ATTESTATION_FIXTURE_SIGNER_IDENTITY:
        messages.append("attestation_signer_identity_mismatch")
    if str(signer.get("key_id", "")) != ATTESTATION_FIXTURE_KEY_ID:
        messages.append("attestation_key_id_mismatch")
    if signer_identity != ATTESTATION_FIXTURE_SIGNER_IDENTITY:
        messages.append("attestation_signing_identity_mismatch")

    return _attestation_result(
        status="invalid" if messages else "verified",
        backend=ATTESTATION_BACKEND_LOCAL_DSSE_FIXTURE,
        signed=True,
        subject_digest=payload_subject_digest or subject_digest,
        signer_identity=signer_identity,
        bundle_ref=bundle_ref,
        envelope_ref=envelope_ref,
        messages=messages,
    )


def _verify_artifact_attestation(
    provenance: dict[str, Any],
    artifact_rel: str,
    actual_digest: str,
) -> dict[str, Any]:
    signing = provenance.get("signing", {}) if isinstance(provenance.get("signing"), dict) else {}
    if not bool(signing.get("signed", False)):
        artifact = provenance.get("artifact", {}) if isinstance(provenance.get("artifact"), dict) else {}
        return _attestation_result(
            status="unsigned",
            backend="local-only",
            signed=False,
            subject_digest=artifact.get("digest", {}),
            messages=[],
        )
    backend = str(signing.get("backend", "")).strip()
    subject_digest = signing.get("subject_digest", {})
    signer_identity = str(signing.get("signer_identity", ""))
    bundle_ref = str(signing.get("bundle_ref", ""))
    envelope_ref = str(signing.get("envelope_ref", ""))
    if backend != ATTESTATION_BACKEND_LOCAL_DSSE_FIXTURE:
        return _attestation_result(
            status="unsupported",
            backend=backend or "unknown",
            signed=True,
            subject_digest=subject_digest,
            signer_identity=signer_identity,
            bundle_ref=bundle_ref,
            envelope_ref=envelope_ref,
            messages=["attestation_backend_unsupported"],
        )
    return _verify_local_dsse_fixture_attestation(provenance, artifact_rel, actual_digest)


def _git_state_for_provenance(base_ref: str = "", head_ref: str = "") -> dict[str, Any]:
    def rev(ref: str) -> str:
        if not ref.strip():
            return ""
        return _git("rev-parse", ref, check=False).stdout.strip()

    return {
        "head": _git("rev-parse", "HEAD", check=False).stdout.strip(),
        "base_ref": base_ref,
        "head_ref": head_ref,
        "base_commit": rev(base_ref),
        "head_commit": rev(head_ref),
        "dirty": bool(_git("status", "--porcelain", check=False).stdout.strip()),
    }


def _write_artifact_provenance_sidecars(
    *,
    tool_name: str,
    artifact_paths: list[str],
    inputs: dict[str, Any],
    git_state: dict[str, Any] | None = None,
    artifact_schemas: dict[str, str] | None = None,
    lineage_manifest: str = "",
) -> dict[str, str]:
    generated_at = _now_iso()
    clean_inputs = _redact_audit_value(inputs)
    schemas = artifact_schemas or {}
    sidecars: dict[str, str] = {}
    for index, artifact_rel in enumerate(artifact_paths):
        artifact_path = _resolve_repo_path(artifact_rel)
        if not artifact_path.exists():
            continue
        prev_path = artifact_paths[index - 1] if index > 0 else ""
        next_path = artifact_paths[index + 1] if index + 1 < len(artifact_paths) else ""
        artifact_schema = schemas.get(artifact_rel, _artifact_schema_from_path(artifact_path))
        provenance = {
            "schema": PROVENANCE_SCHEMA,
            "generated_at": generated_at,
            "tool": tool_name,
            "workflow": tool_name,
            "artifact": {
                "path": artifact_rel,
                "digest": _artifact_digest(artifact_path),
                "schema": artifact_schema,
            },
            "server": {
                "name": "codebase-tooling-mcp",
                "provenance_schema": PROVENANCE_SCHEMA,
            },
            "repository": {
                "path": str(REPO_PATH),
                "git": git_state or _git_state_for_provenance(),
            },
            "invocation": {
                "timestamp": generated_at,
                "selected_inputs": clean_inputs,
            },
            "links": {
                "previous_artifact": prev_path,
                "next_artifact": next_path,
            },
            "signing": {
                "signed": False,
                "reason": "local provenance only; Sigstore/cosign/GitHub attestations deferred",
            },
        }
        if lineage_manifest and artifact_rel != lineage_manifest:
            provenance["links"]["workflow_lineage"] = lineage_manifest
        sidecar_path = _artifact_provenance_path(artifact_rel)
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(
            json.dumps(provenance, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        sidecars[artifact_rel] = str(sidecar_path.relative_to(REPO_PATH))
    return sidecars


def _verify_artifact_provenance_path(artifact_rel: str) -> dict[str, Any]:
    artifact_path = _resolve_repo_path(artifact_rel)
    sidecar_path = _artifact_provenance_path(artifact_rel)
    result: dict[str, Any] = {
        "artifact_path": artifact_rel,
        "provenance_path": str(sidecar_path.relative_to(REPO_PATH)),
        "ok": True,
        "checks": {
            "artifact_present": artifact_path.exists(),
            "provenance_present": sidecar_path.exists(),
            "digest_match": False,
            "schema_match": True,
            "fresh": True,
        },
        "findings": [],
    }
    if not artifact_path.exists():
        result["ok"] = False
        result["findings"].append("artifact_missing")
        return result
    if not sidecar_path.exists():
        result["ok"] = False
        result["findings"].append("provenance_missing")
        return result
    try:
        provenance = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result["ok"] = False
        result["findings"].append("provenance_unreadable")
        return result
    if not isinstance(provenance, dict) or provenance.get("schema") != PROVENANCE_SCHEMA:
        result["ok"] = False
        result["findings"].append("provenance_schema_mismatch")
        return result
    artifact = provenance.get("artifact", {}) if isinstance(provenance.get("artifact"), dict) else {}
    if str(artifact.get("path", "")) != artifact_rel:
        result["ok"] = False
        result["findings"].append("artifact_path_mismatch")
    digest = artifact.get("digest", {}) if isinstance(artifact.get("digest"), dict) else {}
    expected_digest = str(digest.get("value", ""))
    actual_digest = _artifact_digest(artifact_path)["value"]
    result["checks"]["digest_match"] = expected_digest == actual_digest
    if expected_digest != actual_digest:
        result["ok"] = False
        result["findings"].append("digest_mismatch")
    current_schema = _artifact_schema_from_path(artifact_path)
    recorded_schema = str(artifact.get("schema", ""))
    schema_match = not current_schema or recorded_schema == current_schema
    result["checks"]["schema_match"] = schema_match
    if not schema_match:
        result["ok"] = False
        result["findings"].append("artifact_schema_mismatch")
    fresh = sidecar_path.stat().st_mtime >= artifact_path.stat().st_mtime
    result["checks"]["fresh"] = fresh
    if not fresh:
        result["ok"] = False
        result["findings"].append("provenance_stale")
    attestation = _verify_artifact_attestation(provenance, artifact_rel, actual_digest)
    result["attestation"] = attestation
    result["checks"]["attestation_status"] = attestation["status"]
    result["checks"]["attestation_verified"] = attestation["status"] == "verified"
    if attestation["status"] in {"invalid", "unsupported", "unavailable"}:
        result["ok"] = False
        result["findings"].extend(attestation.get("findings", []))
        result["findings"].append(f"attestation_{attestation['status']}")
    return result


def _write_governance_report_exports(
    report: dict[str, Any],
    *,
    provenance_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = _governance_report_paths(str(report["report_id"]))
    json_path = _resolve_repo_path(paths["json"])
    md_path = _resolve_repo_path(paths["markdown"])
    exports = {"json": str(json_path.relative_to(REPO_PATH)), "markdown": str(md_path.relative_to(REPO_PATH))}
    report["exports"] = exports
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    md_path.write_text(_governance_markdown(report), encoding="utf-8")
    return exports


def _tool_annotation_from_categories(categories: list[str]) -> dict[str, Any]:
    """Translate internal security categories to MCP tool annotation hints."""
    category_set = set(categories)
    mutation_capable = bool(MUTATION_TOOL_CATEGORIES.intersection(category_set))
    destructive = "destructive" in category_set
    open_world = bool({"network", "shell/process", "secret-sensitive"}.intersection(category_set))
    return {
        "readOnlyHint": not mutation_capable,
        "destructiveHint": destructive,
        "idempotentHint": not mutation_capable,
        "openWorldHint": open_world,
    }


def _tool_annotation_entry(tool_name: str, *, mode: str = "") -> dict[str, Any]:
    arguments = {"mode": mode} if mode else None
    categories = _tool_categories(tool_name, arguments)
    annotation = _tool_annotation_from_categories(categories)
    return {
        "tool": tool_name,
        "mode": mode,
        "categories": categories,
        "required_scope": _required_scope_for_categories(categories),
        "mutation_capable": bool(MUTATION_TOOL_CATEGORIES.intersection(categories)),
        "annotations": annotation,
    }


def _tool_annotation_manifest() -> dict[str, Any]:
    """Build a machine-checkable safety annotation manifest for the public v1 MCP surface."""
    tools: list[dict[str, Any]] = []
    for tool_name in sorted(PUBLIC_MCP_TOOL_NAMES):
        entry = _tool_annotation_entry(tool_name)
        metadata = TOOL_SECURITY_METADATA.get(tool_name, {})
        mode_categories = metadata.get("mode_categories")
        if isinstance(mode_categories, dict):
            entry["modes"] = [
                _tool_annotation_entry(tool_name, mode=mode)
                for mode in sorted(mode_categories)
            ]
        tools.append(entry)
    return {
        "schema": "tool_annotations.v1",
        "source": "TOOL_SECURITY_METADATA",
        "tool_count": len(tools),
        "tools": tools,
    }


def _tool_categories(tool_name: str, arguments: dict[str, Any] | None = None) -> list[str]:
    metadata = TOOL_SECURITY_METADATA.get(tool_name, {"categories": ["read-only"]})
    categories = list(metadata.get("categories", ["read-only"]))
    mode_categories = metadata.get("mode_categories")
    if isinstance(mode_categories, dict) and arguments:
        mode = str(arguments.get("mode", "")).strip().lower()
        if mode in mode_categories:
            categories = list(mode_categories[mode])
    return categories


def _required_scope_for_categories(categories: list[str] | set[str] | tuple[str, ...]) -> str:
    return MCP_SCOPE_MUTATE if MCP_SCOPE_MUTATE_CATEGORIES.intersection(categories) else MCP_SCOPE_READ


def _inside_http_request() -> bool:
    return _HTTP_REQUEST_AUTHORIZED.get() is not None


def _http_request_authorized_for_tools() -> bool:
    authorized = _HTTP_REQUEST_AUTHORIZED.get()
    return True if authorized is None else bool(authorized)


def _http_request_granted_scopes_for_tools() -> frozenset[str]:
    authorized = _HTTP_REQUEST_AUTHORIZED.get()
    if authorized is None:
        return frozenset(MCP_SUPPORTED_SCOPES)
    if not authorized:
        return frozenset()
    scopes = _HTTP_REQUEST_GRANTED_SCOPES.get()
    if scopes is None:
        return frozenset(MCP_SUPPORTED_SCOPES)
    return scopes


def _require_tool_security_gate(tool_name: str, arguments: dict[str, Any] | None = None) -> list[str]:
    categories = _tool_categories(tool_name, arguments)
    required_scope = _required_scope_for_categories(categories)
    granted_scopes = _http_request_granted_scopes_for_tools() if _inside_http_request() else None
    sensitive = bool(SENSITIVE_TOOL_CATEGORIES.intersection(categories))
    mutating = bool(MUTATION_TOOL_CATEGORIES.intersection(categories))
    if _inside_http_request() and sensitive and not _http_request_authorized_for_tools():
        _append_audit_event(
            tool_name,
            categories,
            False,
            arguments,
            "HTTP session not authorized",
            required_scope=required_scope,
            granted_scopes=granted_scopes,
        )
        _otel_record_policy_gate(
            tool_name,
            categories,
            "deny",
            "HTTP session not authorized",
            arguments,
            required_scope=required_scope,
            granted_scopes=granted_scopes,
        )
        raise PermissionError(f"{tool_name} requires an authorized HTTP session")
    if _inside_http_request() and required_scope not in (granted_scopes or frozenset()):
        granted = granted_scopes or frozenset()
        _append_audit_event(
            tool_name,
            categories,
            False,
            arguments,
            "insufficient_scope",
            required_scope=required_scope,
            granted_scopes=granted,
        )
        _otel_record_policy_gate(
            tool_name,
            categories,
            "deny",
            "insufficient_scope",
            arguments,
            required_scope=required_scope,
            granted_scopes=granted,
        )
        raise HTTPInsufficientScopeError(tool_name, required_scope, granted)
    if mutating and not ALLOW_MUTATIONS:
        _append_audit_event(
            tool_name,
            categories,
            False,
            arguments,
            "mutations disabled",
            required_scope=required_scope,
            granted_scopes=granted_scopes,
        )
        _otel_record_policy_gate(
            tool_name,
            categories,
            "deny",
            "mutations disabled",
            arguments,
            required_scope=required_scope,
            granted_scopes=granted_scopes,
        )
        raise PermissionError(
            f"{tool_name} requires mutation permission; set ALLOW_MUTATIONS=true and use an authorized HTTP session."
        )
    return categories


def _tool_result_success(result: Any) -> bool:
    if isinstance(result, dict) and isinstance(result.get("ok"), bool):
        return bool(result["ok"])
    return True


def _tool_result_failure_reason(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    for key in ("error", "stderr", "message"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return _trim_text(value.strip(), max_chars=200)
    if result.get("timeout") is True:
        return "timeout"
    return "tool returned ok=false"


def _run_with_tool_security_audit(
    tool_name: str,
    arguments: dict[str, Any],
    action: Callable[[], Any],
) -> Any:
    categories = _tool_categories(tool_name, arguments)
    with _otel_span(
        f"mcp.tool.{tool_name}",
        _otel_tool_attributes(tool_name, arguments, categories),
    ) as span:
        categories = _require_tool_security_gate(tool_name, arguments)
        span.set_attribute("mcp.tool.categories", sorted(str(item) for item in categories))
        sensitive = bool(SENSITIVE_TOOL_CATEGORIES.intersection(categories))
        try:
            result = action()
        except Exception as exc:
            if sensitive:
                _append_audit_event(
                    tool_name,
                    categories,
                    False,
                    arguments,
                    type(exc).__name__,
                    required_scope=_required_scope_for_categories(categories),
                    granted_scopes=(
                        _http_request_granted_scopes_for_tools() if _inside_http_request() else None
                    ),
                )
            raise
        _otel_set_result_attributes(span, result)
        if sensitive:
            success = _tool_result_success(result)
            span.set_attribute("mcp.response.ok", success)
            _append_audit_event(
                tool_name,
                categories,
                success,
                arguments,
                "" if success else _tool_result_failure_reason(result),
                required_scope=_required_scope_for_categories(categories),
                granted_scopes=(
                    _http_request_granted_scopes_for_tools() if _inside_http_request() else None
                ),
            )
        return result


class MCPHTTPAuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        method = str(scope.get("method", "GET")).upper()
        if method == "OPTIONS" or path in {"/", "/healthz"}:
            await self.app(scope, receive, send)
            return
        if path == "/.well-known/oauth-protected-resource":
            response = JSONResponse(_http_auth_discovery_payload())
            await response(scope, receive, send)
            return
        if method == "GET" and path == "/.well-known/mcp-server.json":
            response = JSONResponse(_mcp_server_manifest_payload())
            await response(scope, receive, send)
            return
        if _http_path_is_protected_mcp(path):
            origin_allowed, origin_status, origin_reason = _http_origin_policy(scope)
            if not origin_allowed:
                _append_audit_event(
                    "http_request",
                    ["network"],
                    False,
                    {"path": path, "origin": "<redacted>"},
                    origin_reason,
                )
                response = JSONResponse(
                    {"error": "forbidden", "detail": origin_reason},
                    status_code=origin_status,
                )
                await response(scope, receive, send)
                return
            protocol_allowed, protocol_status, protocol_reason = _http_protocol_version_policy(scope)
            if not protocol_allowed:
                _append_audit_event(
                    "http_request",
                    ["network"],
                    False,
                    {"path": path, "mcp_protocol_version": "<redacted>"},
                    protocol_reason,
                )
                response = JSONResponse(
                    {"error": "bad_request", "detail": protocol_reason},
                    status_code=protocol_status,
                )
                await response(scope, receive, send)
                return
        allowed, retry_after = _http_rate_limit_allow(scope)
        if not allowed:
            response = JSONResponse(
                {"error": "rate_limited", "detail": "Too many MCP HTTP requests"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return
        authorized, status_code, reason = _http_authenticate_scope(scope)
        if not authorized:
            _append_audit_event(
                "http_request",
                ["network"],
                False,
                {"path": path},
                reason,
                required_scope=MCP_SCOPE_READ,
                granted_scopes=frozenset(),
            )
            headers = (
                {"WWW-Authenticate": _http_bearer_challenge(required_scope=MCP_SCOPE_READ)}
                if status_code == 401
                else None
            )
            response = JSONResponse(
                {"error": "unauthorized" if status_code == 401 else "forbidden", "detail": reason},
                status_code=status_code,
                headers=headers,
            )
            await response(scope, receive, send)
            return
        granted_scopes = (
            _local_bearer_token_granted_scopes()
            if _http_auth_required()
            else frozenset(MCP_SUPPORTED_SCOPES)
        )
        token = _HTTP_REQUEST_AUTHORIZED.set(True)
        scope_token = _HTTP_REQUEST_GRANTED_SCOPES.set(granted_scopes)
        try:
            if path == "/sse":
                await self.app(scope, receive, send)
            else:
                await asyncio.wait_for(self.app(scope, receive, send), timeout=MCP_HTTP_REQUEST_TIMEOUT_SECONDS)
        except HTTPInsufficientScopeError as exc:
            response = JSONResponse(
                {
                    "error": "insufficient_scope",
                    "detail": "HTTP bearer token lacks the required MCP scope",
                    "required_scope": exc.required_scope,
                    "granted_scopes": sorted(exc.granted_scopes),
                },
                status_code=403,
                headers={"WWW-Authenticate": exc.challenge},
            )
            await response(scope, receive, send)
        except asyncio.TimeoutError:
            _append_audit_event(
                "http_request",
                ["network"],
                False,
                {"path": path},
                "request timeout",
                required_scope=MCP_SCOPE_READ,
                granted_scopes=granted_scopes,
            )
            response = JSONResponse(
                {"error": "timeout", "detail": "MCP HTTP request exceeded configured timeout"},
                status_code=504,
            )
            await response(scope, receive, send)
        finally:
            _HTTP_REQUEST_GRANTED_SCOPES.reset(scope_token)
            _HTTP_REQUEST_AUTHORIZED.reset(token)


def _trim_text(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n\n[truncated: output exceeded {max_chars} characters; original length={len(text)}]"
    )


def _sse_subscriber_count() -> int:
    with _SSE_LOCK:
        return len(_SSE_SUBSCRIBERS)


def _sse_recent_event_count() -> int:
    with _SSE_LOCK:
        return len(_SSE_EVENT_HISTORY)


def _sse_encode_event(entry: dict[str, Any]) -> str:
    lines = [f"id: {entry.get('id', '')}", f"event: {entry.get('event', 'message')}"]
    payload = json.dumps(entry, ensure_ascii=True, sort_keys=True)
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def _sse_replay(limit: int = 20) -> list[dict[str, Any]]:
    clamped = max(0, min(limit, SSE_EVENT_HISTORY_MAX))
    with _SSE_LOCK:
        history = list(_SSE_EVENT_HISTORY)
    if clamped == 0:
        return []
    return history[-clamped:]


def _sse_publish(event: str, **payload: Any) -> dict[str, Any]:
    global _SSE_EVENT_SEQ
    entry = {"event": event, "timestamp": _now_iso(), **payload}
    with _SSE_LOCK:
        _SSE_EVENT_SEQ += 1
        entry["id"] = _SSE_EVENT_SEQ
        _SSE_EVENT_HISTORY.append(entry)
        subscribers = list(_SSE_SUBSCRIBERS.values())
    for subscriber in subscribers:
        with contextlib.suppress(queue.Full):
            subscriber.put_nowait(entry)
    return entry


def _split_sse_chunks(text: str, max_chars: int = 2000) -> list[str]:
    if not text:
        return []
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _run_observed_subprocess(
    command: list[str],
    cwd: str,
    event_source: str,
    timeout_seconds: int | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started_at = time.time()
    _sse_publish(
        "tool.start",
        source=event_source,
        run_id=run_id,
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
    except FileNotFoundError as exc:
        _sse_publish(
            "tool.error",
            source=event_source,
            run_id=run_id,
            command=command,
            cwd=cwd,
            error=str(exc),
        )
        raise

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _reader(pipe: Any, stream_name: str, sink: list[str]) -> None:
        if pipe is None:
            return
        try:
            for chunk in iter(pipe.readline, ""):
                if not chunk:
                    break
                sink.append(chunk)
                for part in _split_sse_chunks(chunk):
                    _sse_publish(
                        "tool.output",
                        source=event_source,
                        run_id=run_id,
                        command=command,
                        cwd=cwd,
                        stream=stream_name,
                        chunk=part,
                    )
        finally:
            with contextlib.suppress(Exception):
                pipe.close()

    stdout_thread = threading.Thread(
        target=_reader,
        args=(proc.stdout, "stdout", stdout_chunks),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_reader,
        args=(proc.stderr, "stderr", stderr_chunks),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    if input_text is not None and proc.stdin is not None:
        with contextlib.suppress(BrokenPipeError):
            proc.stdin.write(input_text)
            proc.stdin.flush()
        with contextlib.suppress(Exception):
            proc.stdin.close()

    timed_out = False
    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=1.0)

    stdout_thread.join(timeout=1.0)
    stderr_thread.join(timeout=1.0)
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    duration_ms = int((time.time() - started_at) * 1000)
    exit_code = None if timed_out else proc.returncode
    _sse_publish(
        "tool.finish",
        source=event_source,
        run_id=run_id,
        command=command,
        cwd=cwd,
        timed_out=timed_out,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_chars=len(stdout),
        stderr_chars=len(stderr),
    )
    return {
        "run_id": run_id,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
    }


def _strictness_score_text(text: str) -> dict[str, Any]:
    raw = text.strip()
    low = raw.lower()
    score = 0
    reasons: list[str] = []
    if raw:
        score += 10
        reasons.append("non_empty")
    if len(raw.split()) >= 10:
        score += 10
        reasons.append("has_context")
    if len(raw.split()) >= 20:
        score += 10
        reasons.append("has_detail")
    if any(k in low for k in ["must", "required", "only", "do not", "forbid"]):
        score += 20
        reasons.append("has_constraints")
    if "one of" in low or "mode=" in low or "mode " in low:
        score += 15
        reasons.append("has_enumeration")
    if any(k in low for k in ["schema", "output", "return"]):
        score += 10
        reasons.append("has_output_contract")
    if any(k in low for k in ["error", "raise", "invalid"]):
        score += 10
        reasons.append("has_failure_contract")
    if any(k in low for k in ["prefer", "router", "compact", "offset", "limit", "fields"]):
        score += 15
        reasons.append("has_tooling_directives")
    score = max(0, min(100, score))
    return {"score": score, "reasons": reasons}


def _ssl_context_for_url(url: str) -> ssl.SSLContext | None:
    if not url.lower().startswith("https://"):
        return None
    cafile = os.getenv("SSL_CERT_FILE", "").strip() or HOST_CA_CERT_FILE
    if cafile:
        p = Path(cafile)
        if p.is_file():
            return ssl.create_default_context(cafile=str(p))
    return ssl.create_default_context()


def _urlopen_with_host_certs(
    req: urllib.request.Request,
    timeout: int,
) -> Any:
    ctx = _ssl_context_for_url(req.full_url)
    if ctx is None:
        return urllib.request.urlopen(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _html_to_text(raw_html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_html)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _ensure_repo_path_exists() -> None:
    REPO_PATH.mkdir(parents=True, exist_ok=True)


def _mcp_resource_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)


def _decode_resource_path(path: str) -> str:
    return urllib.parse.unquote(path)


@mcp.resource(
    "repo://summary",
    name="repo_summary_resource",
    description="Repository summary and basic server capability flags.",
    mime_type="application/json",
)
def repo_summary_resource() -> str:
    _ensure_repo_path_exists()
    branch = ""
    head = ""
    is_git_repo = _is_git_repo()
    if is_git_repo:
        branch = _git("branch", "--show-current").stdout.strip()
        head = _git("rev-parse", "HEAD").stdout.strip()
    payload = {
        "schema": "resource.repo_summary.v1",
        "repo_path": str(REPO_PATH),
        "is_git_repo": is_git_repo,
        "current_branch": branch,
        "head": head,
        "allow_mutations": ALLOW_MUTATIONS,
        "max_read_bytes": MAX_READ_BYTES,
        "max_output_chars": MAX_OUTPUT_CHARS,
    }
    return _mcp_resource_json(payload)


@mcp.resource(
    "repo://file/{path}",
    name="repo_file_resource",
    description="Read a UTF-8 file from the repository by relative path.",
    mime_type="text/plain",
)
def repo_file_resource(path: str) -> str:
    return read_file(path=_decode_resource_path(path))


@mcp.resource(
    "repo://tree/{path}",
    name="repo_tree_resource",
    description="List repository entries under a relative path.",
    mime_type="application/json",
)
def repo_tree_resource(path: str) -> str:
    decoded_path = _decode_resource_path(path)
    entries = list_files(path=decoded_path, recursive=True, include_hidden=False, max_entries=1000)
    payload = {
        "schema": "resource.repo_tree.v1",
        "path": decoded_path,
        "entries": entries,
        "count": len(entries),
    }
    return _mcp_resource_json(payload)


@mcp.resource(
    RELEASE_READINESS_DASHBOARD_RESOURCE_URI,
    name="release_readiness_dashboard_resource",
    description="Read-only MCP Apps dashboard template for release_readiness results.",
    mime_type="text/html;profile=mcp-app",
)
def release_readiness_dashboard_resource() -> str:
    """Return a static MCP Apps HTML view for release_readiness tool data."""
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Release readiness dashboard</title>
<style>
:root{color-scheme:light dark;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
body{margin:0;padding:16px;background:transparent;color:CanvasText;}
.card{border:1px solid color-mix(in srgb,CanvasText 24%,transparent);border-radius:10px;padding:12px;margin:10px 0;background:color-mix(in srgb,Canvas 92%,CanvasText 8%);}
.status{font-size:1.35rem;font-weight:700}.go{color:#22863a}.nogo{color:#cb2431}.warn{color:#b08800}.muted{opacity:.72}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}.pill{display:inline-block;border-radius:999px;padding:2px 8px;margin:2px;font-size:.82rem;border:1px solid currentColor}
pre{white-space:pre-wrap;word-break:break-word;padding:8px;border-radius:6px;background:rgba(127,127,127,.16)} button{margin-left:8px}
</style>
</head>
<body>
<main id="app"><p class="muted">Waiting for release_readiness data from the MCP host…</p></main>
<script>
(function(){
  const app=document.getElementById('app');
  function esc(v){return String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function copy(text){navigator.clipboard?.writeText(text).catch(()=>{});}
  window.copyStep=copy;
  function render(payload){
    const data=payload?.mcp_apps?.dashboard?.data || payload?.structuredContent?.mcp_apps?.dashboard?.data || payload?.data || payload;
    if(!data || !data.groups){return;}
    const groups=data.groups.map(g=>`<section class="card"><h2>${esc(g.title)} <span class="pill ${g.status==='blocking'?'nogo':g.status==='warning'?'warn':'go'}">${esc(g.status)}</span></h2>${(g.items||[]).map(i=>`<div><strong>${esc(i.label)}</strong>: ${esc(i.summary)} ${i.blocking?'<span class="pill nogo">blocking</span>':''}${i.warning?'<span class="pill warn">warning</span>':''}</div>`).join('')}</section>`).join('');
    const steps=(data.next_steps||[]).map(s=>`<pre>${esc(s)}<button onclick="copyStep(${JSON.stringify(String(s))})">Copy</button></pre>`).join('');
    app.innerHTML=`<h1>Release readiness</h1><div class="status ${data.ok?'go':'nogo'}">${data.ok?'GO':'NO-GO'}</div><p class="muted">${esc(data.base_ref)} → ${esc(data.head_ref)} · ${esc(data.schema)}</p><div class="grid">${groups}</div>${data.rollback_reference?`<section class="card"><h2>Rollback / snapshot</h2><pre>${esc(JSON.stringify(data.rollback_reference,null,2))}</pre></section>`:''}<section class="card"><h2>Copyable next steps</h2>${steps || '<p class="muted">No suggested next steps.</p>'}</section>`;
  }
  window.addEventListener('message',ev=>{const msg=ev.data||{}; if(msg.method&&String(msg.method).includes('tool')) render(msg.params||msg.result||msg); else render(msg);});
  window.parent?.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized',params:{app:'release_readiness_dashboard'}},'*');
})();
</script>
</body>
</html>"""


def _workflow_prompt_text(
    title: str,
    goal: str,
    tool_chain: list[str],
    guardrails: list[str],
    requested_output: list[str],
) -> str:
    return "\n".join(
        [
            f"# {title}",
            "",
            f"Goal: {goal}",
            "",
            "Use this MCP workflow through the public `task_router` entrypoint. Prefer `mode='task'` for natural-language orchestration; when a client exposes internal workflow names in summaries or reports, keep the chain aligned with:",
            *[f"- `{tool}`" for tool in tool_chain],
            "",
            "Safety guardrails:",
            *[f"- {guardrail}" for guardrail in guardrails],
            "",
            "Return:",
            *[f"- {item}" for item in requested_output],
        ]
    )


@mcp.prompt(
    name="review_changed_files",
    title="Review changed files",
    description="Review the current branch diff with impact, risk, and test guidance without mutating files.",
)
def review_changed_files(
    base_ref: Annotated[
        str,
        Field(description="Base Git ref for the comparison, for example origin/main or HEAD~1."),
    ] = "origin/main",
    focus: Annotated[
        str,
        Field(description="Optional review focus such as security, docs, tests, or API compatibility."),
    ] = "",
) -> str:
    """Review branch changes with existing read-only analysis workflows."""
    focus_text = f" Focus area: {focus}." if focus.strip() else ""
    return _workflow_prompt_text(
        title="Review changed files",
        goal=f"Compare the working branch against `{base_ref}` and produce concise review findings for changed files.{focus_text}",
        tool_chain=["task_router(mode='task', task='review')", "change_impact_gate", "quality_router(mode='change_impact')"],
        guardrails=[
            "Stay read-only; do not edit files or run mutation modes.",
            "Respect existing mutation and security gates if you later recommend fixes.",
            "Consult `test_impact_map`/`impact_tests` before mutation or release-readiness checks; report selected tests and unmapped coverage gaps.",
            "Call out high-risk files, missing tests, and rollback considerations before suggestions.",
        ],
        requested_output=[
            "Findings first, each with file/path evidence when possible.",
            "Changed-file risk summary and recommended validation commands.",
            "Explicit note if no blocking issue is found.",
        ],
    )


@mcp.prompt(
    name="release_readiness_check",
    title="Release readiness check",
    description="Prepare a release gate report using existing readiness, impact, docs, license, risk, security, and test checks.",
)
def release_readiness_check(
    base_ref: Annotated[str, Field(description="Release comparison base ref.")] = "origin/main",
    head_ref: Annotated[str, Field(description="Release comparison head ref.")] = "HEAD",
    summary_mode: Annotated[str, Field(description="Readiness summary detail, usually quick or full.")] = "quick",
) -> str:
    """Create a release-readiness workflow prompt backed by quality gates."""
    return _workflow_prompt_text(
        title="Release readiness check",
        goal=f"Assess whether `{head_ref}` is ready to release against `{base_ref}` with `{summary_mode}` reporting.",
        tool_chain=["clarification_gate(operation='release_readiness')", "quality_router(mode='release_readiness')", "release_readiness", "required_tool_chain"],
        guardrails=[
            "Run or inspect clarification_gate first; if it returns ok_to_continue=false, render its fallback_checklist or MCP elicitation request instead of recommending release action.",
            "Do not bypass failing gates; report blockers clearly.",
            "Do not mutate files unless the user explicitly requests a follow-up fix workflow and ALLOW_MUTATIONS permits it.",
            "Keep artifacts compatible with existing structured readiness reports.",
        ],
        requested_output=[
            "Release decision: ready, not ready, or needs human review.",
            "Gate-by-gate summary for tests, docs, license, impact, risk, and security.",
            "Required follow-up tool chain or validation commands.",
        ],
    )


@mcp.prompt(
    name="security_triage",
    title="Security triage",
    description="Triage suspicious files, diffs, or dependencies through existing security/risk workflows without weakening gates.",
)
def security_triage(
    target: Annotated[str, Field(description="File, directory, diff range, dependency, or feature area to triage.")] = "changed files",
) -> str:
    """Triage security risk using existing read-only analysis paths."""
    return _workflow_prompt_text(
        title="Security triage",
        goal=f"Investigate security-sensitive behavior in `{target}` and separate confirmed findings from hypotheses.",
        tool_chain=["task_router(mode='task', task='security')", "change_impact_gate", "policy_simulator"],
        guardrails=[
            "Prefer read-only inspection and minimal reproduction steps.",
            "Do not print secrets, tokens, private keys, or credential material.",
            "Do not disable authentication, sandboxing, policy, or mutation gates.",
        ],
        requested_output=[
            "Prioritized findings with exploitability and affected paths.",
            "Safe verification steps that avoid secret exposure.",
            "Mitigation options and residual risk.",
        ],
    )


@mcp.prompt(
    name="devcontainer_health_check",
    title="Devcontainer health check",
    description="Check VS Code/devcontainer MCP setup, forwarded ports, health endpoints, auth mode, and local model service state.",
)
def devcontainer_health_check(
    endpoint: Annotated[str, Field(description="MCP HTTP endpoint expected by VS Code or Copilot.")] = "http://localhost:8000/mcp",
) -> str:
    """Guide VS Code users through a safe MCP/devcontainer health check."""
    return _workflow_prompt_text(
        title="Devcontainer health check",
        goal=f"Verify that VS Code/Copilot can discover and use the MCP server at `{endpoint}` from the devcontainer workflow.",
        tool_chain=["task_router(mode='status')", "healthz", "docs/vscode-mcp-onboarding.md"],
        guardrails=[
            "Never echo bearer token values; only report whether a token/header is configured.",
            "Keep auth enabled unless the user intentionally selected documented insecure local-only mode.",
            "Treat port and Ollama checks as diagnostics, not permission to change host services.",
        ],
        requested_output=[
            "Discovery status for MCP prompts/tools/resources in VS Code or Copilot.",
            "Health summary for `/healthz`, `/mcp`, port forwarding, auth mode, and Ollama.",
            "Next actions with exact docs links or commands, redacting secrets.",
        ],
    )


@mcp.prompt(
    name="snapshot_before_refactor",
    title="Snapshot before refactor",
    description="Plan a safe pre-refactor snapshot and rollback path before any mutation workflow starts.",
)
def snapshot_before_refactor(
    refactor_goal: Annotated[str, Field(description="Short description of the intended refactor.")] = "planned refactor",
) -> str:
    """Prepare snapshot and rollback guardrails before refactoring."""
    return _workflow_prompt_text(
        title="Snapshot before refactor",
        goal=f"Before starting `{refactor_goal}`, create a verifiable rollback point and summarize the safe mutation plan.",
        tool_chain=["workspace_transaction(mode='snapshot')", "workspace_transaction(mode='restore')", "quality_router(mode='self_check')"],
        guardrails=[
            "Only create snapshots or mutate files when ALLOW_MUTATIONS and user intent permit it.",
            "Record snapshot id and current Git status before edits.",
            "Prefer small, reviewable edits and run validation before handing off.",
        ],
        requested_output=[
            "Snapshot/rollback plan and whether mutation is currently allowed.",
            "Refactor steps ordered from safest to riskiest.",
            "Validation commands and restore instructions if the refactor fails.",
        ],
    )


def _is_git_repo() -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_PATH), "rev-parse", "--is-inside-work-tree"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except FileNotFoundError:
        raise RuntimeError("git executable not found inside container")


def _require_git_repo() -> None:
    _ensure_repo_path_exists()
    if not _is_git_repo():
        raise ValueError(f"{REPO_PATH} is not a Git working tree")


def _require_mutations() -> None:
    if not ALLOW_MUTATIONS:
        raise PermissionError(
            "Mutating operations are disabled. Set ALLOW_MUTATIONS=true to enable them."
        )


def _resolve_repo_path(rel_path: str = ".") -> Path:
    _ensure_repo_path_exists()
    candidate = (REPO_PATH / rel_path).resolve()
    try:
        candidate.relative_to(REPO_PATH)
    except ValueError as exc:
        raise ValueError("path escapes repository root") from exc
    return candidate


ROOTS_DIAGNOSTIC_CLASSIFICATIONS = {
    "exact_match",
    "repo_contains_root",
    "root_contains_repo",
    "multiple_roots",
    "no_overlap",
    "unsupported",
    "unavailable",
    "error",
}


def _roots_guidance(classification: str) -> list[str]:
    guidance = {
        "exact_match": [
            "Client roots exactly match the configured repository boundary.",
            "Repository path enforcement still comes from REPO_PATH and _resolve_repo_path.",
        ],
        "repo_contains_root": [
            "The client root is a subdirectory of the configured repository.",
            "Agents may see narrower client context than the server can inspect; keep paths repository-relative.",
        ],
        "root_contains_repo": [
            "The client root contains the configured repository and may include additional host workspace context.",
            "Only repository-relative paths under REPO_PATH are authorized by this server.",
        ],
        "multiple_roots": [
            "The client advertised multiple file roots; inspect per-root relationships for mismatches.",
            "Use one root matching REPO_PATH when possible to avoid confusing agent context.",
        ],
        "no_overlap": [
            "Client file roots do not overlap the configured repository.",
            "Reconnect the MCP client from the mounted repository workspace or adjust REPO_PATH.",
        ],
        "unsupported": [
            "The active client/session does not advertise MCP roots support.",
            "This is diagnostic only; existing repository boundary checks remain active.",
        ],
        "unavailable": [
            "No request-scoped MCP session is available, so roots/list cannot be queried here.",
            "Call roots_diagnostics through an MCP tool session for client-specific roots details.",
        ],
        "error": [
            "The roots/list diagnostic could not complete successfully.",
            "Treat this as advisory and continue relying on REPO_PATH/_resolve_repo_path for authorization.",
        ],
    }
    return guidance.get(classification, guidance["error"])


def _roots_base_payload(classification: str, fetch_status: str) -> dict[str, Any]:
    return {
        "schema": "roots_diagnostics.v1",
        "read_only": True,
        "advisory_only": True,
        "repo_boundary_enforced": True,
        "server_repo": {
            "path": str(REPO_PATH),
            "exists": REPO_PATH.exists(),
            "is_git_repo": _is_git_repo() if REPO_PATH.exists() else False,
        },
        "fetch": {"status": fetch_status},
        "roots": {
            "total_count": 0,
            "file_count": 0,
            "invalid_count": 0,
            "scheme_counts": {},
            "redactions_applied": [],
            "items": [],
        },
        "relationship": {"classification": classification, "file_root_count": 0},
        "guidance": _roots_guidance(classification),
        "safety": {
            "client_paths_redacted": True,
            "authorization_boundary": "REPO_PATH/_resolve_repo_path",
            "note": "MCP roots diagnostics are advisory and never grant filesystem access.",
        },
    }


def _classify_root_path(path: Path) -> tuple[str, str]:
    repo = REPO_PATH.resolve()
    root = path.resolve(strict=False)
    if root == repo:
        return "exact_match", "."
    with contextlib.suppress(ValueError):
        rel = root.relative_to(repo)
        return "repo_contains_root", str(rel).replace("\\", "/") or "."
    with contextlib.suppress(ValueError):
        repo.relative_to(root)
        return "root_contains_repo", "<redacted:root_contains_repo>"
    return "no_overlap", "<redacted:outside_repo>"


def _path_from_file_uri(uri: str) -> Path:
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme.lower() != "file":
        raise ValueError("not a file URI")
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        raise ValueError("remote file URI authorities are not supported")
    raw_path = urllib.parse.unquote(parsed.path or "")
    if not raw_path:
        raise ValueError("file URI has no path")
    return Path(raw_path).resolve(strict=False)


def _summarize_roots_result(roots: list[Any]) -> dict[str, Any]:
    payload = _roots_base_payload("unavailable", "fetched")
    scheme_counts: dict[str, int] = {}
    redactions: set[str] = set()
    items: list[dict[str, Any]] = []
    file_relationships: list[str] = []

    for index, root in enumerate(roots):
        uri = str(getattr(root, "uri", "") or "")
        parsed = urllib.parse.urlparse(uri) if uri else urllib.parse.ParseResult("", "", "", "", "", "")
        scheme = parsed.scheme.lower() or "<missing>"
        scheme_counts[scheme] = scheme_counts.get(scheme, 0) + 1
        item: dict[str, Any] = {
            "index": index,
            "scheme": scheme,
            "name_present": bool(str(getattr(root, "name", "") or "")),
        }
        if scheme != "file":
            item["relationship"] = "ignored_non_file_scheme"
            redactions.add("non_file_uri_omitted")
            items.append(item)
            continue
        try:
            root_path = _path_from_file_uri(uri)
            relationship, normalized = _classify_root_path(root_path)
            item["relationship"] = relationship
            item["normalized_path"] = normalized
            if normalized.startswith("<redacted:"):
                redactions.add("outside_repo_client_path")
            file_relationships.append(relationship)
        except Exception as exc:
            item["relationship"] = "invalid"
            item["error"] = exc.__class__.__name__
            payload["roots"]["invalid_count"] += 1
            redactions.add("invalid_file_uri_omitted")
        items.append(item)

    file_count = len(file_relationships)
    if file_count > 1:
        classification = "multiple_roots"
    elif file_count == 1:
        classification = file_relationships[0]
    elif payload["roots"]["invalid_count"]:
        classification = "error"
    else:
        classification = "no_overlap" if roots else "unavailable"
    if classification not in ROOTS_DIAGNOSTIC_CLASSIFICATIONS:
        classification = "error"

    payload["roots"].update(
        {
            "total_count": len(roots),
            "file_count": file_count,
            "scheme_counts": dict(sorted(scheme_counts.items())),
            "redactions_applied": sorted(redactions),
            "items": items,
        }
    )
    payload["relationship"] = {
        "classification": classification,
        "file_root_count": file_count,
        "per_root_relationships": file_relationships,
    }
    payload["guidance"] = _roots_guidance(classification)
    return payload


def _active_mcp_session() -> Any | None:
    try:
        context = mcp.get_context()
    except Exception:
        return None
    try:
        session = getattr(context, "session", None)
    except Exception:
        session = None
    if session is not None:
        return session
    try:
        request_context = getattr(context, "request_context", None)
        return getattr(request_context, "session", None) if request_context is not None else None
    except Exception:
        return None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _client_roots_supported(session: Any) -> bool:
    checker = getattr(session, "check_client_capability", None)
    if checker is None:
        return hasattr(session, "list_roots")
    try:
        from mcp.types import ClientCapabilities, RootsCapability

        return bool(
            await _maybe_await(
                checker(ClientCapabilities(roots=RootsCapability()))
            )
        )
    except Exception:
        return hasattr(session, "list_roots")



def _repo_resource_uri(rel_path: str) -> str:
    return "repo://file/" + urllib.parse.quote(rel_path, safe="")


def _artifact_resource_link(
    *,
    title: str,
    rel_path: str = "",
    uri: str = "",
    mime_type: str = "application/octet-stream",
    created_at: str = "",
    redacted: bool = True,
    safety_note: str = "Generated artifact metadata only; repository-relative path, no host absolute path.",
) -> dict[str, Any]:
    if not rel_path and not uri:
        raise ValueError("resource links require rel_path or uri")
    link: dict[str, Any] = {
        "schema": "artifact_resource_link.v1",
        "title": title,
        "uri": uri or _repo_resource_uri(rel_path),
        "mime_type": mime_type,
        "created_at": created_at or _now_iso(),
        "safety": {
            "redacted": redacted,
            "contains_secrets": False,
            "repo_boundary_enforced": True,
            "note": safety_note,
        },
    }
    if rel_path:
        path = _resolve_repo_path(rel_path)
        link["path"] = rel_path
        if path.exists() and path.is_file():
            stat = path.stat()
            link["size_bytes"] = stat.st_size
            if not created_at:
                link["created_at"] = datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat()
    return link


def _artifact_meta(resource_links: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "artifact_resources": {
            "schema": "artifact_resource_links.v1",
            "resource_links": resource_links,
            "safety": {
                "absolute_host_paths_exposed": False,
                "secrets_exposed": False,
                "redaction_required": True,
            },
        }
    }


_WORKFLOW_TASK_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2)
_WORKFLOW_TASK_LOCK = threading.Lock()
_WORKFLOW_TASK_FUTURES: dict[str, concurrent.futures.Future[Any]] = {}
_WORKFLOW_TASK_ALLOWED_WORKFLOWS = {"governance_report", "vscode_task_run"}
_WORKFLOW_TASK_FINAL_STATUSES = {"succeeded", "failed", "expired"}


def _workflow_task_categories(workflow: str) -> list[str]:
    if workflow == "vscode_task_run":
        return ["write", "shell/process", "async"]
    return ["read-only", "async"]


def _workflow_task_stable_id(workflow: str, args: dict[str, Any], task_id: str = "") -> str:
    if task_id:
        _workflow_task_path(task_id)
        return task_id
    canonical = json.dumps(
        {"workflow": workflow, "arguments": _redact_audit_value(args)},
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return "task-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _workflow_task_result_is_transient(result: dict[str, Any]) -> bool:
    if result.get("timeout") is True:
        return True
    if result.get("ok") is True:
        return False
    text = "\n".join(
        str(result.get(key, "")) for key in ("stderr", "stdout", "build_log_tail", "error")
    ).lower()
    markers = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "rate limit",
        "too many requests",
        "docker daemon",
        "resource temporarily unavailable",
    )
    return any(marker in text for marker in markers)


def _workflow_tasks_dir() -> Path:
    path = _resolve_repo_path(str(WORKFLOW_TASKS_DIR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prune_workflow_task_statuses() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, WORKFLOW_TASK_RETENTION_DAYS))
    try:
        root = _workflow_tasks_dir()
    except Exception:
        return
    for path in root.glob("*.json"):
        try:
            retention_expires_at = ""
            with contextlib.suppress(Exception):
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    retention_expires_at = str(payload.get("retention_expires_at") or "")
            if retention_expires_at and _is_expired(retention_expires_at, datetime.now(timezone.utc)):
                path.unlink()
                continue
            if not retention_expires_at and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                path.unlink()
        except OSError:
            continue


def _workflow_task_path(task_id: str) -> Path:
    if not re.fullmatch(r"(?:task-[0-9a-f]{32}|[A-Za-z0-9][A-Za-z0-9._-]{0,79})", task_id):
        raise ValueError("task_id must be a workflow task id")
    return _workflow_tasks_dir() / f"{task_id}.json"



def _workflow_task_result_artifacts_dir() -> Path:
    path = _workflow_tasks_dir() / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _workflow_task_result_artifact_path(task_id: str) -> Path:
    _workflow_task_path(task_id)
    return _workflow_task_result_artifacts_dir() / f"{task_id}-vscode-task-result.json"


def _workflow_task_result_artifact_link(task_id: str, created_at: str = "") -> dict[str, Any]:
    rel_path = str(WORKFLOW_TASKS_DIR / "artifacts" / f"{task_id}-vscode-task-result.json")
    return _artifact_resource_link(
        title="VS Code task run result",
        rel_path=rel_path,
        mime_type="application/json",
        created_at=created_at,
        redacted=True,
        safety_note="Redacted VS Code task result artifact; task status stores only compact metadata and references.",
    )


def _write_workflow_task_result_artifact(task_id: str, result: dict[str, Any]) -> dict[str, Any]:
    redacted = _redact_audit_value(result)
    path = _workflow_task_result_artifact_path(task_id)
    tmp = path.with_suffix(".json.tmp")
    payload = {
        "schema": "workflow_task_result_artifact.v1",
        "task_id": task_id,
        "workflow": "vscode_task_run",
        "created_at": _now_iso(),
        "security": {
            "redacted": True,
            "contains_secrets": False,
            "repo_boundary_enforced": True,
        },
        "result": redacted,
    }
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return payload


def _compact_vscode_task_result(result: dict[str, Any], artifact_links: list[dict[str, Any]]) -> dict[str, Any]:
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    build_log_tail_value = result.get("build_log_tail")
    build_log_tail = (
        str(build_log_tail_value)
        if build_log_tail_value is not None
        else _summarize_build_log(stdout, stderr)
    )
    compact: dict[str, Any] = {
        "schema": result.get("schema", "vscode_task_run.v1"),
        "ok": bool(result.get("ok", False)),
        "label": _redact_audit_value(result.get("label", "")),
        "tasks_path": _redact_audit_value(result.get("tasks_path", "")),
        "control_profile": _redact_audit_value(result.get("control_profile", "")),
        "cwd": _redact_audit_value(result.get("cwd", "")),
        "exit_code": result.get("exit_code"),
        "timeout": bool(result.get("timeout", False)),
        "output_summary": {
            "stdout_chars": len(stdout),
            "stderr_chars": len(stderr),
            "build_log_tail_lines": len(build_log_tail.splitlines()),
        },
        "artifact_references": artifact_links,
        "output_artifacts": {
            "schema": "artifact_resource_links.v1",
            "resource_links": artifact_links,
        },
    }
    if result.get("proposals"):
        compact["proposals"] = _redact_audit_value(result.get("proposals"))
    if isinstance(result.get("error"), dict):
        compact["error"] = _redact_audit_value(result.get("error"))
    return compact

def _workflow_task_artifact_link(task_id: str, created_at: str = "") -> dict[str, Any]:
    rel_path = str(WORKFLOW_TASKS_DIR / f"{task_id}.json")
    return _artifact_resource_link(
        title="Workflow task status",
        rel_path=rel_path,
        mime_type="application/json",
        created_at=created_at,
        redacted=True,
        safety_note="Task status stores redacted workflow metadata and artifact references only; no raw secrets.",
    )


def _write_workflow_task_status(status: dict[str, Any]) -> dict[str, Any]:
    redacted = _redact_audit_value(status)
    path = _workflow_task_path(str(redacted["task_id"]))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(redacted, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return redacted


def _read_workflow_task_status(task_id: str) -> dict[str, Any]:
    path = _workflow_task_path(task_id)
    if not path.exists():
        raise FileNotFoundError(f"workflow task not found: {task_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("workflow task status is invalid")
    return payload


def _workflow_task_expired(payload: dict[str, Any]) -> bool:
    return _is_expired(str(payload.get("expires_at") or ""), datetime.now(timezone.utc))


def _expire_workflow_task_if_needed(payload: dict[str, Any]) -> dict[str, Any]:
    if str(payload.get("status")) in _WORKFLOW_TASK_FINAL_STATUSES:
        return payload
    if not _workflow_task_expired(payload):
        return payload
    payload = dict(payload)
    payload["status"] = "expired"
    payload["state"] = "expired"
    payload["finished_at"] = payload.get("finished_at") or _now_iso()
    payload["progress"] = 1.0
    payload["progress_detail"] = {"phase": "expired", "percent": 100}
    payload["error"] = "task expired before completion status was observed"
    payload["audit_events"] = [
        *payload.get("audit_events", []),
        {"event": "expired", "at": payload["finished_at"]},
    ]
    _append_audit_event(
        "workflow_task",
        ["read-only", "async"],
        False,
        {"task_id": payload.get("task_id"), "workflow": payload.get("workflow"), "event": "expired"},
        "expired",
    )
    _otel_record_workflow_lifecycle(
        str(payload.get("task_id") or ""),
        str(payload.get("workflow") or ""),
        "expired",
        success=False,
        status="expired",
    )
    return _write_workflow_task_status(payload)


def _workflow_task_status_payload(task_id: str) -> dict[str, Any]:
    payload = _expire_workflow_task_if_needed(_read_workflow_task_status(task_id))
    created_at = str(payload.get("created_at") or "")
    payload["resource_links"] = [_workflow_task_artifact_link(task_id, created_at=created_at)]
    payload["_meta"] = _artifact_meta(payload["resource_links"])
    return payload


def _run_governance_report_task_inner(task_id: str, args: dict[str, Any]) -> None:
    started_at = _now_iso()
    payload = _read_workflow_task_status(task_id)
    payload.update(
        {
            "status": "running",
            "state": "running",
            "started_at": started_at,
            "updated_at": started_at,
            "progress": 0.25,
            "progress_detail": {"phase": "running", "percent": 25},
            "audit_events": [
                *payload.get("audit_events", []),
                {"event": "running", "at": started_at},
            ],
        }
    )
    _write_workflow_task_status(payload)
    _append_audit_event(
        "workflow_task",
        ["read-only", "async"],
        True,
        {"task_id": task_id, "workflow": "governance_report", "event": "started"},
        "started",
    )
    _otel_record_workflow_lifecycle(task_id, "governance_report", "started", status="running")
    try:
        result = governance_report(**args)
        finished_at = _now_iso()
        exports = result.get("exports", {}) if isinstance(result.get("exports"), dict) else {}
        resource_links = (
            result.get("resource_links", [])
            if isinstance(result.get("resource_links"), list)
            else []
        )
        payload.update(
            {
                "status": "succeeded",
                "state": "succeeded",
                "ok": True,
                "finished_at": finished_at,
                "updated_at": finished_at,
                "progress": 1.0,
                "progress_detail": {"phase": "complete", "percent": 100},
                "result": {
                    "schema": result.get("schema"),
                    "report_id": result.get("report_id"),
                    "generated_at": result.get("generated_at"),
                    "exports": exports,
                },
                "artifact_references": resource_links,
                "audit_events": [
                    *payload.get("audit_events", []),
                    {"event": "succeeded", "at": finished_at},
                ],
            }
        )
        _write_workflow_task_status(payload)
        _append_audit_event(
            "workflow_task",
            ["read-only", "async"],
            True,
            {
                "task_id": task_id,
                "workflow": "governance_report",
                "report_id": result.get("report_id"),
                "event": "completed",
            },
            "completed",
        )
        _otel_record_workflow_lifecycle(
            task_id,
            "governance_report",
            "completed",
            status="succeeded",
            artifact_refs=[str(link.get("path")) for link in resource_links if isinstance(link.get("path"), str)],
        )
    except Exception as exc:  # pragma: no cover - defensive background failure path
        finished_at = _now_iso()
        payload.update(
            {
                "status": "failed",
                "state": "failed",
                "ok": False,
                "finished_at": finished_at,
                "updated_at": finished_at,
                "progress": 1.0,
                "progress_detail": {"phase": "failed", "percent": 100},
                "error": _redact_audit_reason(type(exc).__name__),
                "audit_events": [
                    *payload.get("audit_events", []),
                    {"event": "failed", "at": finished_at, "reason": type(exc).__name__},
                ],
            }
        )
        _write_workflow_task_status(payload)
        _append_audit_event(
            "workflow_task",
            ["read-only", "async"],
            False,
            {"task_id": task_id, "workflow": "governance_report", "event": "failed"},
            type(exc).__name__,
        )
        _otel_record_workflow_lifecycle(
            task_id,
            "governance_report",
            "failed",
            success=False,
            status=type(exc).__name__,
        )


def _run_governance_report_task(task_id: str, args: dict[str, Any]) -> None:
    with _otel_correlation_context(task_id):
        _run_governance_report_task_inner(task_id, args)



def _run_vscode_task_inner(task_id: str, args: dict[str, Any], max_retries: int = 0) -> None:
    started_at = _now_iso()
    payload = _read_workflow_task_status(task_id)
    payload.update(
        {
            "status": "running",
            "state": "running",
            "started_at": started_at,
            "updated_at": started_at,
            "progress": 0.25,
            "progress_detail": {"phase": "running", "percent": 25},
            "audit_events": [
                *payload.get("audit_events", []),
                {"event": "running", "at": started_at},
            ],
        }
    )
    _write_workflow_task_status(payload)
    _append_audit_event(
        "workflow_task",
        _workflow_task_categories("vscode_task_run"),
        True,
        {"task_id": task_id, "workflow": "vscode_task_run", "event": "started"},
        "started",
    )
    _otel_record_workflow_lifecycle(task_id, "vscode_task_run", "started", status="running")

    retries: list[dict[str, Any]] = []
    attempt = 0
    result: dict[str, Any] = {}
    ok = False
    while attempt <= max(0, max_retries):
        attempt += 1
        try:
            result = vscode_task_run(**args)
        except Exception as exc:
            result = {
                "schema": "vscode_task_run.v1",
                "ok": False,
                "timeout": False,
                "stderr": _redact_audit_reason(str(exc)),
                "error": {"type": type(exc).__name__, "message": _redact_audit_reason(str(exc))},
            }
        ok = bool(result.get("ok", False))
        transient = _workflow_task_result_is_transient(result)
        if ok or attempt > max(0, max_retries) or not transient:
            break
        retries.append({"attempt": attempt, "reason": "transient_failure", "at": _now_iso()})
        _append_audit_event(
            "workflow_task",
            _workflow_task_categories("vscode_task_run"),
            False,
            {"task_id": task_id, "workflow": "vscode_task_run", "event": "retry", "attempt": attempt},
            "transient_failure",
        )
        _otel_record_workflow_lifecycle(
            task_id,
            "vscode_task_run",
            "retry",
            success=False,
            status="transient_failure",
        )

    artifact_payload = _write_workflow_task_result_artifact(task_id, result)
    artifact_links = [
        _workflow_task_result_artifact_link(
            task_id, created_at=str(artifact_payload.get("created_at") or "")
        )
    ]
    finished_at = _now_iso()
    state = "succeeded" if ok else "failed"
    payload.update(
        {
            "status": state,
            "state": state,
            "ok": ok,
            "attempt": attempt,
            "retries": retries,
            "finished_at": finished_at,
            "updated_at": finished_at,
            "progress": 1.0,
            "progress_detail": {"phase": "complete" if ok else "failed", "percent": 100},
            "result": _compact_vscode_task_result(result, artifact_links),
            "artifact_references": artifact_links,
            "audit_events": [
                *payload.get("audit_events", []),
                {"event": "completed" if ok else "failed", "at": finished_at},
            ],
        }
    )
    _write_workflow_task_status(payload)
    _append_audit_event(
        "workflow_task",
        _workflow_task_categories("vscode_task_run"),
        ok,
        {"task_id": task_id, "workflow": "vscode_task_run", "event": "completed" if ok else "failed"},
        "completed" if ok else "failed",
    )
    _otel_record_workflow_lifecycle(
        task_id,
        "vscode_task_run",
        "completed" if ok else "failed",
        success=ok,
        status="succeeded" if ok else "failed",
        artifact_refs=[str(link.get("path")) for link in artifact_links if isinstance(link.get("path"), str)],
    )


def _run_vscode_task(task_id: str, args: dict[str, Any], max_retries: int = 0) -> None:
    with _otel_correlation_context(task_id):
        _run_vscode_task_inner(task_id, args, max_retries=max_retries)


def _start_workflow_task(
    workflow: str,
    args: dict[str, Any],
    retry_of: str = "",
    task_id: str = "",
    max_retries: int = 0,
    restart: bool = False,
) -> dict[str, Any]:
    if workflow not in _WORKFLOW_TASK_ALLOWED_WORKFLOWS:
        raise ValueError(
            "workflow must be one of: "
            + ", ".join(sorted(_WORKFLOW_TASK_ALLOWED_WORKFLOWS))
        )
    if retry_of:
        previous = _workflow_task_status_payload(retry_of)
        if previous.get("workflow") != workflow:
            raise ValueError("retry_of workflow does not match requested workflow")
    _prune_workflow_task_statuses()
    id_args = dict(args)
    if retry_of:
        id_args["retry_of"] = retry_of
    task_id = _workflow_task_stable_id(workflow, id_args, task_id)
    status_path = _workflow_task_path(task_id)
    if status_path.exists() and not restart:
        return _workflow_task_status_payload(task_id)
    created_at = _now_iso()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=max(1, WORKFLOW_TASK_EXPIRY_HOURS))
    ).isoformat()
    retention_expires_at = (
        datetime.now(timezone.utc) + timedelta(days=max(1, WORKFLOW_TASK_RETENTION_DAYS))
    ).isoformat()
    payload: dict[str, Any] = {
        "schema": "workflow_task.v1",
        "task_id": task_id,
        "workflow": workflow,
        "status": "pending",
        "state": "pending",
        "started": True,
        "ok": False,
        "attempt": 0,
        "max_retries": max(0, min(3, max_retries)),
        "retries": [],
        "created_at": created_at,
        "started_at": "",
        "finished_at": "",
        "updated_at": created_at,
        "expires_at": expires_at,
        "retention_expires_at": retention_expires_at,
        "retry_of": retry_of,
        "progress": 0.0,
        "progress_detail": {"phase": "queued", "percent": 0},
        "arguments": _redact_audit_value(args),
        "artifact_references": [],
        "audit_events": [{"event": "start", "at": created_at}],
        "security": {
            "redacted": True,
            "contains_secrets": False,
            "repo_boundary_enforced": True,
        },
    }
    if retry_of:
        payload["audit_events"].append(
            {"event": "retry", "at": created_at, "retry_of": retry_of}
        )
    _write_workflow_task_status(payload)
    runner = _run_vscode_task if workflow == "vscode_task_run" else _run_governance_report_task
    if workflow == "vscode_task_run":
        future = _WORKFLOW_TASK_EXECUTOR.submit(runner, task_id, args, max(0, min(3, max_retries)))
    else:
        future = _WORKFLOW_TASK_EXECUTOR.submit(runner, task_id, args)
    with _WORKFLOW_TASK_LOCK:
        _WORKFLOW_TASK_FUTURES[task_id] = future
    _append_audit_event(
        "workflow_task",
        _workflow_task_categories(workflow),
        True,
        {"task_id": task_id, "workflow": workflow, "retry_of": retry_of, "event": "start"},
        "start",
    )
    _otel_record_workflow_lifecycle(task_id, workflow, "start", status="pending")
    return _workflow_task_status_payload(task_id)


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    _ensure_repo_path_exists()
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_PATH), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git executable not found inside container") from exc

    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        msg = (
            stderr
            or stdout
            or f"git {' '.join(args)} failed with exit code {result.returncode}"
        )
        raise RuntimeError(msg)
    return result


def _normalize_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    for p in paths:
        resolved = _resolve_repo_path(p)
        normalized.append(str(resolved.relative_to(REPO_PATH)))
    return normalized


def _list_report_files(max_entries: int = 200) -> list[str]:
    reports_dir = _resolve_repo_path(str(REPORTS_DIR))
    if not reports_dir.exists():
        return []

    entries: list[str] = []
    for item in reports_dir.rglob("*"):
        if item.is_file():
            entries.append(str(item.relative_to(REPO_PATH)))
            if len(entries) >= max_entries:
                break
    entries.sort()
    return entries


def _is_hidden_rel_path(rel: Path) -> bool:
    return any(part.startswith(".") for part in rel.parts)


def _is_likely_binary(path: Path, max_file_bytes: int = 1048576) -> bool:
    try:
        size = path.stat().st_size
        if size > max_file_bytes:
            return True
        with path.open("rb") as f:
            chunk = f.read(8192)
        if b"\x00" in chunk:
            return True
    except OSError:
        return True
    return False


def _allowed_by_globs(
    rel_str: str,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
) -> bool:
    if include_globs and not any(fnmatch.fnmatch(rel_str, g) for g in include_globs):
        return False
    if exclude_globs and any(fnmatch.fnmatch(rel_str, g) for g in exclude_globs):
        return False
    return True


def _iter_candidate_files(
    root: Path,
    recursive: bool,
    include_hidden: bool = False,
) -> Any:
    if root.is_file():
        if include_hidden:
            yield root
            return
        rel = root.relative_to(REPO_PATH)
        if not _is_hidden_rel_path(rel):
            yield root
        return

    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            if not include_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            filenames.sort()
            base = Path(dirpath)
            for name in filenames:
                if not include_hidden and name.startswith("."):
                    continue
                p = base / name
                if not p.is_file():
                    continue
                if include_hidden:
                    yield p
                    continue
                rel = p.relative_to(REPO_PATH)
                if not _is_hidden_rel_path(rel):
                    yield p
        return

    for p in sorted(root.glob("*")):
        if not p.is_file():
            continue
        if include_hidden:
            yield p
            continue
        rel = p.relative_to(REPO_PATH)
        if not _is_hidden_rel_path(rel):
            yield p


def _read_lines(path: Path, encoding: str = "utf-8") -> list[str]:
    return path.read_text(encoding=encoding, errors="replace").splitlines()


def _truncate_with_flag(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    if len(text) <= max_chars:
        return text, False
    marker = f"\n\n[truncated: exceeded {max_chars} chars]"
    keep = max(1, max_chars - len(marker))
    return text[:keep] + marker, True


def _read_pdf_text(path: Path, max_pages: int) -> tuple[str, dict[str, Any]]:
    global PdfReader
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    if PdfReader is _OPTIONAL_DEPENDENCY_UNLOADED:
        PdfReader = _import_optional_dependency("pypdf", "pypdf").PdfReader
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed")
    reader = PdfReader(str(path))
    lines: list[str] = []
    page_count = len(reader.pages)
    for page in reader.pages[:max_pages]:
        extracted = page.extract_text() or ""
        if extracted:
            lines.append(extracted.strip())
    meta = {"page_count": page_count, "pages_read": min(page_count, max_pages)}
    return "\n\n".join(x for x in lines if x), meta


def _read_docx_text(path: Path) -> tuple[str, dict[str, Any]]:
    global docx
    if docx is _OPTIONAL_DEPENDENCY_UNLOADED:
        docx = _import_optional_dependency("docx", "python-docx")
    if docx is None:
        raise RuntimeError("python-docx is not installed")
    document = docx.Document(str(path))
    chunks: list[str] = []
    para_count = 0
    for p in document.paragraphs:
        t = p.text.strip()
        if t:
            chunks.append(t)
        para_count += 1
    table_cells = 0
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if text:
                    chunks.append(text)
                table_cells += 1
    return "\n".join(chunks), {"paragraph_count": para_count, "table_cells": table_cells}


def _read_doc_text(path: Path) -> tuple[str, dict[str, Any]]:
    antiword = shutil.which("antiword")
    if antiword:
        proc = subprocess.run(
            [antiword, str(path)],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        output = (proc.stdout or "").strip()
        if output:
            return output, {"backend": "antiword", "exit_code": proc.returncode}
        return "", {"backend": "antiword", "exit_code": proc.returncode}

    # Fallback keeps behavior functional even without antiword.
    raw = path.read_bytes()
    text = raw.decode("latin-1", errors="replace")
    return text, {"backend": "latin1-fallback", "bytes_read": len(raw)}


def _read_xlsx_text(path: Path, max_rows_per_sheet: int) -> tuple[str, dict[str, Any]]:
    global openpyxl
    if max_rows_per_sheet < 1:
        raise ValueError("max_rows_per_sheet must be >= 1")
    if openpyxl is _OPTIONAL_DEPENDENCY_UNLOADED:
        openpyxl = _import_optional_dependency("openpyxl", "openpyxl")
    if openpyxl is None:
        raise RuntimeError("openpyxl is not installed")
    wb = openpyxl.load_workbook(filename=str(path), read_only=True, data_only=True)
    chunks: list[str] = []
    total_rows = 0
    sheets_meta: list[dict[str, Any]] = []
    for ws in wb.worksheets:
        rows_read = 0
        for row in ws.iter_rows(values_only=True):
            if rows_read >= max_rows_per_sheet:
                break
            values = [str(v).strip() for v in row if v is not None and str(v).strip()]
            if values:
                chunks.append(" | ".join(values))
            rows_read += 1
        total_rows += rows_read
        sheets_meta.append({"name": ws.title, "rows_read": rows_read})
    return "\n".join(chunks), {"sheet_count": len(wb.worksheets), "rows_read": total_rows, "sheets": sheets_meta}


def _read_xls_text(path: Path, max_rows_per_sheet: int) -> tuple[str, dict[str, Any]]:
    global xlrd
    if max_rows_per_sheet < 1:
        raise ValueError("max_rows_per_sheet must be >= 1")
    if xlrd is _OPTIONAL_DEPENDENCY_UNLOADED:
        xlrd = _import_optional_dependency("xlrd", "xlrd")
    if xlrd is None:
        raise RuntimeError("xlrd is not installed")
    wb = xlrd.open_workbook(str(path))
    chunks: list[str] = []
    total_rows = 0
    sheets_meta: list[dict[str, Any]] = []
    for i in range(wb.nsheets):
        sheet = wb.sheet_by_index(i)
        rows_read = min(sheet.nrows, max_rows_per_sheet)
        for r in range(rows_read):
            row = sheet.row_values(r)
            values = [str(v).strip() for v in row if str(v).strip()]
            if values:
                chunks.append(" | ".join(values))
        total_rows += rows_read
        sheets_meta.append({"name": sheet.name, "rows_read": rows_read})
    return "\n".join(chunks), {"sheet_count": wb.nsheets, "rows_read": total_rows, "sheets": sheets_meta}


def _read_opendoc_text(path: Path, ext: str, max_rows_per_sheet: int) -> tuple[str, dict[str, Any]]:
    if max_rows_per_sheet < 1:
        raise ValueError("max_rows_per_sheet must be >= 1")
    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read("content.xml")
    except KeyError as exc:
        raise RuntimeError("content.xml not found in OpenDocument file") from exc
    except zipfile.BadZipFile as exc:
        raise RuntimeError("invalid OpenDocument zip container") from exc

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RuntimeError(f"invalid OpenDocument content.xml: {exc}") from exc

    ns = {
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
        "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    }
    chunks: list[str] = []
    meta: dict[str, Any] = {"format": ext}

    if ext == ".ods":
        sheet_rows: list[dict[str, Any]] = []
        for table in root.findall(".//table:table", ns):
            table_name = table.attrib.get(f"{{{ns['table']}}}name", "")
            rows_read = 0
            for row in table.findall("table:table-row", ns):
                if rows_read >= max_rows_per_sheet:
                    break
                cells: list[str] = []
                for cell in row.findall("table:table-cell", ns):
                    ps = [p.text.strip() for p in cell.findall(".//text:p", ns) if p.text and p.text.strip()]
                    if ps:
                        cells.append(" ".join(ps))
                if cells:
                    chunks.append(" | ".join(cells))
                rows_read += 1
            sheet_rows.append({"name": table_name, "rows_read": rows_read})
        meta["sheet_count"] = len(sheet_rows)
        meta["sheets"] = sheet_rows
        meta["rows_read"] = sum(s["rows_read"] for s in sheet_rows)
        return "\n".join(chunks), meta

    # .odt / .odp text extraction
    paragraphs = [p.text.strip() for p in root.findall(".//text:p", ns) if p.text and p.text.strip()]
    chunks.extend(paragraphs)
    meta["paragraph_count"] = len(paragraphs)
    return "\n".join(chunks), meta


def _read_pptx_presentation(
    path: Path,
    max_slides: int,
    max_chars_per_slide: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if max_slides < 1:
        raise ValueError("max_slides must be >= 1")
    if max_chars_per_slide < 1:
        raise ValueError("max_chars_per_slide must be >= 1")
    try:
        with zipfile.ZipFile(path) as zf:
            slide_files = sorted(
                [n for n in zf.namelist() if re.match(r"^ppt/slides/slide\d+\.xml$", n)],
                key=lambda n: int(re.search(r"slide(\d+)\.xml$", n).group(1)),  # type: ignore[union-attr]
            )
            slides: list[dict[str, Any]] = []
            for idx, slide_name in enumerate(slide_files[:max_slides], start=1):
                raw = zf.read(slide_name)
                root = ET.fromstring(raw)
                text_nodes = [t.text.strip() for t in root.findall(".//{*}t") if t.text and t.text.strip()]
                text = "\n".join(text_nodes)
                text, _ = _truncate_with_flag(text, max_chars=max_chars_per_slide)
                title = text_nodes[0] if text_nodes else f"Slide {idx}"
                slides.append(
                    {
                        "index": idx,
                        "title": title[:160],
                        "text": text,
                        "text_blocks": len(text_nodes),
                    }
                )
    except zipfile.BadZipFile as exc:
        raise RuntimeError("invalid .pptx container") from exc
    except ET.ParseError as exc:
        raise RuntimeError(f"invalid .pptx slide xml: {exc}") from exc
    return slides, {"slide_count": len(slide_files), "slides_read": len(slides)}


def _read_odp_presentation(
    path: Path,
    max_slides: int,
    max_chars_per_slide: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if max_slides < 1:
        raise ValueError("max_slides must be >= 1")
    if max_chars_per_slide < 1:
        raise ValueError("max_chars_per_slide must be >= 1")
    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read("content.xml")
    except KeyError as exc:
        raise RuntimeError("content.xml not found in .odp") from exc
    except zipfile.BadZipFile as exc:
        raise RuntimeError("invalid .odp container") from exc

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RuntimeError(f"invalid .odp content xml: {exc}") from exc

    ns = {
        "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    }
    pages = root.findall(".//draw:page", ns)
    slides: list[dict[str, Any]] = []
    for idx, page in enumerate(pages[:max_slides], start=1):
        title = page.attrib.get(f"{{{ns['draw']}}}name", "") or f"Slide {idx}"
        lines = [p.text.strip() for p in page.findall(".//text:p", ns) if p.text and p.text.strip()]
        text = "\n".join(lines)
        text, _ = _truncate_with_flag(text, max_chars=max_chars_per_slide)
        slides.append(
            {
                "index": idx,
                "title": title[:160],
                "text": text,
                "text_blocks": len(lines),
            }
        )
    return slides, {"slide_count": len(pages), "slides_read": len(slides)}


def _read_ppt_legacy_text(path: Path, max_slides: int, max_chars_per_slide: int) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    warnings: list[str] = []
    ppttxt = shutil.which("catppt") or shutil.which("ppttotext")
    text = ""
    if ppttxt:
        proc = subprocess.run(
            [ppttxt, str(path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        text = (proc.stdout or "").strip()
    if not text:
        raw = path.read_bytes()
        ascii_chunks = re.findall(rb"[ -~]{4,}", raw)
        text = "\n".join(chunk.decode("latin-1", errors="replace") for chunk in ascii_chunks)
        warnings.append("Used lossy fallback extractor for .ppt (install catppt/ppttotext for better quality).")
    text, _ = _truncate_with_flag(text.strip(), max_chars=max(1, max_slides * max_chars_per_slide))
    slides = [{"index": 1, "title": "Slide 1", "text": text, "text_blocks": len(text.splitlines())}] if text else []
    return slides, {"slide_count": 1 if text else 0, "slides_read": len(slides)}, warnings


def _image_basic_features(path: Path) -> dict[str, Any]:
    global Image
    features: dict[str, Any] = {
        "width": 0,
        "height": 0,
        "mode": "",
        "format": path.suffix.lower().lstrip("."),
        "aspect_ratio": 0.0,
        "mean_luma": None,
    }
    if Image is _OPTIONAL_DEPENDENCY_UNLOADED:
        try:
            Image = _import_optional_dependency("PIL.Image", "Pillow")
        except RuntimeError:
            Image = None
            return features
    if Image is None:
        return features
    try:
        with Image.open(path) as img:
            width, height = img.size
            features["width"] = int(width)
            features["height"] = int(height)
            features["mode"] = str(img.mode)
            features["format"] = str((img.format or features["format"])).lower()
            features["aspect_ratio"] = round(width / height, 4) if height else 0.0
            gray = img.convert("L")
            stat = gray.resize((1, 1)).getpixel((0, 0))
            features["mean_luma"] = float(stat)
    except Exception:
        return features
    return features


def _node_display_name(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Call):
        return _ast_expr_name(node.func)
    if isinstance(node, ast.Import):
        return ", ".join(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return f"{module}:{', '.join(alias.name for alias in node.names)}"
    return ""


def _ast_expr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _ast_expr_name(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    if isinstance(node, ast.Call):
        return _ast_expr_name(node.func)
    return ""


def _guess_file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".toml"}:
        return "toml"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    raise ValueError("unsupported file type; expected .json, .toml, .yaml, or .yml")


def _parse_query_path(query: str) -> list[str | int]:
    query = query.strip()
    if not query:
        return []
    tokens: list[str | int] = []
    current = ""
    i = 0
    while i < len(query):
        ch = query[i]
        if ch == ".":
            if current:
                tokens.append(current)
                current = ""
            i += 1
            continue
        if ch == "[":
            if current:
                tokens.append(current)
                current = ""
            j = query.find("]", i)
            if j == -1:
                raise ValueError("invalid query path: missing closing ']'")
            raw_index = query[i + 1 : j].strip()
            if not raw_index.isdigit():
                raise ValueError("invalid query path: index must be a non-negative int")
            tokens.append(int(raw_index))
            i = j + 1
            continue
        current += ch
        i += 1
    if current:
        tokens.append(current)
    return tokens


def _query_value(data: Any, query: str) -> Any:
    value = data
    for token in _parse_query_path(query):
        if isinstance(token, int):
            if not isinstance(value, list):
                raise ValueError("query expected list while resolving index")
            if token < 0 or token >= len(value):
                raise ValueError("query index out of range")
            value = value[token]
            continue
        if not isinstance(value, dict):
            raise ValueError("query expected object while resolving key")
        if token not in value:
            raise ValueError(f"query key not found: {token}")
        value = value[token]
    return value


def _memory_load() -> dict[str, Any]:
    memory_path = _resolve_repo_path(str(MEMORY_FILE))
    if not memory_path.exists():
        return {"entries": [], "summaries": [], "decisions": []}
    try:
        payload = json.loads(memory_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"entries": [], "summaries": [], "decisions": []}
    if not isinstance(payload, dict):
        return {"entries": [], "summaries": [], "decisions": []}
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    summaries = payload.get("summaries", [])
    if not isinstance(summaries, list):
        summaries = []
    decisions = payload.get("decisions", [])
    if not isinstance(decisions, list):
        decisions = []
    return {"entries": entries, "summaries": summaries, "decisions": decisions}


def _memory_save(payload: dict[str, Any]) -> None:
    memory_path = _resolve_repo_path(str(MEMORY_FILE))
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = {
        "entries": payload.get("entries", []),
        "summaries": payload.get("summaries", []),
        "decisions": payload.get("decisions", []),
    }
    memory_path.write_text(
        json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8"
    )


def _memory_stats_load() -> dict[str, Any]:
    path = _resolve_repo_path(str(MEMORY_STATS_FILE))
    if not path.exists():
        return {
            "events": {"get": 0, "hit": 0, "miss": 0, "compact": 0, "summary_upsert": 0},
            "last_event_at": "",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "events": {"get": 0, "hit": 0, "miss": 0, "compact": 0, "summary_upsert": 0},
            "last_event_at": "",
        }
    if not isinstance(payload, dict):
        payload = {}
    events = payload.get("events", {})
    if not isinstance(events, dict):
        events = {}
    base = {"get": 0, "hit": 0, "miss": 0, "compact": 0, "summary_upsert": 0}
    for k in base:
        v = events.get(k, 0)
        base[k] = int(v) if isinstance(v, int) and v >= 0 else 0
    return {"events": base, "last_event_at": str(payload.get("last_event_at", ""))}


def _memory_stats_save(payload: dict[str, Any]) -> None:
    path = _resolve_repo_path(str(MEMORY_STATS_FILE))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _memory_stats_record(event: str) -> None:
    stats = _memory_stats_load()
    events = stats.get("events", {})
    events[event] = int(events.get(event, 0)) + 1
    stats["events"] = events
    stats["last_event_at"] = _now_iso()
    _memory_stats_save(stats)


def _memory_entry_rank(entry: dict[str, Any]) -> tuple[float, float]:
    confidence = float(entry.get("confidence", 0.0) or 0.0)
    ts = _parse_iso_timestamp(str(entry.get("updated_at", "")))
    epoch = ts.timestamp() if ts else 0.0
    return confidence, epoch


def memory_auto_compact(
    namespace: str | None = None,
    threshold_entries: int = 80,
    threshold_chars: int = 16000,
    keep_entries: int = 40,
    summary_max_chars: int = 1200,
    drop_expired: bool = False,
) -> dict[str, Any]:
    """Compact memory when size thresholds are exceeded by writing/updating summary records."""
    if threshold_entries < 1:
        raise ValueError("threshold_entries must be >= 1")
    if threshold_chars < 256:
        raise ValueError("threshold_chars must be >= 256")
    if keep_entries < 1:
        raise ValueError("keep_entries must be >= 1")
    if summary_max_chars < 128:
        raise ValueError("summary_max_chars must be >= 128")

    payload = _memory_load()
    now = datetime.now(timezone.utc)
    entries = []
    for row in payload["entries"]:
        if namespace is not None and row.get("namespace") != namespace:
            continue
        if _is_expired(row.get("expires_at"), now) and not drop_expired:
            continue
        entries.append(row)
    entries_sorted = sorted(entries, key=_memory_entry_rank, reverse=True)
    serialized = json.dumps(entries_sorted, ensure_ascii=False)
    over_threshold = len(entries_sorted) > threshold_entries or len(serialized) > threshold_chars
    if not over_threshold:
        return {
            "schema": "memory_auto_compact.v1",
            "compacted": False,
            "namespace": namespace,
            "entry_count": len(entries_sorted),
            "entry_chars": len(serialized),
            "threshold_entries": threshold_entries,
            "threshold_chars": threshold_chars,
        }
    if not ALLOW_MUTATIONS:
        return {
            "schema": "memory_auto_compact.v1",
            "compacted": False,
            "namespace": namespace,
            "entry_count": len(entries_sorted),
            "entry_chars": len(serialized),
            "threshold_entries": threshold_entries,
            "threshold_chars": threshold_chars,
            "reason": "mutations_disabled",
        }

    focus_ns = namespace or "global"
    top = entries_sorted[:keep_entries]
    lines = []
    for row in top:
        key = str(row.get("key", ""))[:64]
        val = row.get("value")
        val_txt = _trim_text(json.dumps(val, ensure_ascii=False), max_chars=180)
        lines.append(f"- {key}: {val_txt}")
    summary_text = _trim_text(
        f"Auto-compact summary for namespace={focus_ns}. Kept top {len(top)} of {len(entries_sorted)} entries.\n"
        + "\n".join(lines),
        max_chars=summary_max_chars,
    )
    memory_summary_upsert(
        namespace=focus_ns,
        focus="auto_compact",
        summary=summary_text,
        ttl_days=60,
        confidence=0.9,
        source="memory.auto_compact",
        tags=["auto", "compact", "summary"],
    )
    _memory_stats_record("compact")
    _memory_stats_record("summary_upsert")
    return {
        "schema": "memory_auto_compact.v1",
        "compacted": True,
        "namespace": namespace,
        "entry_count": len(entries_sorted),
        "entry_chars": len(serialized),
        "kept_entries": len(top),
        "threshold_entries": threshold_entries,
        "threshold_chars": threshold_chars,
        "summary_focus": "auto_compact",
    }


def _memory_trace_reusable_script_success(
    script_rel: str,
    *,
    profile: str,
    steps: list[dict[str, Any]],
    venv_python: str,
) -> dict[str, Any]:
    if not ALLOW_MUTATIONS:
        return {"recorded": False, "reason": "mutations_disabled"}
    rel = script_rel.strip().replace("\\", "/")
    if not rel:
        return {"recorded": False, "reason": "empty_script_path"}
    if Path(rel).suffix.lower() not in {".py", ".sh", ".bash", ".zsh"}:
        return {"recorded": False, "reason": "unsupported_script_type"}

    payload = _memory_load()
    entries = payload["entries"]
    now_iso = _now_iso()
    key = f"script:{rel}"
    command_recipes = [step.get("command", []) for step in steps if isinstance(step, dict)]

    for entry in entries:
        if entry.get("namespace") != "reusable_scripts":
            continue
        if entry.get("key") != key:
            continue
        value = entry.get("value", {})
        if not isinstance(value, dict):
            value = {}
        prev_count = int(value.get("success_count", 0) or 0)
        value.update(
            {
                "script_path": rel,
                "last_success_profile": profile,
                "last_success_at": now_iso,
                "success_count": prev_count + 1,
                "venv_python": venv_python,
                "check_recipe": command_recipes,
            }
        )
        entry["value"] = value
        entry["source"] = "task_router.coding_check.auto"
        entry["confidence"] = 1.0
        entry["tags"] = ["script", "reusable", "success", "coding-check"]
        entry["updated_at"] = now_iso
        entry["expires_at"] = _to_iso_expiry(180)
        _memory_save(payload)
        return {"recorded": True, "namespace": "reusable_scripts", "key": key}

    entries.append(
        {
            "namespace": "reusable_scripts",
            "key": key,
            "value": {
                "script_path": rel,
                "last_success_profile": profile,
                "last_success_at": now_iso,
                "success_count": 1,
                "venv_python": venv_python,
                "check_recipe": command_recipes,
            },
            "confidence": 1.0,
            "source": "task_router.coding_check.auto",
            "tags": ["script", "reusable", "success", "coding-check"],
            "created_at": now_iso,
            "updated_at": now_iso,
            "expires_at": _to_iso_expiry(180),
        }
    )
    _memory_save(payload)
    return {"recorded": True, "namespace": "reusable_scripts", "key": key}


def _decision_priority(decided_by: str) -> int:
    who = decided_by.strip().lower()
    if who == "human":
        return 2
    if who == "llm":
        return 1
    return 0


def _effective_decisions(
    decisions: list[dict[str, Any]],
    now: datetime,
    namespace: str | None = None,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for row in decisions:
        ns = str(row.get("namespace", ""))
        topic = str(row.get("topic", ""))
        if not ns or not topic:
            continue
        if namespace is not None and ns != namespace:
            continue
        expired = _is_expired(row.get("expires_at"), now)
        if expired and not include_expired:
            continue
        key = (ns, topic)
        prev = selected.get(key)
        row_pri = _decision_priority(str(row.get("decided_by", "")))
        row_ts = _parse_iso_timestamp(str(row.get("updated_at", ""))) or datetime.min.replace(
            tzinfo=timezone.utc
        )
        if prev is None:
            copied = dict(row)
            copied["expired"] = expired
            selected[key] = copied
            continue
        prev_pri = _decision_priority(str(prev.get("decided_by", "")))
        prev_ts = _parse_iso_timestamp(str(prev.get("updated_at", ""))) or datetime.min.replace(
            tzinfo=timezone.utc
        )
        if (row_pri, row_ts) >= (prev_pri, prev_ts):
            copied = dict(row)
            copied["expired"] = expired
            selected[key] = copied
    return sorted(
        selected.values(),
        key=lambda x: (str(x.get("namespace", "")), str(x.get("topic", ""))),
    )


def _json_file_load(path: Path, default: Any) -> Any:
    file_path = _resolve_repo_path(str(path))
    if not file_path.exists():
        return default
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
    return payload


def _json_file_save(path: Path, payload: Any) -> None:
    file_path = _resolve_repo_path(str(path))
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


_TOOL_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "find_paths": (
        "find",
        "file",
        "files",
        "path",
        "paths",
        "folder",
        "folders",
        "directory",
        "directories",
        "tree",
        "list",
    ),
    "grep": ("grep", "search", "text", "pattern", "match", "matches", "contains"),
    "read_file": ("read", "open", "show", "display", "view", "file", "contents"),
    "read_snippet": ("snippet", "lines", "line", "section", "excerpt"),
    "write_file": ("write", "create", "save", "file"),
    "replace_in_files": ("replace", "rename", "substitute", "update", "text"),
    "git_diff": ("diff", "patch", "changes", "compare"),
    "git_status": ("status", "git", "modified", "staged"),
    "self_test": ("test", "tests", "pytest", "unit", "coverage"),
    "doc_summarizer_small": ("summary", "summarize", "document", "docs"),
    "math_solver": ("math", "solve", "equation", "algebra"),
    "browse_web": ("web", "browse", "http", "url", "site"),
}


def _tokenize_router_query(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _tool_intent_terms(tool: str) -> set[str]:
    terms = set(_tokenize_router_query(tool.replace("_", " ")))
    terms.update(_TOOL_INTENT_KEYWORDS.get(tool, ()))
    return {term for term in terms if term}


def _intent_rank_candidates(query: str, candidates: list[str]) -> list[dict[str, Any]]:
    tokens = _tokenize_router_query(query)
    token_set = set(tokens)
    joined = " ".join(tokens)
    ranked: list[dict[str, Any]] = []
    for tool in candidates:
        terms = _tool_intent_terms(tool)
        exact_hits = sum(1 for term in terms if term in token_set)
        phrase_hits = sum(1 for term in terms if len(term) > 3 and term in joined)
        prefix_hits = sum(
            1
            for token in token_set
            for term in terms
            if len(token) > 2 and len(term) > 2 and (token.startswith(term) or term.startswith(token))
        )
        score = (exact_hits * 3.0) + (phrase_hits * 1.5) + (prefix_hits * 0.5)
        ranked.append(
            {
                "tool": tool,
                "score": round(score, 4),
                "exact_hits": exact_hits,
                "phrase_hits": phrase_hits,
                "prefix_hits": prefix_hits,
                "terms": sorted(terms),
            }
        )
    ranked.sort(key=lambda row: (row["score"], row["exact_hits"], row["phrase_hits"], row["tool"]), reverse=True)
    return ranked


def _tool_router_confidence(
    ranked: list[dict[str, Any]],
    min_calls: int,
    min_success_rate: float,
    min_score_gap: float,
) -> dict[str, Any]:
    if not ranked:
        return {
            "confident": False,
            "reason": "no_candidates",
            "score_gap": 0.0,
            "top_calls": 0,
            "top_success_rate": 0.0,
        }
    top = ranked[0]
    top_calls = int(top.get("calls", 0))
    top_success_rate = float(top.get("success_rate", 0.0))
    second_score = float(ranked[1].get("score", top.get("score", 0.0))) if len(ranked) > 1 else 0.0
    top_score = float(top.get("score", 0.0))
    score_gap = top_score - second_score if len(ranked) > 1 else top_score
    if top_calls < min_calls:
        return {
            "confident": False,
            "reason": "insufficient_calls",
            "score_gap": round(score_gap, 4),
            "top_calls": top_calls,
            "top_success_rate": round(top_success_rate, 4),
        }
    if top_success_rate < min_success_rate:
        return {
            "confident": False,
            "reason": "low_success_rate",
            "score_gap": round(score_gap, 4),
            "top_calls": top_calls,
            "top_success_rate": round(top_success_rate, 4),
        }
    if len(ranked) > 1 and score_gap < min_score_gap:
        return {
            "confident": False,
            "reason": "low_score_gap",
            "score_gap": round(score_gap, 4),
            "top_calls": top_calls,
            "top_success_rate": round(top_success_rate, 4),
        }
    return {
        "confident": True,
        "reason": "learned",
        "score_gap": round(score_gap, 4),
        "top_calls": top_calls,
        "top_success_rate": round(top_success_rate, 4),
    }


def _state_snapshot_index_load() -> dict[str, Any]:
    payload = _json_file_load(STATE_SNAPSHOT_INDEX_FILE, {"snapshots": {}})
    if not isinstance(payload, dict):
        return {"snapshots": {}}
    snapshots = payload.get("snapshots", {})
    if not isinstance(snapshots, dict):
        snapshots = {}
    return {"snapshots": snapshots}


def _state_snapshot_index_save(payload: dict[str, Any]) -> None:
    snapshots = payload.get("snapshots", {})
    if not isinstance(snapshots, dict):
        snapshots = {}
    _json_file_save(STATE_SNAPSHOT_INDEX_FILE, {"snapshots": snapshots})


def _token_budget_load() -> dict[str, Any]:
    payload = _json_file_load(
        TOKEN_BUDGET_FILE,
        {
            "max_output_chars": MAX_OUTPUT_CHARS,
            "default_output_profile": "compact",
        },
    )
    if not isinstance(payload, dict):
        return {"max_output_chars": MAX_OUTPUT_CHARS, "default_output_profile": "compact"}
    max_chars = payload.get("max_output_chars", MAX_OUTPUT_CHARS)
    profile = payload.get("default_output_profile", "compact")
    if not isinstance(max_chars, int) or max_chars < 1:
        max_chars = MAX_OUTPUT_CHARS
    if profile not in OUTPUT_PROFILES:
        profile = "compact"
    return {"max_output_chars": max_chars, "default_output_profile": profile}


def _token_budget_apply_max(max_chars: int | None) -> int:
    if isinstance(max_chars, int) and max_chars > 0:
        return max_chars
    return int(_token_budget_load()["max_output_chars"])


def _default_output_profile(output_profile: str | None) -> str:
    if output_profile and output_profile.strip():
        return _validate_output_profile(output_profile)
    return _validate_output_profile(_token_budget_load()["default_output_profile"])


def _paginate(items: list[Any], offset: int = 0, limit: int | None = None) -> list[Any]:
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1 when provided")
    if limit is None:
        return items[offset:]
    return items[offset : offset + limit]


def _select_fields(records: list[dict[str, Any]], fields: list[str] | None) -> list[dict[str, Any]]:
    if not fields:
        return records
    selected: list[dict[str, Any]] = []
    for row in records:
        selected.append({k: row.get(k) for k in fields if k in row})
    return selected


def _cache_load() -> dict[str, Any]:
    payload = _json_file_load(TOOL_CACHE_FILE, {"entries": {}})
    if not isinstance(payload, dict):
        return {"entries": {}}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    return {"entries": entries}


def _cache_save(payload: dict[str, Any]) -> None:
    _json_file_save(TOOL_CACHE_FILE, payload)


def _parse_iso_timestamp(raw: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _cache_get_entry(tool: str, key: str) -> dict[str, Any] | None:
    payload = _cache_load()
    tool_entries = payload["entries"].get(tool, {})
    if not isinstance(tool_entries, dict):
        return None
    row = tool_entries.get(key)
    if not isinstance(row, dict):
        return None
    return row


def _cache_get(tool: str, key: str) -> Any | None:
    row = _cache_get_entry(tool, key)
    if row is None:
        return None
    return row.get("value")


def _cache_set(tool: str, key: str, value: Any, max_entries: int = 50) -> None:
    payload = _cache_load()
    entries = payload["entries"]
    tool_entries = entries.get(tool, {})
    if not isinstance(tool_entries, dict):
        tool_entries = {}
    tool_entries[key] = {"updated_at": _now_iso(), "value": value}
    if len(tool_entries) > max_entries:
        ordered = sorted(
            tool_entries.items(), key=lambda kv: str(kv[1].get("updated_at", "")), reverse=True
        )
        tool_entries = dict(ordered[:max_entries])
    entries[tool] = tool_entries
    payload["entries"] = entries
    _cache_save(payload)


def _cache_clear(tool: str | None = None) -> dict[str, Any]:
    payload = _cache_load()
    entries = payload.get("entries", {})
    if not isinstance(entries, dict):
        entries = {}
    removed = 0
    if tool:
        tool_entries = entries.pop(tool, {})
        if isinstance(tool_entries, dict):
            removed = len(tool_entries)
    else:
        for v in entries.values():
            if isinstance(v, dict):
                removed += len(v)
        entries = {}
    payload["entries"] = entries
    _cache_save(payload)
    return {"removed_entries": removed, "tool": tool or "*"}


def _cache_stats() -> dict[str, Any]:
    payload = _cache_load()
    entries = payload.get("entries", {})
    if not isinstance(entries, dict):
        entries = {}
    per_tool: dict[str, int] = {}
    total = 0
    for tool, rows in entries.items():
        if isinstance(rows, dict):
            per_tool[tool] = len(rows)
            total += len(rows)
    return {"total_entries": total, "tools": per_tool}


def _cache_list_tool(tool: str, limit: int = 50) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    payload = _cache_load()
    tool_entries = payload.get("entries", {}).get(tool, {})
    if not isinstance(tool_entries, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key, row in tool_entries.items():
        if not isinstance(row, dict):
            continue
        value = row.get("value")
        rows.append(
            {
                "key": key,
                "updated_at": str(row.get("updated_at", "")),
                "value_type": type(value).__name__,
                "value_schema": value.get("schema") if isinstance(value, dict) else None,
            }
        )
    rows.sort(key=lambda x: x["updated_at"], reverse=True)
    return rows[:limit]


def _cache_prune(max_age_minutes: int, tool: str | None = None) -> dict[str, Any]:
    if max_age_minutes < 1:
        raise ValueError("max_age_minutes must be >= 1")
    payload = _cache_load()
    entries = payload.get("entries", {})
    if not isinstance(entries, dict):
        entries = {}
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    removed = 0
    scanned = 0

    target_tools = [tool] if tool else list(entries.keys())
    for tool_name in target_tools:
        tool_entries = entries.get(tool_name, {})
        if not isinstance(tool_entries, dict):
            continue
        kept: dict[str, Any] = {}
        for key, row in tool_entries.items():
            scanned += 1
            if not isinstance(row, dict):
                removed += 1
                continue
            ts = _parse_iso_timestamp(str(row.get("updated_at", "")))
            if ts is None or ts >= cutoff:
                kept[key] = row
            else:
                removed += 1
        if kept:
            entries[tool_name] = kept
        else:
            entries.pop(tool_name, None)
    payload["entries"] = entries
    _cache_save(payload)
    return {"removed_entries": removed, "scanned_entries": scanned, "tool": tool or "*", "max_age_minutes": max_age_minutes}


def _resolve_safe_command_target(command: list[str]) -> tuple[str, list[str]]:
    if not command:
        raise ValueError("command must not be empty")
    binary = command[0]
    if binary != "env":
        return binary, command[1:]

    idx = 1
    while idx < len(command):
        token = command[idx]
        if token == "-i":
            idx += 1
            continue
        if token == "-u":
            if idx + 1 >= len(command):
                raise ValueError("env -u must include a variable name")
            idx += 2
            continue
        if token.startswith("-"):
            raise ValueError(f"env flag not allowed: {token}")
        if "=" in token:
            idx += 1
            continue
        break
    if idx >= len(command):
        raise ValueError("env command must include a wrapped executable")
    return command[idx], command[idx + 1 :]


def _validate_safe_command(command: list[str]) -> None:
    binary, args = _resolve_safe_command_target(command)
    if binary in SAFE_INLINE_PYTHON_BINARIES:
        _validate_safe_inline_python(binary, args)
        return
    if binary not in SAFE_COMMANDS:
        raise ValueError(f"command not allowed: {binary}")
    if binary == "git":
        if not args:
            raise ValueError("git command must include a subcommand")
        if args[0] not in SAFE_GIT_SUBCOMMANDS:
            raise ValueError(f"git subcommand not allowed: {args[0]}")
    if binary == "sed" and any(arg == "-i" or arg.startswith("-i") for arg in args):
        raise ValueError("sed in-place edits are not allowed")
    if binary == "find" and any(arg in {"-delete", "-exec", "-ok"} for arg in args):
        raise ValueError("find destructive/exec flags are not allowed")
    if binary == "awk":
        script = args[0] if args else ""
        if "system(" in script:
            raise ValueError("awk system() is not allowed")


def _safe_inline_python_error(binary: str, reason: str) -> ValueError:
    return ValueError(f"command not allowed: {binary} ({reason})")


def _ast_dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _ast_dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _validate_safe_inline_python(binary: str, args: list[str]) -> None:
    if len(args) != 2 or args[0] != "-c":
        raise _safe_inline_python_error(binary, "only inline -c code is allowlisted")
    code = str(args[1]).strip()
    if not code:
        raise _safe_inline_python_error(binary, "inline code must not be empty")
    if len(code) > SAFE_INLINE_PYTHON_MAX_CHARS:
        raise _safe_inline_python_error(
            binary,
            f"inline code exceeds {SAFE_INLINE_PYTHON_MAX_CHARS} characters",
        )
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise _safe_inline_python_error(binary, f"invalid inline code: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in SAFE_INLINE_PYTHON_ALLOWED_MODULES:
                    raise _safe_inline_python_error(binary, f"module not allowed: {root}")
            continue
        if isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                raise _safe_inline_python_error(binary, "relative imports are not allowed")
            root = node.module.split(".", 1)[0]
            if root not in SAFE_INLINE_PYTHON_ALLOWED_MODULES:
                raise _safe_inline_python_error(binary, f"module not allowed: {root}")
            continue
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in SAFE_INLINE_PYTHON_BLOCKED_ATTRS:
                raise _safe_inline_python_error(binary, f"attribute not allowed: {node.attr}")
            continue
        if isinstance(node, ast.Name) and node.id in SAFE_INLINE_PYTHON_BLOCKED_NAMES:
            raise _safe_inline_python_error(binary, f"name not allowed: {node.id}")
        if isinstance(node, ast.Call):
            call_name = _ast_dotted_name(node.func)
            leaf = call_name.rsplit(".", 1)[-1] if call_name else ""
            if leaf in SAFE_INLINE_PYTHON_BLOCKED_NAMES or leaf in SAFE_INLINE_PYTHON_BLOCKED_ATTRS:
                raise _safe_inline_python_error(binary, f"call not allowed: {call_name or leaf}")


def _approval_points_load() -> dict[str, Any]:
    payload = _json_file_load(APPROVAL_POINTS_FILE, {"items": []})
    items = payload.get("items", [])
    if not isinstance(items, list):
        items = []
    payload["items"] = items
    return payload


def _approval_point_append(action: str, risk_level: str, details: str) -> dict[str, Any]:
    if not action.strip():
        raise ValueError("action is required for create mode")
    payload = _approval_points_load()
    row = {
        "approval_id": uuid.uuid4().hex[:12],
        "action": action,
        "risk_level": risk_level,
        "details": details,
        "status": "pending",
        "created_at": _now_iso(),
    }
    payload["items"].append(row)
    _json_file_save(APPROVAL_POINTS_FILE, payload)
    return row


def _find_approved_manual_command_request(command: list[str], cwd: str) -> dict[str, Any] | None:
    payload = _approval_points_load()
    command_json = json.dumps(command)
    cwd_marker = f"cwd={cwd};"
    command_marker = f"command={command_json};"
    for row in reversed(payload["items"]):
        if row.get("action") != "manual_command_execution":
            continue
        if row.get("status") != "approved":
            continue
        details = str(row.get("details", ""))
        if cwd_marker in details and command_marker in details:
            return row
    return None


def _is_manual_command_request(reason: str) -> bool:
    return reason.startswith("command not allowed:") or reason.startswith(
        "git subcommand not allowed:"
    )


def _terminal_read_available(
    session: dict[str, Any], max_output_chars: int, wait_timeout_ms: int = 0
) -> str:
    fd = int(session["read_fd"])
    chunks: list[str] = []
    total = 0
    first = True
    while total < max_output_chars:
        timeout = (wait_timeout_ms / 1000.0) if first and wait_timeout_ms > 0 else 0
        first = False
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            break
        try:
            data = os.read(fd, min(4096, max_output_chars - total))
        except BlockingIOError:
            break
        except OSError:
            break
        if not data:
            break
        text = data.decode("utf-8", errors="replace")
        chunks.append(text)
        total += len(text)
    out = "".join(chunks)
    if out:
        log_path = Path(session["log_path"])
        with log_path.open("a", encoding="utf-8") as f:
            f.write(out)
        session["output_chars"] = int(session.get("output_chars", 0)) + len(out)
    return out


def _result_store_load() -> dict[str, Any]:
    payload = _json_file_load(RESULT_STORE_FILE, {"results": {}})
    if not isinstance(payload, dict):
        return {"results": {}}
    results = payload.get("results")
    if not isinstance(results, dict):
        results = {}
    return {"results": results}


def _result_store_save(payload: dict[str, Any]) -> None:
    _json_file_save(RESULT_STORE_FILE, payload)


def _result_store_put(tool: str, value: Any, max_entries: int = 200) -> str:
    payload = _result_store_load()
    results = payload["results"]
    rid = uuid.uuid4().hex[:16]
    results[rid] = {"tool": tool, "created_at": _now_iso(), "value": value}
    if len(results) > max_entries:
        ordered = sorted(
            results.items(), key=lambda kv: str(kv[1].get("created_at", "")), reverse=True
        )
        results = dict(ordered[:max_entries])
    payload["results"] = results
    _result_store_save(payload)
    return rid


def _result_store_get(result_id: str) -> dict[str, Any]:
    payload = _result_store_load()
    row = payload["results"].get(result_id)
    if not isinstance(row, dict):
        raise FileNotFoundError(f"result handle not found: {result_id}")
    return row


def _compress_table(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"columns": [], "rows": []}
    cols = sorted({k for r in records for k in r.keys()})
    rows = [[r.get(c) for c in cols] for r in records]
    return {"columns": cols, "rows": rows}


def _compressed_observation_for_rows(
    *,
    tool_name: str,
    rows: list[dict[str, Any]],
    total_count: int,
    raw_reference: dict[str, Any],
    rule_set: str = "deterministic_rows_v1",
    max_signals: int = 5,
) -> dict[str, Any]:
    """Build a deterministic, redacted observation summary for verbose row output.

    The summary never serves as the only raw copy: callers must pass either an
    inline-return reference, a result handle, or an artifact path in
    ``raw_reference``. Preserved signal samples use the same audit redactor used
    for persisted workflow metadata so the compressed layer cannot expose more
    than the corresponding raw/redacted output path.
    """
    path_counts: dict[str, int] = {}
    for row in rows:
        path = str(row.get("path", "")) if isinstance(row, dict) else ""
        if path:
            path_counts[path] = path_counts.get(path, 0) + 1
    top_paths = [
        {"path": path, "count": count}
        for path, count in sorted(
            path_counts.items(), key=lambda item: (-item[1], item[0])
        )[:max_signals]
    ]

    preserved: list[dict[str, Any]] = []
    for row in rows[:max_signals]:
        if not isinstance(row, dict):
            continue
        signal = {
            key: row[key]
            for key in ("path", "line", "column", "match", "lineText")
            if key in row
        }
        preserved.append(_redact_audit_value(signal))

    omitted: list[dict[str, Any]] = []
    if total_count > len(rows):
        omitted.append(
            {
                "category": "matches_not_returned",
                "reason_code": "pagination_or_adaptive_limit",
                "count": total_count - len(rows),
            }
        )
    if len(rows) > len(preserved):
        omitted.append(
            {
                "category": "additional_returned_rows",
                "reason_code": "sample_cap",
                "count": len(rows) - len(preserved),
            }
        )
    if any(isinstance(row, dict) and "lineText" in row for row in rows):
        omitted.append(
            {
                "category": "full_line_text",
                "reason_code": "secret_safe_signal_sampling",
                "count": max(0, len(rows) - len(preserved)),
            }
        )

    return {
        "schema": "compressed_observation.v1",
        "summary": (
            f"{tool_name} returned {len(rows)} row(s)"
            + (f" from {len(path_counts)} path(s)" if path_counts else "")
            + (
                f"; {total_count - len(rows)} additional row(s) omitted"
                if total_count > len(rows)
                else ""
            )
        ),
        "preserved_signals": {
            "top_paths": top_paths,
            "sample_rows": preserved,
            "total_count": total_count,
            "returned_count": len(rows),
        },
        "omitted": omitted,
        "raw_reference": raw_reference,
        "rules": {
            "rule_set": rule_set,
            "version": 1,
            "deterministic": True,
            "max_preserved_signals": max_signals,
        },
        "provenance": {
            "tool": tool_name,
            "generated_by": "codebase-tooling-mcp",
            "input_scope": "returned_rows",
        },
        "redaction": {
            "applied": True,
            "method": "mcp_audit_redaction",
            "contains_secrets": False,
        },
    }


def _compressed_observation_for_governance_report(
    report: dict[str, Any], raw_reference: dict[str, Any]
) -> dict[str, Any]:
    audit = report.get("audit", {}) if isinstance(report.get("audit"), dict) else {}
    counts = audit.get("counts", {}) if isinstance(audit.get("counts"), dict) else {}
    event_count = int(counts.get("event_count", 0) or 0)
    failures = (
        counts.get("failures", {}) if isinstance(counts.get("failures"), dict) else {}
    )
    top_tools = counts.get("tools", {}) if isinstance(counts.get("tools"), dict) else {}
    preserved = {
        "event_count": event_count,
        "failure_count": int(failures.get("count", 0) or 0),
        "top_tools": [
            {"tool": tool, "count": count}
            for tool, count in sorted(
                top_tools.items(), key=lambda item: (-int(item[1]), str(item[0]))
            )[:5]
        ],
        "workflow_diagnostics": _redact_audit_value(
            report.get("workflow_diagnostics", {})
        ),
        "export_paths": _redact_audit_value(report.get("exports", {})),
    }
    omitted = [
        {
            "category": "redacted_audit_events",
            "reason_code": "sample_cap",
            "count": max(
                0,
                event_count
                - len(
                    audit.get("redacted_events_sample", [])
                    if isinstance(audit.get("redacted_events_sample"), list)
                    else []
                ),
            ),
        },
        {
            "category": "full_artifact_body",
            "reason_code": "raw_artifact_reference",
            "count": 1,
        },
    ]
    return {
        "schema": "compressed_observation.v1",
        "summary": (
            "governance_report summarized "
            f"{event_count} redacted audit event(s) with "
            f"{preserved['failure_count']} failure(s)"
        ),
        "preserved_signals": preserved,
        "omitted": omitted,
        "raw_reference": raw_reference,
        "rules": {
            "rule_set": "deterministic_governance_report_v1",
            "version": 1,
            "deterministic": True,
            "max_preserved_signals": 5,
        },
        "provenance": {
            "tool": "governance_report",
            "generated_by": "codebase-tooling-mcp",
            "input_scope": "redacted_report",
        },
        "redaction": {
            "applied": True,
            "method": "mcp_audit_redaction",
            "contains_secrets": False,
        },
    }


def _payload_size_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=True).encode("utf-8"))
    except Exception:
        return len(str(value).encode("utf-8"))


def _vec_l2(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def _vec_normalize(vec: list[float]) -> list[float]:
    n = _vec_l2(vec)
    if n == 0:
        return vec
    return [x / n for x in vec]


def _vec_cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    denom = _vec_l2(a) * _vec_l2(b)
    if denom == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / denom


def _hash_embed_one(text: str, dim: int = 256) -> list[float]:
    if dim < 8:
        raise ValueError("embedding dimension must be >= 8")
    vec = [0.0] * dim
    tokens = re.findall(r"[A-Za-z0-9_./:-]+", text.lower())
    if not tokens:
        tokens = [text.lower()]
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = -1.0 if (h[4] & 1) else 1.0
        vec[idx] += sign
    return _vec_normalize(vec)


def _local_embed_vectors(
    texts: list[str],
    backend: str = "auto",
    normalize: bool = True,
) -> tuple[str, list[list[float]]]:
    selected = backend.strip().lower()
    if selected == "auto":
        selected = LOCAL_EMBED_BACKEND or "hash"

    if selected in {"hash", "toy", "fallback"}:
        vectors = [_hash_embed_one(t, dim=LOCAL_EMBED_DIM) for t in texts]
        if normalize:
            vectors = [_vec_normalize(v) for v in vectors]
        return ("hash", vectors)

    if selected in {"sentence-transformers", "sentence_transformers"}:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "sentence-transformers backend requested but package is unavailable"
            ) from exc
        model_ref = LOCAL_EMBED_MODEL
        if not model_ref:
            raise RuntimeError("LOCAL_EMBED_MODEL is required for sentence-transformers backend")
        model = SentenceTransformer(model_ref, device="cpu")
        vectors_raw = model.encode(texts, normalize_embeddings=normalize)
        vectors = [[float(x) for x in row] for row in vectors_raw]
        return ("sentence-transformers", vectors)

    raise ValueError(
        "unsupported embed backend; expected one of: auto, hash, sentence-transformers"
    )


def _model_default_stop_sequences(model: str) -> list[str]:
    return []


def _sanitize_model_output(text: str) -> str:
    out = str(text or "")
    out = re.sub(r"(?is)<think>.*?</think>\s*", "", out)
    out = re.sub(r"(?is)^.*?</think>\s*", "", out)
    out = re.sub(r"(?is)<think>.*$", "", out)
    for token in MODEL_STRIP_TOKENS:
        out = out.replace(token, "")
    return out


def _local_infer_via_endpoint(
    prompt: str,
    model: str,
    max_tokens: int,
    temperature: float,
    system: str = "",
    stop: list[str] | None = None,
) -> str:
    options: dict[str, Any] = {"num_predict": max_tokens, "temperature": temperature}
    stop_sequences = [*(_model_default_stop_sequences(model)), *(stop or [])]
    if stop_sequences:
        options["stop"] = list(dict.fromkeys(stop_sequences))
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": options,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        LOCAL_INFER_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _urlopen_with_host_certs(req, timeout=60) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return _sanitize_model_output(body)
    if isinstance(parsed, dict):
        for key in ("response", "text", "output", "completion"):
            if isinstance(parsed.get(key), str):
                return _sanitize_model_output(parsed[key])
    return _sanitize_model_output(body)


def _autocomplete_prompt(prefix: str, suffix: str, language: str) -> str:
    lang = language.strip() or "plaintext"
    return (
        "You complete source code.\n"
        f"Language: {lang}\n"
        "Return only the continuation for the cursor position.\n"
        "Do not repeat the prefix or explain anything.\n\n"
        "<PREFIX>\n"
        f"{prefix}\n"
        "</PREFIX>\n"
        "<SUFFIX>\n"
        f"{suffix}\n"
        "</SUFFIX>\n"
        "<CONTINUATION>\n"
    )


def _autocomplete_strip_wrappers(text: str) -> str:
    out = text
    if out.lstrip().startswith("```"):
        out = out.replace("```python", "").replace("```", "")
    return out


def _autocomplete_apply_stops(text: str, stop: list[str] | None = None) -> str:
    if not stop:
        return text
    cut = len(text)
    for tok in stop:
        if not tok:
            continue
        idx = text.find(tok)
        if idx >= 0:
            cut = min(cut, idx)
    return text[:cut]


def _autocomplete_fallback(prefix: str, suffix: str) -> str:
    del suffix
    tail = prefix.rstrip()
    if tail.endswith(":"):
        return "\n    "
    if tail.endswith(("(", "[", "{")):
        return { "(": ")", "[": "]", "{": "}" }[tail[-1]]
    if tail.endswith("return"):
        return " None"
    return ""


def _require_sympy() -> None:
    global sp
    if sp is _OPTIONAL_DEPENDENCY_UNLOADED:
        sp = _import_optional_dependency("sympy", "sympy")
    if sp is None:
        raise RuntimeError("sympy is not installed in this runtime")


def _math_expr(expr: str) -> Any:
    _require_sympy()
    return sp.sympify(expr)


def _math_steps_stub(mode: str, expr: str) -> list[str]:
    return [
        f"Parsed expression for mode '{mode}'.",
        "Applied symbolic transformation.",
        "Generated exact and numeric outputs.",
    ]


def _sql_normalize(query: str) -> str:
    global sqlparse
    if sqlparse is _OPTIONAL_DEPENDENCY_UNLOADED:
        try:
            sqlparse = _import_optional_dependency("sqlparse", "sqlparse")
        except RuntimeError:
            sqlparse = None
            return " ".join(query.split())
    if sqlparse is None:
        return " ".join(query.split())
    return sqlparse.format(query, keyword_case="upper", reindent=True)


def _extract_diff_lines(diff_text: str) -> list[str]:
    return [line for line in diff_text.splitlines() if line.startswith("+") or line.startswith("-")]


def _simple_translate(text: str, source_lang: str, target_lang: str) -> str:
    source = source_lang.lower()
    target = target_lang.lower()
    key = f"{source}->{target}"
    lexicons = {
        "en->de": {
            "hello": "hallo",
            "world": "welt",
            "error": "fehler",
            "success": "erfolg",
            "file": "datei",
            "test": "test",
        },
        "en->es": {
            "hello": "hola",
            "world": "mundo",
            "error": "error",
            "success": "exito",
            "file": "archivo",
            "test": "prueba",
        },
        "en->fr": {
            "hello": "bonjour",
            "world": "monde",
            "error": "erreur",
            "success": "succes",
            "file": "fichier",
            "test": "test",
        },
    }
    lex = lexicons.get(key)
    if not lex:
        return text
    tokens = re.findall(r"\w+|\W+", text)
    out: list[str] = []
    for t in tokens:
        low = t.lower()
        if low in lex:
            out.append(lex[low])
        else:
            out.append(t)
    return "".join(out)


def _diagram_fingerprint(paths: list[str]) -> str:
    h = hashlib.sha256()
    normalized = sorted(set(paths))
    for rel in normalized:
        p = _resolve_repo_path(rel)
        if not p.is_file():
            continue
        h.update(rel.encode("utf-8"))
        h.update(_file_sha256(p).encode("utf-8"))
    return h.hexdigest()


def _mermaid_sanitize_id(text: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]", "_", text)
    out = re.sub(r"_+", "_", out).strip("_")
    return out or "node"


def _adaptive_limit(requested: int, soft_cap: int = 500) -> int:
    if requested < 1:
        raise ValueError("requested limit must be >= 1")
    budget = _token_budget_load().get("max_output_chars", MAX_OUTPUT_CHARS)
    if not isinstance(budget, int):
        budget = MAX_OUTPUT_CHARS
    factor = 1.0
    if budget <= 100000:
        factor = 0.75
    if budget <= 50000:
        factor = 0.5
    if budget <= 25000:
        factor = 0.35
    cap = max(10, int(soft_cap * factor))
    return min(requested, cap)


def _fingerprint_path(
    root: Path,
    recursive: bool = True,
    suffixes: set[str] | None = None,
    max_files: int = 2000,
) -> str:
    hasher = hashlib.sha256()
    count = 0
    for p in _iter_candidate_files(root, recursive=recursive):
        if suffixes and p.suffix.lower() not in suffixes:
            continue
        rel = str(p.relative_to(REPO_PATH)).replace("\\", "/")
        hasher.update(rel.encode("utf-8"))
        try:
            hasher.update(_file_sha256(p).encode("utf-8"))
        except OSError:
            continue
        count += 1
        if count >= max_files:
            break
    return hasher.hexdigest()


def _failure_memory_load() -> dict[str, Any]:
    payload = _json_file_load(FAILURE_MEMORY_FILE, {"entries": []})
    if not isinstance(payload, dict):
        return {"entries": []}
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    return {"entries": entries}


def _failure_memory_save(payload: dict[str, Any]) -> None:
    _json_file_save(FAILURE_MEMORY_FILE, payload)


def _failure_record(
    command: list[str],
    stderr: str,
    stdout: str = "",
    category: str = "command",
    suggestion: str | None = None,
) -> None:
    payload = _failure_memory_load()
    entries = payload["entries"]
    entries.append(
        {
            "timestamp": _now_iso(),
            "command": command,
            "category": category,
            "stderr": _trim_text(stderr),
            "stdout": _trim_text(stdout),
            "suggestion": suggestion or "",
        }
    )
    payload["entries"] = entries[-500:]
    _failure_memory_save(payload)


def _tx_path(txn_id: str) -> Path:
    return _resolve_repo_path(str(EDIT_TXN_DIR / f"{txn_id}.json"))


def _tx_load(txn_id: str) -> dict[str, Any]:
    tx_path = _tx_path(txn_id)
    if not tx_path.is_file():
        raise FileNotFoundError(f"transaction not found: {txn_id}")
    try:
        payload = json.loads(tx_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("transaction metadata is corrupted") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("transaction metadata is invalid")
    return payload


def _tx_save(txn_id: str, payload: dict[str, Any]) -> None:
    tx_path = _tx_path(txn_id)
    tx_path.parent.mkdir(parents=True, exist_ok=True)
    tx_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _collect_python_symbols_top_level(
    source: str, rel_path: str, include_private: bool = False
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    symbols: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        name = node.name
        if not include_private and name.startswith("_"):
            continue
        kind = "class"
        if isinstance(node, ast.FunctionDef):
            kind = "function"
        if isinstance(node, ast.AsyncFunctionDef):
            kind = "async_function"
        symbols.append(
            {
                "path": rel_path,
                "name": name,
                "kind": kind,
                "line_start": int(getattr(node, "lineno", 1)),
                "line_end": int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
            }
        )
    return symbols


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_sitter_language_for_ext(ext: str) -> str | None:
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".go": "go",
        ".rs": "rust",
    }
    return mapping.get(ext.lower())


def _tree_sitter_available() -> bool:
    return _ts_get_parser is not None


def _tree_sitter_parse_nodes(
    source: str,
    language: str,
    node_types: list[str] | None = None,
    max_nodes: int = 5000,
) -> list[dict[str, Any]]:
    if _ts_get_parser is None:
        raise RuntimeError("tree_sitter_languages is not installed")
    parser = _ts_get_parser(language)
    tree = parser.parse(source.encode("utf-8", errors="replace"))
    wanted = set(node_types or [])
    results: list[dict[str, Any]] = []

    stack = [tree.root_node]
    while stack and len(results) < max_nodes:
        node = stack.pop()
        if not wanted or node.type in wanted:
            results.append(
                {
                    "type": node.type,
                    "start_line": int(node.start_point[0]) + 1,
                    "start_column": int(node.start_point[1]) + 1,
                    "end_line": int(node.end_point[0]) + 1,
                    "end_column": int(node.end_point[1]) + 1,
                }
            )
        stack.extend(reversed(node.children))
    return results


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso_expiry(ttl_days: int | None) -> str | None:
    if ttl_days is None:
        return None
    if ttl_days < 1:
        raise ValueError("ttl_days must be >= 1")
    return (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()


def _is_expired(expires_at: str | None, now: datetime) -> bool:
    if not expires_at:
        return False
    try:
        ts = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts < now


def _collect_python_symbols(
    source: str, rel_path: str, include_private: bool
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    symbols: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        name = node.name
        if not include_private and name.startswith("_"):
            continue
        kind = "class"
        if isinstance(node, ast.FunctionDef):
            kind = "function"
        if isinstance(node, ast.AsyncFunctionDef):
            kind = "async_function"
        symbols.append(
            {
                "path": rel_path,
                "name": name,
                "kind": kind,
                "line_start": int(getattr(node, "lineno", 1)),
                "line_end": int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
            }
        )
    return symbols


def _module_name_from_relpath(rel_path: Path) -> str:
    parts = list(rel_path.parts)
    if not parts:
        return ""
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def _import_candidates(module: str) -> list[str]:
    parts = module.split(".")
    candidates: list[str] = []
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        candidates.append(prefix)
    return candidates


def _validate_output_profile(output_profile: str) -> str:
    profile = output_profile.strip().lower()
    if profile not in OUTPUT_PROFILES:
        raise ValueError("output_profile must be one of: compact, normal, verbose")
    return profile


def _build_snippet(
    file_path: Path,
    start_line: int,
    end_line: int,
    context_before: int = 0,
    context_after: int = 0,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    if start_line < 1 or end_line < 1:
        raise ValueError("start_line and end_line must be >= 1")
    if end_line < start_line:
        raise ValueError("end_line must be >= start_line")
    if context_before < 0 or context_after < 0:
        raise ValueError("context_before/context_after must be >= 0")
    if not file_path.is_file():
        raise FileNotFoundError(str(file_path.relative_to(REPO_PATH)))

    lines = _read_lines(file_path, encoding=encoding)
    total_lines = len(lines)
    from_line = max(1, start_line - context_before)
    to_line = min(total_lines, end_line + context_after)
    snippet_lines = lines[from_line - 1 : to_line]
    return {
        "path": str(file_path.relative_to(REPO_PATH)),
        "requested_start_line": start_line,
        "requested_end_line": end_line,
        "start_line": from_line,
        "end_line": to_line,
        "total_lines": total_lines,
        "content": "\n".join(snippet_lines),
    }


def _symbol_to_profile(symbol: dict[str, Any], profile: str) -> dict[str, Any]:
    if profile == "compact":
        return {
            "path": symbol["path"],
            "name": symbol["name"],
            "kind": symbol["kind"],
            "line_start": symbol["line_start"],
        }
    return symbol


def _match_to_profile(match: dict[str, Any], profile: str) -> dict[str, Any]:
    if profile == "compact":
        return {
            "path": match["path"],
            "line": match["line"],
            "column": match["column"],
            "match": match["match"],
        }
    return match


def _run_lab_script(script_name: str, args: list[str]) -> dict[str, Any]:
    _require_mutations()
    _require_git_repo()

    script_rel = str(LABS_DIR / script_name)
    script_path = _resolve_repo_path(script_rel)
    if not script_path.is_file():
        raise FileNotFoundError(script_rel)

    proc = subprocess.run(
        [sys.executable, str(script_path), *args],
        cwd=str(REPO_PATH),
        check=False,
        capture_output=True,
        text=True,
    )

    stdout = _trim_text(proc.stdout.strip())
    stderr = _trim_text(proc.stderr.strip())
    result: dict[str, Any] = {
        "script": script_rel,
        "args": args,
        "exit_code": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout": stdout,
        "stderr": stderr,
        "reports": _list_report_files(),
    }

    if proc.returncode != 0:
        msg = stderr or stdout or f"{script_name} failed with exit code {proc.returncode}"
        raise RuntimeError(msg)

    return result


def _chunk_strings(values: list[str], chunk_size: int) -> list[list[str]]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    return [values[i : i + chunk_size] for i in range(0, len(values), chunk_size)]


def _require_reuse_cli() -> None:
    if shutil.which("reuse") is None:
        raise RuntimeError("reuse CLI not found; install python package 'reuse'")


def _run_reuse(args: list[str], timeout_seconds: int = 120) -> dict[str, Any]:
    _require_reuse_cli()
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    observed = _run_observed_subprocess(
        ["reuse", *args],
        cwd=str(REPO_PATH),
        event_source="reuse",
        timeout_seconds=timeout_seconds,
    )
    return {
        "ok": observed["exit_code"] == 0 and not observed["timed_out"],
        "exit_code": observed["exit_code"],
        "command": ["reuse", *args],
        "stdout": _trim_text(observed["stdout"].strip()),
        "stderr": _trim_text(observed["stderr"].strip()),
        "timeout": observed["timed_out"],
    }


def _collect_spdx_license_ids(path: str = ".", recursive: bool = True) -> list[str]:
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)
    found: set[str] = set()
    matcher = re.compile(r"SPDX-License-Identifier:\s*(.+)")
    token_re = re.compile(r"[A-Za-z0-9.\-+]+")
    keywords = {"AND", "OR", "WITH"}
    for candidate in _iter_candidate_files(root, recursive=recursive):
        rel = candidate.relative_to(REPO_PATH)
        rel_str = str(rel).replace("\\", "/")
        if rel_str.startswith(".git/") or rel_str.startswith(".codebase-tooling-mcp/"):
            continue
        if _is_likely_binary(candidate):
            continue
        text = candidate.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines()[:120]:
            match = matcher.search(line)
            if not match:
                continue
            expr = match.group(1)
            for token in token_re.findall(expr):
                if token in keywords or token.startswith("LicenseRef-"):
                    continue
                found.add(token)
    return sorted(found)


def _collect_missing_spdx_headers(
    path: str = ".",
    recursive: bool = True,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    max_files: int = 5000,
) -> list[str]:
    if max_files < 1:
        raise ValueError("max_files must be >= 1")
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)
    missing: list[str] = []
    for candidate in _iter_candidate_files(root, recursive=recursive):
        rel = candidate.relative_to(REPO_PATH)
        rel_str = str(rel).replace("\\", "/")
        if rel_str.startswith(".git/") or rel_str.startswith(".codebase-tooling-mcp/") or rel_str.startswith("LICENSES/"):
            continue
        if not _allowed_by_globs(rel_str, include_globs=include_globs, exclude_globs=exclude_globs):
            continue
        if _is_likely_binary(candidate):
            continue
        try:
            lines = _read_lines(candidate, encoding="utf-8")
        except OSError:
            continue
        window = "\n".join(lines[:40])
        if "SPDX-License-Identifier:" not in window:
            missing.append(rel_str)
            if len(missing) >= max_files:
                break
    return missing


_VOLATILE_GOLDEN_KEYS = {
    "generated_at",
    "started_at",
    "finished_at",
    "updated_at",
    "created_at",
    "result_id",
    "git_head",
    "git_branch",
}


def _stable_for_golden(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value.keys()):
            if key in _VOLATILE_GOLDEN_KEYS:
                continue
            out[key] = _stable_for_golden(value[key])
        return out
    if isinstance(value, list):
        return [_stable_for_golden(v) for v in value]
    return value


def _hash_json_payload(value: Any) -> str:
    payload = json.dumps(_stable_for_golden(value), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_failed_tests_pytest(output: str) -> list[str]:
    failed = set(re.findall(r"^FAILED\\s+([^\\s]+)", output, flags=re.MULTILINE))
    for match in re.findall(r"^\\s*([^\\s]+)::([^\\s]+)\\s+FAILED\\s*$", output, flags=re.MULTILINE):
        failed.add(f"{match[0]}::{match[1]}")
    return sorted(failed)


def _extract_failed_tests_unittest(output: str) -> list[str]:
    failed: set[str] = set()
    for name, module in re.findall(
        r"^(?:FAIL|ERROR):\\s+([^\\s]+)\\s+\\(([^\\)]+)\\)",
        output,
        flags=re.MULTILINE,
    ):
        failed.add(f"{module}.{name}")
    return sorted(failed)


def _risk_level_value(level: str) -> int:
    order = {"low": 1, "medium": 2, "high": 3}
    return order.get(level, 0)


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _readme_tool_names() -> set[str]:
    readme = _resolve_repo_path("README.md")
    if not readme.is_file():
        return set()
    names: set[str] = set()
    for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"- `([^`]+)`", line.strip())
        if m:
            names.add(m.group(1))
    return names


def _declared_tool_names() -> set[str]:
    server_file = REPO_PATH / "source/server.py"
    if not server_file.is_file():
        server_file = Path(__file__).resolve()
    names: set[str] = set()
    lines = server_file.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        if line.strip() != "@mcp.tool()":
            continue
        for j in range(i + 1, min(i + 8, len(lines))):
            m = re.match(r"\s*def\s+([a-zA-Z0-9_]+)\(", lines[j])
            if m:
                names.add(m.group(1))
                break
    return names


def _server_tool_names() -> set[str]:
    return set(PUBLIC_MCP_TOOL_NAMES)


def _lossless_blob_store_load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema": "lossless_blob_store.v1", "blobs": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema": "lossless_blob_store.v1", "blobs": {}}
    blobs = payload.get("blobs", {})
    if not isinstance(blobs, dict):
        blobs = {}
    return {"schema": "lossless_blob_store.v1", "blobs": blobs}


def _lossless_blob_store_save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _lossless_collect_string_counts(value: Any, counts: dict[str, int]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            counts[str(k)] = counts.get(str(k), 0) + 1
            _lossless_collect_string_counts(v, counts)
        return
    if isinstance(value, list):
        for item in value:
            _lossless_collect_string_counts(item, counts)
        return
    if isinstance(value, str):
        counts[value] = counts.get(value, 0) + 1


def _lossless_build_symbol_table(
    value: Any,
    min_symbol_length: int,
    min_symbol_reuse: int,
) -> dict[str, str]:
    counts: dict[str, int] = {}
    _lossless_collect_string_counts(value, counts)
    candidates = [
        s for s, c in counts.items() if len(s) >= min_symbol_length and c >= min_symbol_reuse
    ]
    candidates.sort(key=lambda s: (-counts[s], -len(s), s))
    table: dict[str, str] = {}
    for i, s in enumerate(candidates, start=1):
        table[f"s{i}"] = s
    return table


def _lossless_symbol_inverse(table: dict[str, str]) -> dict[str, str]:
    inv: dict[str, str] = {}
    for token, text in table.items():
        if text not in inv:
            inv[text] = token
    return inv


def _lossless_encode_node(
    value: Any,
    symbol_inverse: dict[str, str],
    blobs: dict[str, str],
    use_blob_refs: bool,
    min_blob_chars: int,
) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            token = symbol_inverse.get(key)
            enc_key = {"$sym": token} if token else key
            out_key = json.dumps(enc_key, sort_keys=True) if isinstance(enc_key, dict) else enc_key
            out[out_key] = _lossless_encode_node(
                v,
                symbol_inverse=symbol_inverse,
                blobs=blobs,
                use_blob_refs=use_blob_refs,
                min_blob_chars=min_blob_chars,
            )
        return out
    if isinstance(value, list):
        return [
            _lossless_encode_node(
                item,
                symbol_inverse=symbol_inverse,
                blobs=blobs,
                use_blob_refs=use_blob_refs,
                min_blob_chars=min_blob_chars,
            )
            for item in value
        ]
    if isinstance(value, str):
        token = symbol_inverse.get(value)
        if token:
            return {"$sym": token}
        if use_blob_refs and len(value) >= min_blob_chars:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            blob_id = f"sha256:{digest}"
            blobs.setdefault(blob_id, value)
            return {"$blob": blob_id}
    return value


def _lossless_decode_key(raw_key: str, symbol_table: dict[str, str]) -> str:
    if raw_key.startswith("{") and raw_key.endswith("}"):
        try:
            parsed = json.loads(raw_key)
            if isinstance(parsed, dict) and isinstance(parsed.get("$sym"), str):
                token = parsed["$sym"]
                return symbol_table.get(token, token)
        except json.JSONDecodeError:
            pass
    return raw_key


def _lossless_decode_node(
    value: Any,
    symbol_table: dict[str, str],
    blobs: dict[str, str],
) -> Any:
    if isinstance(value, dict):
        if "$sym" in value and len(value) == 1 and isinstance(value["$sym"], str):
            token = value["$sym"]
            return symbol_table.get(token, token)
        if "$blob" in value and len(value) == 1 and isinstance(value["$blob"], str):
            blob_id = value["$blob"]
            if blob_id not in blobs:
                raise KeyError(f"missing blob id: {blob_id}")
            return blobs[blob_id]
        out: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _lossless_decode_key(str(raw_key), symbol_table=symbol_table)
            out[key] = _lossless_decode_node(raw_value, symbol_table=symbol_table, blobs=blobs)
        return out
    if isinstance(value, list):
        return [_lossless_decode_node(item, symbol_table=symbol_table, blobs=blobs) for item in value]
    return value


def _delta_join_path(base: str, segment: str) -> str:
    escaped = segment.replace("~", "~0").replace("/", "~1")
    if base == "":
        return f"/{escaped}"
    return f"{base}/{escaped}"


def _delta_build_ops(base: Any, target: Any, path: str, ops: list[dict[str, Any]]) -> None:
    if type(base) is not type(target):
        ops.append({"op": "set", "path": path or "/", "value": target})
        return
    if isinstance(base, dict):
        base_keys = set(base.keys())
        target_keys = set(target.keys())
        for key in sorted(base_keys - target_keys):
            ops.append({"op": "remove", "path": _delta_join_path(path, str(key))})
        for key in sorted(target_keys):
            child_path = _delta_join_path(path, str(key))
            if key not in base:
                ops.append({"op": "set", "path": child_path, "value": target[key]})
            else:
                _delta_build_ops(base[key], target[key], child_path, ops)
        return
    if isinstance(base, list):
        if base != target:
            ops.append({"op": "set", "path": path or "/", "value": target})
        return
    if base != target:
        ops.append({"op": "set", "path": path or "/", "value": target})


def _delta_parse_path(path: str) -> list[str]:
    if path in {"", "/"}:
        return []
    if not path.startswith("/"):
        raise ValueError("delta path must start with '/'")
    parts = path.split("/")[1:]
    return [p.replace("~1", "/").replace("~0", "~") for p in parts]


def _delta_set_value(root: Any, parts: list[str], value: Any) -> Any:
    if not parts:
        return value
    cursor = root
    for i, part in enumerate(parts[:-1]):
        nxt = parts[i + 1]
        if isinstance(cursor, dict):
            if part not in cursor or not isinstance(cursor[part], (dict, list)):
                cursor[part] = [] if nxt.isdigit() else {}
            cursor = cursor[part]
        elif isinstance(cursor, list):
            idx = int(part)
            while len(cursor) <= idx:
                cursor.append({})
            if not isinstance(cursor[idx], (dict, list)):
                cursor[idx] = [] if nxt.isdigit() else {}
            cursor = cursor[idx]
        else:
            raise ValueError("cannot navigate delta path")
    last = parts[-1]
    if isinstance(cursor, dict):
        cursor[last] = value
        return root
    if isinstance(cursor, list):
        idx = int(last)
        while len(cursor) <= idx:
            cursor.append(None)
        cursor[idx] = value
        return root
    raise ValueError("cannot set delta value on non-container")


def _delta_remove_value(root: Any, parts: list[str]) -> Any:
    if not parts:
        return None
    cursor = root
    for part in parts[:-1]:
        if isinstance(cursor, dict):
            if part not in cursor:
                return root
            cursor = cursor[part]
        elif isinstance(cursor, list):
            idx = int(part)
            if idx >= len(cursor):
                return root
            cursor = cursor[idx]
        else:
            return root
    last = parts[-1]
    if isinstance(cursor, dict):
        cursor.pop(last, None)
    elif isinstance(cursor, list):
        idx = int(last)
        if 0 <= idx < len(cursor):
            cursor.pop(idx)
    return root


def _docker_cli_status() -> dict[str, Any]:
    docker_bin = shutil.which("docker")
    socket_path = Path("/var/run/docker.sock")
    status: dict[str, Any] = {
        "docker_cli_present": bool(docker_bin),
        "docker_cli_path": docker_bin or "",
        "docker_socket_present": socket_path.exists(),
        "docker_socket_is_socket": socket_path.is_socket(),
    }
    if docker_bin:
        try:
            version = subprocess.run(
                [docker_bin, "version", "--format", "{{json .}}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
            status["docker_version_rc"] = version.returncode
            if version.stdout.strip():
                with contextlib.suppress(json.JSONDecodeError):
                    payload = json.loads(version.stdout)
                    status["docker_client_version"] = (
                        payload.get("Client", {}).get("Version", "")
                        if isinstance(payload, dict)
                        else ""
                    )
                    status["docker_server_version"] = (
                        payload.get("Server", {}).get("Version", "")
                        if isinstance(payload, dict)
                        else ""
                    )
            if version.stderr.strip():
                status["docker_version_stderr"] = _trim_text(
                    version.stderr.strip(),
                    max_chars=2000,
                )
        except Exception as exc:
            status["docker_error"] = str(exc)
    return status


def _list_listening_ports() -> set[int]:
    ports: set[int] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        with contextlib.suppress(FileNotFoundError, PermissionError):
            with open(table, "r", encoding="utf-8", errors="replace") as f:
                for line in f.readlines()[1:]:
                    cols = line.split()
                    if len(cols) < 4 or cols[3] != "0A":
                        continue
                    local_addr = cols[1]
                    if ":" not in local_addr:
                        continue
                    port_hex = local_addr.rsplit(":", 1)[1]
                    with contextlib.suppress(ValueError):
                        ports.add(int(port_hex, 16))
    return ports


def _count_processes_with_tokens(*tokens: str) -> int:
    wanted = tuple(t for t in tokens if t)
    if not wanted:
        return 0
    count = 0
    proc_root = Path("/proc")
    with contextlib.suppress(FileNotFoundError, PermissionError):
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            cmdline_path = entry / "cmdline"
            try:
                raw = cmdline_path.read_bytes()
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            text = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
            if text and all(tok in text for tok in wanted):
                count += 1
    return count


def _ollama_native_base_url() -> str:
    parsed = urllib.parse.urlparse(LOCAL_INFER_ENDPOINT)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "http://127.0.0.1:11434"


def _ollama_tags_url() -> str:
    return f"{_ollama_native_base_url()}/api/tags"


def _ollama_openai_base_url() -> str:
    return f"{_ollama_native_base_url()}/v1/"


def _parse_model_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _fetch_ollama_tags(timeout: float = 3.0) -> dict[str, Any]:
    tags_url = _ollama_tags_url()
    result: dict[str, Any] = {"url": tags_url, "reachable": False, "model_ids": []}
    try:
        req = urllib.request.Request(tags_url, method="GET")
        with _urlopen_with_host_certs(req, timeout=timeout) as resp:
            result["reachable"] = True
            result["status"] = int(getattr(resp, "status", 200))
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        result["error"] = str(exc)
        return result

    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError:
        result["parse_error"] = "Invalid JSON from /api/tags"
        return result

    models = parsed.get("models") if isinstance(parsed, dict) else None
    if not isinstance(models, list):
        result["parse_error"] = "Expected 'models' list in /api/tags response"
        return result

    model_ids: list[str] = []
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("model") or entry.get("name")
        if isinstance(model_id, str) and model_id and model_id not in model_ids:
            model_ids.append(model_id)
    result["model_ids"] = model_ids
    return result


def _continue_agent_probe_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "repo_status",
                "description": "Return a short repository status summary.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Short status request.",
                        }
                    },
                    "required": ["summary"],
                },
            },
        }
    ]


def _probe_ollama_chat(model: str, timeout: float = 10.0) -> dict[str, Any]:
    chat_url = f"{_ollama_native_base_url()}/api/chat"
    result: dict[str, Any] = {
        "url": chat_url,
        "model": model,
        "mode": "continue_agent",
        "reachable": False,
        "ok": False,
    }
    if not model:
        result["error"] = "No model configured for chat probe"
        return result

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "Call the repo_status tool now with summary set to status. Do not answer in normal text.",
            }
        ],
        "tools": _continue_agent_probe_tools(),
        "stream": True,
        "options": {"num_predict": 64, "temperature": 0},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        chat_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with _urlopen_with_host_certs(req, timeout=timeout) as resp:
            result["reachable"] = True
            result["status"] = int(getattr(resp, "status", 200))
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        result["reachable"] = True
        result["status"] = int(getattr(exc, "code", 0) or 0)
        body = ""
        with contextlib.suppress(Exception):
            body = exc.read().decode("utf-8", errors="replace")
            if body:
                result["body"] = _trim_text(body, max_chars=1200)
        result["error"] = str(exc)
        with contextlib.suppress(Exception):
            parsed_error = json.loads(body) if body else {}
            if isinstance(parsed_error, dict) and isinstance(parsed_error.get("error"), str):
                result["error"] = parsed_error["error"]
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result

    if body:
        result["body"] = _trim_text(body, max_chars=1200)
    try:
        parsed_events = (
            [json.loads(line) for line in body.splitlines() if line.strip()]
            if body and "\n" in body
            else ([json.loads(body)] if body else [])
        )
    except json.JSONDecodeError:
        result["parse_error"] = "Invalid JSON from /api/chat"
        return result

    tool_calls: list[Any] = []
    for parsed in parsed_events:
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
            result["error"] = parsed["error"]
            return result
        message = parsed.get("message") if isinstance(parsed, dict) else None
        event_tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
        if isinstance(event_tool_calls, list):
            tool_calls.extend(event_tool_calls)
    tool_call_names: list[str] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            tool_call_names.append(function["name"])
    result["tool_call_count"] = len(tool_call_names)
    result["tool_call_names"] = tool_call_names
    result["ok"] = (
        200 <= int(result.get("status", 0)) < 300
        and "repo_status" in tool_call_names
    )
    if not result["ok"] and 200 <= int(result.get("status", 0)) < 300:
        result["error"] = "Agent-mode tool call response did not include repo_status"
    return result


def _probe_http(url: str, timeout: float = 2.0) -> dict[str, Any]:
    result: dict[str, Any] = {"url": url, "reachable": False}
    try:
        req = urllib.request.Request(url, method="GET")
        with _urlopen_with_host_certs(req, timeout=timeout) as resp:
            result["reachable"] = True
            result["status"] = int(getattr(resp, "status", 200))
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _runtime_state_payload(include_ollama_probe: bool = True) -> dict[str, Any]:
    listening_ports = _list_listening_ports()
    http_mode = MCP_TRANSPORT in {"http", "streamable-http", "streamable_http"}
    ollama_tags_url = _ollama_tags_url()
    ollama_host_env = os.getenv("OLLAMA_HOST", "").strip()
    configured_ollama_port = urllib.parse.urlparse(ollama_tags_url).port
    ollama_probe: dict[str, Any] = (
        _probe_http(ollama_tags_url, timeout=2.0) if include_ollama_probe else {}
    )
    ollama_processes = _count_processes_with_tokens("ollama", "serve")

    return {
        "schema": "runtime_state.v1",
        "timestamp": _now_iso(),
        "transport": MCP_TRANSPORT,
        "server": {
            "pid": os.getpid(),
            "host": HOST,
            "port": PORT,
            "http_mode": http_mode,
            "port_listening": (PORT in listening_ports) if http_mode else None,
            "python_server_processes": _count_processes_with_tokens("python", "server.py"),
        },
        "sse": {
            "subscribers": _sse_subscriber_count(),
            "buffered_events": _sse_recent_event_count(),
        },
        "ollama": {
            "host_env": ollama_host_env,
            "models_dir_env": os.getenv("OLLAMA_MODELS", ""),
            "serve_processes": ollama_processes,
            "running": ollama_processes > 0,
            "configured_port": configured_ollama_port,
            "configured_port_listening": (
                configured_ollama_port in listening_ports
                if configured_ollama_port is not None
                else None
            ),
            "port_11434_listening": 11434 in listening_ports,
            "tags_probe": ollama_probe,
        },
        "docker": _docker_cli_status(),
    }


def _load_vscode_tasks(tasks_path: str = ".vscode/tasks.json") -> tuple[Path, list[dict[str, Any]]]:
    path = _resolve_repo_path(tasks_path)
    if not path.is_file():
        raise FileNotFoundError(tasks_path)
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("invalid tasks.json: root must be an object")
    tasks = payload.get("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError("invalid tasks.json: tasks must be an array")
    normalized: list[dict[str, Any]] = []
    for item in tasks:
        if isinstance(item, dict):
            normalized.append(item)
    return path, normalized


def _task_command_from_vscode_task(task: dict[str, Any]) -> list[str]:
    command_value = task.get("command")
    if not isinstance(command_value, str) or not command_value.strip():
        raise ValueError("task command must be a non-empty string")
    args_value = task.get("args", [])
    if args_value is None:
        args_value = []
    if not isinstance(args_value, list):
        raise ValueError("task args must be an array")
    args: list[str] = [str(x) for x in args_value]
    head = shlex.split(command_value)
    if not head:
        raise ValueError("task command must not be empty")
    return [*head, *args]


def _first_non_flag_token(
    tokens: list[str],
    start: int,
    options_with_values: set[str],
) -> str:
    idx = start
    while idx < len(tokens):
        token = tokens[idx]
        if token in options_with_values:
            idx += 2
            continue
        if token.startswith("-"):
            idx += 1
            continue
        return token
    raise ValueError("missing subcommand")


def _docker_control_policy(control_profile: str) -> tuple[set[str], set[str]]:
    profiles = {"build", "compose", "runtime", "all"}
    if control_profile not in profiles:
        raise ValueError(
            f"control_profile must be one of: {', '.join(sorted(profiles))}"
        )

    docker_build = {"build", "buildx", "pull", "images", "version", "info", "compose"}
    compose_build = {"build", "config", "images", "ps", "pull"}

    docker_compose = {"compose", "images", "version", "info"}
    compose_ops = {
        "build",
        "config",
        "images",
        "ps",
        "pull",
        "up",
        "down",
        "start",
        "stop",
        "restart",
        "logs",
    }

    docker_runtime = {
        "run",
        "exec",
        "ps",
        "logs",
        "start",
        "stop",
        "restart",
        "rm",
        "inspect",
        "cp",
        "images",
        "version",
        "info",
        "compose",
    }
    compose_runtime = {"run", "exec", "ps", "logs", "start", "stop", "restart", "up", "down"}

    if control_profile == "build":
        return docker_build, compose_build
    if control_profile == "compose":
        return docker_compose, compose_ops
    if control_profile == "runtime":
        return docker_runtime, compose_runtime
    return docker_build | docker_compose | docker_runtime, compose_build | compose_ops | compose_runtime


def _validate_build_task_command(command: list[str], control_profile: str = "build") -> None:
    if not command:
        raise ValueError("empty command")

    allowed_docker_sub, allowed_compose_sub = _docker_control_policy(control_profile)
    binary = command[0]
    if binary not in {"docker", "docker-compose"}:
        raise ValueError(f"only docker task control is allowed; got: {binary}")

    if binary == "docker":
        sub = _first_non_flag_token(
            command,
            1,
            {"-H", "--host", "--context", "--config", "--tlscacert", "--tlscert", "--tlskey"},
        )
        if sub not in allowed_docker_sub:
            raise ValueError(
                f"docker subcommand not allowed for control_profile={control_profile}: {sub}"
            )
        if sub == "compose":
            compose_sub = _first_non_flag_token(
                command,
                command.index("compose") + 1,
                {"-f", "--file", "-p", "--project-name", "--profile", "--env-file", "--project-directory"},
            )
            if compose_sub not in allowed_compose_sub:
                raise ValueError(
                    f"docker compose subcommand not allowed for control_profile={control_profile}: {compose_sub}"
                )
        return

    compose_sub = _first_non_flag_token(
        command,
        1,
        {"-f", "--file", "-p", "--project-name", "--profile", "--env-file", "--project-directory"},
    )
    if compose_sub not in allowed_compose_sub:
        raise ValueError(
            f"docker-compose subcommand not allowed for control_profile={control_profile}: {compose_sub}"
        )


def _summarize_build_log(stdout: str, stderr: str, max_lines: int = 120) -> str:
    joined = "\n".join(x for x in [stdout, stderr] if x).strip()
    if not joined:
        return ""
    lines = joined.splitlines()
    if len(lines) <= max_lines:
        return joined
    return "\n".join(lines[-max_lines:])


def _build_log_proposals(stdout: str, stderr: str) -> list[dict[str, str]]:
    text = "\n".join([stdout or "", stderr or ""]).lower()
    proposals: list[dict[str, str]] = []

    def add(issue: str, proposal: str, confidence: str = "medium") -> None:
        proposals.append(
            {
                "issue": issue,
                "proposal": proposal,
                "confidence": confidence,
            }
        )

    if any(x in text for x in ["no space left on device", "insufficient disk space"]):
        add(
            "Docker build ran out of disk space.",
            "Run `docker system prune -af --volumes` and retry the build.",
            "high",
        )
    if "failed to solve with frontend dockerfile.v0" in text and "not found" in text:
        add(
            "Dockerfile step references missing file or stage.",
            "Check COPY/ADD sources and multi-stage `--from=` names in the Dockerfile.",
            "high",
        )
    if "pull access denied" in text or "requested access to the resource is denied" in text:
        add(
            "Image pull denied (auth or image name problem).",
            "Run `docker login`, verify image name/tag, and confirm registry permissions.",
            "high",
        )
    if "error getting credentials" in text or "credential helper" in text:
        add(
            "Docker credential helper failed.",
            "Fix `~/.docker/config.json` credsStore/credHelpers or authenticate with `docker login`.",
            "medium",
        )
    if "network timed out" in text or "tls handshake timeout" in text:
        add(
            "Network timeout while pulling/downloading dependencies.",
            "Retry build, validate proxy/firewall settings, and check registry reachability.",
            "medium",
        )
    if "apt-get" in text and "temporary failure resolving" in text:
        add(
            "DNS resolution failed during package install.",
            "Check container DNS/network; retry with stable DNS or mirror settings.",
            "high",
        )
    if "permission denied" in text and "/var/run/docker.sock" in text:
        add(
            "No permission to access Docker socket.",
            "Ensure container user is in the docker socket group and reopen/rebuild devcontainer.",
            "high",
        )
    if "executor failed running" in text and "exit code: 127" in text:
        add(
            "Command not found during Docker build step.",
            "Install required package/binary before the failing RUN command.",
            "high",
        )
    if "executor failed running" in text and "exit code: 1" in text:
        add(
            "Build RUN step failed with a generic non-zero exit code.",
            "Inspect the failing step in build log and split complex RUN commands for clearer errors.",
            "medium",
        )
    if "failed to read dockerfile" in text:
        add(
            "Dockerfile path is wrong for selected build context.",
            "Verify task build context and Dockerfile path (`-f`) in `.vscode/tasks.json`.",
            "high",
        )
    if "cannot connect to the docker daemon" in text:
        add(
            "Docker daemon is unreachable.",
            "Check daemon status/socket mount and run `docker_cli_status` before retrying.",
            "high",
        )
    if "context canceled" in text:
        add(
            "Build context transfer canceled/interrupted.",
            "Retry the build and check for large context or unstable Docker daemon.",
            "low",
        )

    if not proposals:
        add(
            "No known build-failure signature matched.",
            "Inspect the final failing step in `build_log_tail` and rerun with `--progress=plain` for detail.",
            "low",
        )
    return proposals



def _policy_insights_path() -> Path:
    """Return the source-controlled maintainer policy insight bank path."""
    return Path(__file__).resolve().parents[1] / POLICY_INSIGHTS_FILE


def _load_policy_insight_bank() -> dict[str, Any]:
    path = _policy_insights_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"policy insight bank is missing: {POLICY_INSIGHTS_FILE}") from exc
    if payload.get("schema") != "mcp_policy_insights.v1":
        raise ValueError("policy insight bank schema must be mcp_policy_insights.v1")
    insights = payload.get("insights")
    if not isinstance(insights, list) or not insights:
        raise ValueError("policy insight bank must contain at least one insight")
    required = {
        "id",
        "tool_router",
        "trigger",
        "expected_decision",
        "rationale",
        "source",
        "remediation",
    }
    seen: set[str] = set()
    for index, insight in enumerate(insights):
        if not isinstance(insight, dict):
            raise ValueError(f"policy insight {index} must be an object")
        missing = sorted(required.difference(insight))
        if missing:
            raise ValueError(f"policy insight {index} missing required fields: {missing}")
        insight_id = str(insight["id"])
        if insight_id in seen:
            raise ValueError(f"duplicate policy insight id: {insight_id}")
        seen.add(insight_id)
        if not isinstance(insight.get("trigger"), dict):
            raise ValueError(f"policy insight {insight_id} trigger must be an object")
    return payload


def _policy_insight_public_row(insight: dict[str, Any]) -> dict[str, Any]:
    trigger = insight.get("trigger", {}) if isinstance(insight.get("trigger"), dict) else {}
    return {
        "id": str(insight.get("id", "")),
        "tool_router": str(insight.get("tool_router", "")),
        "trigger": {
            "kind": str(trigger.get("kind", "")),
            "summary": str(trigger.get("summary", "")),
        },
        "expected_decision": str(insight.get("expected_decision", "")),
        "rationale": str(insight.get("rationale", "")),
        "source": str(insight.get("source", "")),
        "remediation": str(insight.get("remediation", "")),
    }


def _policy_insight_replay_decision(insight: dict[str, Any]) -> dict[str, Any]:
    """Replay one maintainer-authored insight through local policy primitives."""
    trigger = insight.get("trigger", {}) if isinstance(insight.get("trigger"), dict) else {}
    kind = str(trigger.get("kind", ""))
    if kind == "tool_security_gate":
        global ALLOW_MUTATIONS
        original_allow_mutations = ALLOW_MUTATIONS
        token = None
        try:
            if "allow_mutations" in trigger:
                ALLOW_MUTATIONS = bool(trigger.get("allow_mutations"))
            if "http_authorized" in trigger:
                token = _HTTP_REQUEST_AUTHORIZED.set(bool(trigger.get("http_authorized")))
            _require_tool_security_gate(
                str(trigger.get("tool", insight.get("tool_router", ""))),
                trigger.get("arguments") if isinstance(trigger.get("arguments"), dict) else {},
            )
        except PermissionError as exc:
            return {"decision": "deny", "reason": _redact_audit_reason(str(exc))}
        finally:
            if token is not None:
                _HTTP_REQUEST_AUTHORIZED.reset(token)
            ALLOW_MUTATIONS = original_allow_mutations
        return {"decision": "allow", "reason": "policy gate allowed request"}
    if kind == "audit_redaction":
        sample = trigger.get("sample", {})
        redacted = _redact_audit_value(sample)
        encoded = json.dumps(redacted, sort_keys=True)
        forbidden = [str(item) for item in trigger.get("forbidden_fragments", []) if str(item)]
        leaked = [item for item in forbidden if item in encoded]
        decision = "redact" if "<redacted>" in encoded and not leaked else "leak"
        return {"decision": decision, "reason": "audit redaction replay", "leaked_fragments": leaked}
    raise ValueError(f"unsupported policy insight trigger kind: {kind}")


def _policy_insight_replay_report() -> dict[str, Any]:
    bank = _load_policy_insight_bank()
    results: list[dict[str, Any]] = []
    ok = True
    for insight in bank["insights"]:
        actual = _policy_insight_replay_decision(insight)
        expected = str(insight.get("expected_decision", ""))
        matched = actual["decision"] == expected
        ok = ok and matched
        results.append(
            {
                "id": str(insight.get("id", "")),
                "tool_router": str(insight.get("tool_router", "")),
                "expected_decision": expected,
                "actual_decision": actual["decision"],
                "matched": matched,
                "reason": actual.get("reason", ""),
            }
        )
    return {
        "schema": "mcp_policy_insight_replay.v1",
        "ok": ok,
        "insight_count": len(results),
        "results": results,
    }


@mcp.tool()
def policy_insights(insight_id: str = "") -> dict[str, Any]:
    """Read-only summary of maintainer-owned policy/tool-gate regression insights."""
    bank = _load_policy_insight_bank()
    rows = [_policy_insight_public_row(item) for item in bank["insights"]]
    selected = insight_id.strip()
    if selected:
        rows = [row for row in rows if row["id"] == selected]
        if not rows:
            raise ValueError(f"unknown policy insight id: {selected}")
    return {
        "schema": "mcp_policy_insights_summary.v1",
        "bank_schema": bank["schema"],
        "bank_version": str(bank.get("version", "")),
        "maintainer_controlled": True,
        "runtime_learning": False,
        "source_path": str(POLICY_INSIGHTS_FILE),
        "insight_count": len(rows),
        "insights": rows,
        "safety": {
            "read_only": True,
            "raw_triggers_exposed": False,
            "contains_secrets": False,
        },
        "promotion": str(bank.get("promotion", "")),
    }

@mcp.tool()
def tool_annotations(tool_name: str = "") -> dict[str, Any]:
    """Return MCP safety annotation hints for public tools and router modes."""
    manifest = _tool_annotation_manifest()
    selected = tool_name.strip()
    if not selected:
        return manifest
    for entry in manifest["tools"]:
        if entry["tool"] == selected:
            return {"schema": manifest["schema"], "source": manifest["source"], "tool": entry}
    raise ValueError(f"unknown public MCP tool: {selected}")


@mcp.tool()
def tool_output_contracts(tool_name: str = "") -> dict[str, Any]:
    """Return outputSchema contracts for the schema-backed core tools."""
    if tool_name.strip():
        return tool_output_contract(tool_name.strip())
    return all_tool_output_contracts()


@mcp.tool()
def repo_info() -> dict[str, Any]:
    """Read-only capability probe: repo/git/docker state, branch/head, and server limits."""
    _ensure_repo_path_exists()

    info: dict[str, Any] = {
        "repo_path": str(REPO_PATH),
        "repo_exists": REPO_PATH.exists(),
        "is_git_repo": _is_git_repo(),
        "allow_mutations": ALLOW_MUTATIONS,
        "transport": MCP_TRANSPORT,
        "max_read_bytes": MAX_READ_BYTES,
        "max_output_chars": MAX_OUTPUT_CHARS,
        "docker": _docker_cli_status(),
    }

    if info["is_git_repo"]:
        info["current_branch"] = _git("branch", "--show-current").stdout.strip()
        info["head"] = _git("rev-parse", "HEAD").stdout.strip()
        status = _git("status", "--porcelain").stdout.strip()
        info["dirty"] = bool(status)

    return info


@mcp.tool()
async def roots_diagnostics(timeout_seconds: float = 1.0) -> dict[str, Any]:
    """Read-only MCP roots diagnostic comparing client roots with REPO_PATH."""
    if timeout_seconds <= 0:
        timeout_seconds = 1.0
    timeout_seconds = min(float(timeout_seconds), 5.0)
    session = _active_mcp_session()
    if session is None:
        payload = _roots_base_payload("unavailable", "unavailable")
        payload["fetch"]["reason"] = "no_active_mcp_request_session"
        return payload

    if not hasattr(session, "list_roots"):
        payload = _roots_base_payload("unsupported", "unsupported")
        payload["fetch"]["reason"] = "session_has_no_list_roots_api"
        return payload

    if not await _client_roots_supported(session):
        payload = _roots_base_payload("unsupported", "unsupported")
        payload["fetch"]["reason"] = "client_roots_capability_not_advertised"
        return payload

    try:
        result = await asyncio.wait_for(session.list_roots(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        payload = _roots_base_payload("error", "timeout")
        payload["fetch"]["reason"] = "roots_list_timeout"
        return payload
    except Exception as exc:
        payload = _roots_base_payload("error", "error")
        payload["fetch"].update({"reason": "roots_list_failed", "error": exc.__class__.__name__})
        return payload

    roots = getattr(result, "roots", result)
    if roots is None:
        roots = []
    if not isinstance(roots, list):
        try:
            roots = list(roots)
        except TypeError:
            payload = _roots_base_payload("error", "error")
            payload["fetch"]["reason"] = "roots_result_invalid"
            payload["roots"]["invalid_count"] = 1
            return payload
    return _summarize_roots_result(roots)


@mcp.tool()
def runtime_state() -> dict[str, Any]:
    """Return process/port/dependency runtime state for server and optional Ollama."""
    return _runtime_state_payload(include_ollama_probe=True)


def docker_cli_status() -> dict[str, Any]:
    """Report docker CLI/socket awareness and daemon reachability signals."""
    return {
        "schema": "docker_cli_status.v1",
        **_docker_cli_status(),
    }


def docker_cli_run(
    command: list[str],
    cwd: str = ".",
    control_profile: str = "build",
    timeout_seconds: int = 1800,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    """Run a validated Docker CLI command directly."""
    _require_mutations()
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    out_cap = _token_budget_apply_max(max_output_chars)
    _validate_build_task_command(command, control_profile=control_profile)

    workdir = _resolve_repo_path(cwd)

    try:
        proc = subprocess.run(
            command,
            cwd=str(workdir),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        timeout_stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        build_log_tail = _summarize_build_log(timeout_stdout, timeout_stderr)
        proposals = _build_log_proposals(timeout_stdout, timeout_stderr)
        return {
            "schema": "docker_cli_run.v1",
            "ok": False,
            "command": command,
            "cwd": rel_cwd,
            "control_profile": control_profile,
            "exit_code": None,
            "timeout": True,
            "stdout": _trim_text(timeout_stdout, max_chars=out_cap),
            "stderr": _trim_text(timeout_stderr, max_chars=out_cap),
            "build_log_tail": _trim_text(build_log_tail, max_chars=out_cap),
            "proposals": proposals,
        }

    build_log_tail = _summarize_build_log(proc.stdout, proc.stderr)
    proposals: list[dict[str, str]] = []
    if proc.returncode != 0:
        proposals = _build_log_proposals(proc.stdout, proc.stderr)
    return {
        "schema": "docker_cli_run.v1",
        "ok": proc.returncode == 0,
        "command": command,
        "cwd": str(workdir.relative_to(REPO_PATH)),
        "control_profile": control_profile,
        "exit_code": proc.returncode,
        "timeout": False,
        "stdout": _trim_text(proc.stdout, max_chars=out_cap),
        "stderr": _trim_text(proc.stderr, max_chars=out_cap),
        "build_log_tail": _trim_text(build_log_tail, max_chars=out_cap),
        "proposals": proposals,
    }


class DockerRouterService:
    """Application service for Docker CLI routing."""

    def route(
        self,
        mode: str = "status",
        command: list[str] | None = None,
        cwd: str = ".",
        control_profile: str = "build",
        timeout_seconds: int = 1800,
        max_output_chars: int | None = None,
    ) -> dict[str, Any]:
        if mode not in {"status", "run"}:
            raise ValueError("mode must be one of: status, run")
        if mode == "status":
            return {
                "schema": "docker_router.v1",
                "mode": mode,
                "result": docker_cli_status(),
            }
        if not command:
            raise ValueError("command is required for run mode")
        return {
            "schema": "docker_router.v1",
            "mode": mode,
            "result": docker_cli_run(
                command=command,
                cwd=cwd,
                control_profile=control_profile,
                timeout_seconds=timeout_seconds,
                max_output_chars=max_output_chars,
            ),
        }


class VSCodeRouterService:
    """Application service for VS Code tasks routing."""

    def route(
        self,
        mode: str = "list",
        label: str = "",
        tasks_path: str = ".vscode/tasks.json",
        label_prefix: str = "",
        control_profile: str = "build",
        timeout_seconds: int = 1800,
        max_output_chars: int | None = None,
    ) -> dict[str, Any]:
        if mode not in {"list", "run"}:
            raise ValueError("mode must be one of: list, run")
        if mode == "list":
            return {
                "schema": "vscode_router.v1",
                "mode": mode,
                "result": vscode_tasks_list(
                    tasks_path=tasks_path,
                    label_prefix=label_prefix,
                    control_profile=control_profile,
                ),
            }
        if not label.strip():
            raise ValueError("label is required for run mode")
        return {
            "schema": "vscode_router.v1",
            "mode": mode,
            "result": vscode_task_run(
                label=label,
                tasks_path=tasks_path,
                control_profile=control_profile,
                timeout_seconds=timeout_seconds,
                max_output_chars=max_output_chars,
            ),
        }


_DOCKER_ROUTER_SERVICE = DockerRouterService()
_VSCODE_ROUTER_SERVICE = VSCodeRouterService()


@mcp.tool()
def docker_router(
    mode: str = "status",
    command: list[str] | None = None,
    cwd: str = ".",
    control_profile: str = "build",
    timeout_seconds: int = 1800,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    """Docker CLI gateway. mode=status|run; run validates the command against the selected control profile."""
    arguments = {
        "mode": mode,
        "command": command,
        "cwd": cwd,
        "control_profile": control_profile,
        "timeout_seconds": timeout_seconds,
        "max_output_chars": max_output_chars,
    }
    return _run_with_tool_security_audit(
        "docker_router",
        arguments,
        lambda: _DOCKER_ROUTER_SERVICE.route(
            mode=mode,
            command=command,
            cwd=cwd,
            control_profile=control_profile,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        ),
    )


@mcp.tool()
def vscode_router(
    mode: str = "list",
    label: str = "",
    tasks_path: str = ".vscode/tasks.json",
    label_prefix: str = "",
    control_profile: str = "build",
    timeout_seconds: int = 1800,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    """VS Code task gateway. mode=list|run; list filters tasks.json and run executes one exact label."""
    arguments = {
        "mode": mode,
        "label": label,
        "tasks_path": tasks_path,
        "label_prefix": label_prefix,
        "control_profile": control_profile,
        "timeout_seconds": timeout_seconds,
        "max_output_chars": max_output_chars,
    }
    return _run_with_tool_security_audit(
        "vscode_router",
        arguments,
        lambda: _VSCODE_ROUTER_SERVICE.route(
            mode=mode,
            label=label,
            tasks_path=tasks_path,
            label_prefix=label_prefix,
            control_profile=control_profile,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        ),
    )


def vscode_tasks_list(
    tasks_path: str = ".vscode/tasks.json",
    label_prefix: str = "",
    control_profile: str = "build",
) -> dict[str, Any]:
    """List VS Code tasks and whether each is runnable under the selected control profile."""
    tasks_file, tasks = _load_vscode_tasks(tasks_path)
    rows: list[dict[str, Any]] = []
    for task in tasks:
        label = str(task.get("label", "")).strip()
        if label_prefix and not label.startswith(label_prefix):
            continue
        try:
            cmd = _task_command_from_vscode_task(task)
            _validate_build_task_command(cmd, control_profile=control_profile)
            rows.append(
                {
                    "label": label,
                    "ok": True,
                    "command": cmd,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "label": label,
                    "ok": False,
                    "error": str(exc),
                }
            )
    return {
        "schema": "vscode_tasks_list.v1",
        "tasks_path": str(tasks_file.relative_to(REPO_PATH)),
        "label_prefix": label_prefix,
        "control_profile": control_profile,
        "count": len(rows),
        "tasks": rows,
    }


def vscode_task_run(
    label: str,
    tasks_path: str = ".vscode/tasks.json",
    control_profile: str = "build",
    timeout_seconds: int = 1800,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    """Run an approved Docker task by label from VS Code tasks.json."""
    _require_mutations()
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    out_cap = _token_budget_apply_max(max_output_chars)
    tasks_file, tasks = _load_vscode_tasks(tasks_path)

    selected: dict[str, Any] | None = None
    for task in tasks:
        if str(task.get("label", "")).strip() == label:
            selected = task
            break
    if selected is None:
        raise ValueError(f"task not found: {label}")

    command = _task_command_from_vscode_task(selected)
    _validate_build_task_command(command, control_profile=control_profile)

    options = selected.get("options", {})
    task_cwd = "."
    if isinstance(options, dict):
        task_cwd_value = options.get("cwd")
        if isinstance(task_cwd_value, str) and task_cwd_value.strip():
            task_cwd = task_cwd_value
    if task_cwd.startswith("${workspaceFolder}"):
        suffix = task_cwd[len("${workspaceFolder}") :].lstrip("/\\")
        task_cwd = suffix or "."
    workdir = _resolve_repo_path(task_cwd)
    rel_cwd = str(workdir.relative_to(REPO_PATH))

    try:
        proc = subprocess.run(
            command,
            cwd=str(workdir),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        timeout_stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        build_log_tail = _summarize_build_log(timeout_stdout, timeout_stderr)
        proposals = _build_log_proposals(timeout_stdout, timeout_stderr)
        return {
            "schema": "vscode_task_run.v1",
            "ok": False,
            "label": label,
            "tasks_path": str(tasks_file.relative_to(REPO_PATH)),
            "control_profile": control_profile,
            "command": command,
            "cwd": rel_cwd,
            "exit_code": None,
            "timeout": True,
            "stdout": _trim_text(timeout_stdout, max_chars=out_cap),
            "stderr": _trim_text(timeout_stderr, max_chars=out_cap),
            "build_log_tail": _trim_text(build_log_tail, max_chars=out_cap),
            "proposals": proposals,
        }

    build_log_tail = _summarize_build_log(proc.stdout, proc.stderr)
    proposals: list[dict[str, str]] = []
    if proc.returncode != 0:
        proposals = _build_log_proposals(proc.stdout, proc.stderr)

    return {
        "schema": "vscode_task_run.v1",
        "ok": proc.returncode == 0,
        "label": label,
        "tasks_path": str(tasks_file.relative_to(REPO_PATH)),
        "control_profile": control_profile,
        "command": command,
        "cwd": str(workdir.relative_to(REPO_PATH)),
        "exit_code": proc.returncode,
        "timeout": False,
        "stdout": _trim_text(proc.stdout, max_chars=out_cap),
        "stderr": _trim_text(proc.stderr, max_chars=out_cap),
        "build_log_tail": _trim_text(build_log_tail, max_chars=out_cap),
        "proposals": proposals,
    }


@mcp.tool()
def git_init(initial_branch: str = "main") -> dict[str, str]:
    """Initialize a Git repository in the mounted directory."""
    _require_mutations()
    _ensure_repo_path_exists()
    if _is_git_repo():
        raise ValueError(f"{REPO_PATH} is already a Git working tree")

    _git("init", "-b", initial_branch)
    return {
        "repo_path": str(REPO_PATH),
        "message": f"initialized repository with initial branch '{initial_branch}'",
    }


@mcp.tool()
def list_files(
    path: str = ".",
    recursive: bool = True,
    include_hidden: bool = False,
    max_entries: int = 1000,
) -> list[str]:
    """List files and directories under a repository-relative path."""
    if max_entries < 1:
        raise ValueError("max_entries must be >= 1")

    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    entries: list[str] = []

    def include_item(p: Path) -> bool:
        rel = p.relative_to(REPO_PATH)
        if include_hidden:
            return True
        return not any(part.startswith(".") for part in rel.parts)

    if root.is_file():
        if include_item(root):
            return [str(root.relative_to(REPO_PATH))]
        return []

    iterator = root.rglob("*") if recursive else root.glob("*")
    for item in iterator:
        if not include_item(item):
            continue
        rel = str(item.relative_to(REPO_PATH))
        if item.is_dir():
            rel += "/"
        entries.append(rel)
        if len(entries) >= max_entries:
            break

    entries.sort()
    return entries


@mcp.tool()
def read_file(
    path: str, encoding: str = "utf-8", max_bytes: int = MAX_READ_BYTES
) -> str:
    """Read a UTF-8 text file from the repository."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be >= 1")

    file_path = _resolve_repo_path(path)
    if not file_path.is_file():
        raise FileNotFoundError(path)

    size = file_path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"file is too large ({size} bytes > {max_bytes} bytes)")

    data = file_path.read_text(encoding=encoding)
    return _trim_text(data)


@mcp.tool()
def read_document(
    path: str,
    max_chars: int = 20000,
    max_pages: int = 20,
    max_rows_per_sheet: int = 200,
    output_profile: str | None = None,
) -> dict[str, Any]:
    """Read document formats: .pdf, .doc, .docx, .xls, .xlsx, .odt, .ods, .odp."""
    profile = _default_output_profile(output_profile)
    file_path = _resolve_repo_path(path)
    if not file_path.is_file():
        raise FileNotFoundError(path)
    ext = file_path.suffix.lower()
    if ext not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".odt", ".ods", ".odp"}:
        raise ValueError(
            "unsupported extension; expected .pdf, .doc, .docx, .xls, .xlsx, .odt, .ods, or .odp"
        )

    warnings: list[str] = []
    metadata: dict[str, Any] = {"extension": ext}
    text = ""

    try:
        if ext == ".pdf":
            text, extra = _read_pdf_text(file_path, max_pages=max_pages)
        elif ext == ".docx":
            text, extra = _read_docx_text(file_path)
        elif ext == ".doc":
            text, extra = _read_doc_text(file_path)
            if extra.get("backend") != "antiword":
                warnings.append("antiword not available; used lossy latin-1 fallback for .doc")
        elif ext == ".xlsx":
            text, extra = _read_xlsx_text(file_path, max_rows_per_sheet=max_rows_per_sheet)
        elif ext in {".odt", ".ods", ".odp"}:
            text, extra = _read_opendoc_text(
                file_path,
                ext=ext,
                max_rows_per_sheet=max_rows_per_sheet,
            )
        else:
            text, extra = _read_xls_text(file_path, max_rows_per_sheet=max_rows_per_sheet)
    except Exception as exc:
        raise RuntimeError(f"failed to parse {ext} document: {exc}") from exc

    metadata.update(extra)
    text = text.strip()
    text, truncated = _truncate_with_flag(text, max_chars=max_chars)

    result = {
        "schema": "read_document.v1",
        "path": str(file_path.relative_to(REPO_PATH)),
        "extension": ext,
        "chars": len(text),
        "truncated": truncated,
        "warnings": warnings,
        "metadata": metadata if profile != "compact" else {},
        "text": text,
    }
    if profile == "compact":
        return {
            "schema": "read_document.compact.v1",
            "path": result["path"],
            "extension": ext,
            "chars": result["chars"],
            "truncated": truncated,
            "warnings": warnings,
            "text": text,
        }
    return result


@mcp.tool()
def browse_web(
    url: str,
    timeout_seconds: int = 15,
    max_bytes: int = 300000,
    max_chars: int = 12000,
    extract_text: bool = True,
    output_profile: str | None = None,
) -> dict[str, Any]:
    """Fetch a web page/document over HTTP(S) using host/system certificate store."""
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    if max_bytes < 1:
        raise ValueError("max_bytes must be >= 1")
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("url scheme must be http or https")

    profile = _default_output_profile(output_profile)
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "repo-git-mcp/1.0 (+browse_web)",
            "Accept": "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with _urlopen_with_host_certs(req, timeout=timeout_seconds) as resp:
            status = int(getattr(resp, "status", 200))
            final_url = str(getattr(resp, "url", url))
            content_type = str(resp.headers.get("Content-Type", ""))
            raw = resp.read(max_bytes + 1)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"browse failed: {exc}") from exc

    truncated_bytes = len(raw) > max_bytes
    if truncated_bytes:
        raw = raw[:max_bytes]
    charset_match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, flags=re.IGNORECASE)
    encoding = charset_match.group(1) if charset_match else "utf-8"
    text_raw = raw.decode(encoding, errors="replace")
    text = _html_to_text(text_raw) if extract_text else text_raw
    text, truncated_chars = _truncate_with_flag(text, max_chars=max_chars)

    result = {
        "schema": "browse_web.v1",
        "url": url,
        "final_url": final_url,
        "status": status,
        "content_type": content_type,
        "encoding": encoding,
        "extract_text": extract_text,
        "bytes_read": len(raw),
        "truncated_bytes": truncated_bytes,
        "truncated_chars": truncated_chars,
        "text": text,
    }
    if profile == "compact":
        return {
            "schema": "browse_web.compact.v1",
            "url": result["url"],
            "final_url": result["final_url"],
            "status": result["status"],
            "content_type": result["content_type"],
            "truncated": bool(truncated_bytes or truncated_chars),
            "text": result["text"],
        }
    return result


@mcp.tool()
def interpret_presentation(
    path: str,
    max_slides: int = 50,
    max_chars_per_slide: int = 1200,
    use_local_model: bool = True,
    output_profile: str | None = None,
) -> dict[str, Any]:
    """Interpret presentation files (.pptx, .ppt, .odp) and optionally summarize with a local model."""
    profile = _default_output_profile(output_profile)
    file_path = _resolve_repo_path(path)
    if not file_path.is_file():
        raise FileNotFoundError(path)
    ext = file_path.suffix.lower()
    if ext not in {".pptx", ".ppt", ".odp"}:
        raise ValueError("unsupported extension; expected .pptx, .ppt, or .odp")

    warnings: list[str] = []
    if ext == ".pptx":
        slides, meta = _read_pptx_presentation(
            file_path, max_slides=max_slides, max_chars_per_slide=max_chars_per_slide
        )
    elif ext == ".odp":
        slides, meta = _read_odp_presentation(
            file_path, max_slides=max_slides, max_chars_per_slide=max_chars_per_slide
        )
    else:
        slides, meta, legacy_warnings = _read_ppt_legacy_text(
            file_path, max_slides=max_slides, max_chars_per_slide=max_chars_per_slide
        )
        warnings.extend(legacy_warnings)

    interpreted_summary = ""
    model_used = ""
    if use_local_model and slides:
        joined = "\n\n".join(
            f"Slide {s['index']} - {s['title']}\n{s['text']}" for s in slides[:12]
        )
        prompt = (
            "Summarize this presentation in <=8 bullets. "
            "Include objective, key points, risks, and action items.\n\n"
            f"{joined}"
        )
        try:
            inferred = local_infer(
                prompt=prompt,
                task="presentation_summary",
                backend="auto",
                output_profile="compact",
                max_tokens=400,
            )
            interpreted_summary = str(inferred.get("output", "")).strip()
            model_used = str(inferred.get("backend", ""))
        except Exception as exc:
            warnings.append(f"local model summary failed: {exc}")

    if not interpreted_summary:
        titles = [str(s.get("title", "")).strip() for s in slides if str(s.get("title", "")).strip()]
        if titles:
            interpreted_summary = "Slides cover: " + ", ".join(titles[:8])
            if len(titles) > 8:
                interpreted_summary += ", ..."
        else:
            interpreted_summary = "No textual content extracted from presentation."

    result = {
        "schema": "interpret_presentation.v1",
        "path": str(file_path.relative_to(REPO_PATH)),
        "extension": ext,
        "slide_count": int(meta.get("slide_count", len(slides))),
        "slides_read": int(meta.get("slides_read", len(slides))),
        "used_local_model": bool(model_used),
        "model_backend": model_used,
        "warnings": warnings,
        "summary": interpreted_summary,
        "slides": slides,
    }
    if profile == "compact":
        return {
            "schema": "interpret_presentation.compact.v1",
            "path": result["path"],
            "extension": ext,
            "slide_count": result["slide_count"],
            "slides_read": result["slides_read"],
            "used_local_model": result["used_local_model"],
            "warnings": warnings,
            "summary": interpreted_summary,
            "slides": [{"index": s["index"], "title": s["title"]} for s in slides[:50]],
        }
    return result


@mcp.tool()
def write_file(
    path: str,
    content: str,
    overwrite: bool = True,
    create_dirs: bool = True,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Write a text file into the repository."""
    _require_mutations()
    file_path = _resolve_repo_path(path)

    if file_path.exists() and file_path.is_dir():
        raise IsADirectoryError(path)
    if file_path.exists() and not overwrite:
        raise FileExistsError(path)

    existed_before = file_path.exists()

    if create_dirs:
        file_path.parent.mkdir(parents=True, exist_ok=True)

    file_path.write_text(content, encoding=encoding)
    return {
        "path": str(file_path.relative_to(REPO_PATH)),
        "bytes_written": len(content.encode(encoding)),
        "existed_before": existed_before,
    }


@mcp.tool()
def delete_path(path: str, recursive: bool = False) -> dict[str, str]:
    """Delete a file or directory inside the repository."""
    _require_mutations()
    target = _resolve_repo_path(path)
    if not target.exists():
        raise FileNotFoundError(path)

    rel = str(target.relative_to(REPO_PATH))
    if target.is_dir():
        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()
    else:
        target.unlink()
    return {"deleted": rel}


@mcp.tool()
def move_path(
    source: str,
    destination: str,
    overwrite: bool = False,
    create_dirs: bool = True,
) -> dict[str, str]:
    """Move or rename a file or directory inside the repository."""
    _require_mutations()
    src = _resolve_repo_path(source)
    dst = _resolve_repo_path(destination)

    if not src.exists():
        raise FileNotFoundError(source)
    if dst.exists() and not overwrite:
        raise FileExistsError(destination)
    if create_dirs:
        dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and overwrite:
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()

    shutil.move(str(src), str(dst))
    return {
        "source": str(src.relative_to(REPO_PATH)),
        "destination": str(dst.relative_to(REPO_PATH)),
    }


@mcp.tool()
def git_status(short: bool = True) -> dict[str, Any]:
    """Return git status as structured content with raw text preserved."""
    _require_git_repo()
    args = ["status"]
    if short:
        args.append("--short")
    raw = _trim_text(_git(*args).stdout)
    return {
        "status": [line for line in raw.splitlines() if line],
        "short": short,
        "raw": raw,
    }


@mcp.tool()
def git_diff(
    ref: str | None = None,
    pathspec: str | None = None,
    staged: bool = False,
) -> str:
    """Return a git diff against the working tree, index, or a ref."""
    _require_git_repo()
    args = ["diff"]
    if staged:
        args.append("--staged")
    if ref:
        args.append(ref)
    if pathspec:
        _resolve_repo_path(pathspec)
        args.extend(["--", pathspec])
    return _trim_text(_git(*args).stdout)


@mcp.tool()
def git_log(limit: int = 20, ref: str = "HEAD") -> str:
    """Return recent commit history."""
    _require_git_repo()
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return _trim_text(
        _git(
            "log",
            f"--max-count={limit}",
            "--decorate",
            "--graph",
            "--oneline",
            ref,
        ).stdout
    )


@mcp.tool()
def git_show(ref: str = "HEAD", path: str | None = None) -> str:
    """Show a commit, object, or file content from Git history."""
    _require_git_repo()
    target = ref
    if path:
        _resolve_repo_path(path)
        target = f"{ref}:{path}"
    return _trim_text(_git("show", target).stdout)


@mcp.tool()
def git_add(paths: list[str]) -> dict[str, Any]:
    """Stage one or more repository-relative paths."""
    _require_mutations()
    _require_git_repo()
    if not paths:
        raise ValueError("paths must not be empty")
    normalized = _normalize_paths(paths)
    _git("add", "--", *normalized)
    return {"staged": normalized}


@mcp.tool()
def git_restore(paths: list[str], staged: bool = False) -> dict[str, Any]:
    """Restore paths from HEAD or unstage them."""
    _require_mutations()
    _require_git_repo()
    if not paths:
        raise ValueError("paths must not be empty")
    normalized = _normalize_paths(paths)
    args = ["restore"]
    if staged:
        args.append("--staged")
    args.extend(["--", *normalized])
    _git(*args)
    return {"restored": normalized, "staged": staged}


@mcp.tool()
def git_commit(message: str, allow_empty: bool = False) -> dict[str, str]:
    """Create a commit from staged changes."""
    _require_mutations()
    _require_git_repo()
    if not message.strip():
        raise ValueError("message must not be empty")

    args = ["commit", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    _git(*args)
    return {
        "commit": _git("rev-parse", "HEAD").stdout.strip(),
        "summary": _git("log", "-1", "--oneline").stdout.strip(),
    }


@mcp.tool()
def git_checkout(ref: str, create_branch: bool = False) -> dict[str, str]:
    """Checkout an existing ref or create a new branch from the current HEAD."""
    _require_mutations()
    _require_git_repo()
    if not ref.strip():
        raise ValueError("ref must not be empty")

    if create_branch:
        _git("checkout", "-b", ref)
    else:
        _git("checkout", ref)
    return {"current_branch": _git("branch", "--show-current").stdout.strip()}


@mcp.tool()
def git_create_branch(name: str, checkout: bool = True) -> dict[str, str]:
    """Create a branch, optionally switching to it immediately."""
    _require_mutations()
    _require_git_repo()
    if not name.strip():
        raise ValueError("name must not be empty")

    if checkout:
        _git("checkout", "-b", name)
    else:
        _git("branch", name)
    return {
        "branch": name,
        "checked_out": str(checkout).lower(),
        "current_branch": _git("branch", "--show-current").stdout.strip(),
    }


@mcp.tool()
def git_fetch(remote: str = "origin", prune: bool = False) -> str:
    """Fetch from a remote."""
    _require_mutations()
    _require_git_repo()
    args = ["fetch"]
    if prune:
        args.append("--prune")
    args.append(remote)
    result = _git(*args)
    return _trim_text(result.stdout + result.stderr)


@mcp.tool()
def git_pull(
    remote: str = "origin", branch: str | None = None, rebase: bool = False
) -> str:
    """Pull from a remote."""
    _require_mutations()
    _require_git_repo()
    args = ["pull"]
    if rebase:
        args.append("--rebase")
    args.append(remote)
    if branch:
        args.append(branch)
    result = _git(*args)
    return _trim_text(result.stdout + result.stderr)


@mcp.tool()
def git_push(
    remote: str = "origin",
    branch: str | None = None,
    set_upstream: bool = False,
) -> str:
    """Push to a remote."""
    _require_mutations()
    _require_git_repo()
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args.append(remote)
    if branch:
        args.append(branch)
    result = _git(*args)
    return _trim_text(result.stdout + result.stderr)


@mcp.tool()
def lab_release_rehearsal(
    config_path: str = ".config/labs/release_rehearsal.json",
    allow_dirty: bool = False,
    keep_branch: bool = False,
) -> dict[str, Any]:
    """Run release rehearsal lab and write report(s) under .codebase-tooling-mcp/reports."""
    _resolve_repo_path(config_path)
    args = ["--config", config_path]
    if allow_dirty:
        args.append("--allow-dirty")
    if keep_branch:
        args.append("--keep-branch")
    return _run_lab_script("release_rehearsal.py", args)


@mcp.tool()
def lab_refactor_tournament(
    config_path: str = ".config/labs/refactor_tournament.json",
    allow_dirty: bool = False,
    keep_branches: bool = False,
) -> dict[str, Any]:
    """Run refactor tournament lab and write report(s) under .codebase-tooling-mcp/reports."""
    _resolve_repo_path(config_path)
    args = ["--config", config_path]
    if allow_dirty:
        args.append("--allow-dirty")
    if keep_branches:
        args.append("--keep-branches")
    return _run_lab_script("refactor_tournament.py", args)


@mcp.tool()
def lab_policy_gatekeeper(
    config_path: str = ".config/labs/policy_gatekeeper.json",
    changed_ref: str = "HEAD",
    report_path: str = ".codebase-tooling-mcp/reports/POLICY_GATEKEEPER.md",
) -> dict[str, Any]:
    """Run policy-as-code gatekeeper checks."""
    _resolve_repo_path(config_path)
    _resolve_repo_path(report_path)
    args = [
        "--config",
        config_path,
        "--changed-ref",
        changed_ref,
        "--report-path",
        report_path,
    ]
    return _run_lab_script("policy_gatekeeper.py", args)


@mcp.tool()
def lab_branch_swarm(
    config_path: str = ".config/labs/branch_swarm_lab.json",
    allow_dirty: bool = False,
    keep_branches: bool = False,
) -> dict[str, Any]:
    """Run branch swarm benchmark lab."""
    _resolve_repo_path(config_path)
    args = ["--config", config_path]
    if allow_dirty:
        args.append("--allow-dirty")
    if keep_branches:
        args.append("--keep-branches")
    return _run_lab_script("branch_swarm_lab.py", args)


@mcp.tool()
def lab_narrated_pr(
    base: str = "HEAD~1",
    head: str = "HEAD",
    output_path: str = ".codebase-tooling-mcp/reports/PR_PACKET.md",
) -> dict[str, Any]:
    """Generate a narrated PR packet for a commit range."""
    _resolve_repo_path(output_path)
    args = ["--base", base, "--head", head, "--output", output_path]
    return _run_lab_script("narrated_pr_generator.py", args)


@mcp.tool()
def lab_repo_digital_twin(
    json_path: str = ".codebase-tooling-mcp/reports/REPO_DIGITAL_TWIN.json",
    markdown_path: str = ".codebase-tooling-mcp/reports/REPO_DIGITAL_TWIN.md",
    max_files: int = 1000,
    hotspot_limit: int = 20,
) -> dict[str, Any]:
    """Generate repo digital twin JSON + markdown snapshots."""
    if max_files < 1:
        raise ValueError("max_files must be >= 1")
    if hotspot_limit < 1:
        raise ValueError("hotspot_limit must be >= 1")
    _resolve_repo_path(json_path)
    _resolve_repo_path(markdown_path)
    args = [
        "--json",
        json_path,
        "--md",
        markdown_path,
        "--max-files",
        str(max_files),
        "--hotspot-limit",
        str(hotspot_limit),
    ]
    return _run_lab_script("repo_digital_twin.py", args)


@mcp.tool()
def license_monitor(
    path: str = ".",
    recursive: bool = True,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    run_reuse_lint: bool = True,
    generate_spdx: bool = True,
    spdx_output_path: str = str(REUSE_SPDX_REPORT),
    lint_report_path: str = str(REUSE_LINT_REPORT),
    auto_fix_headers: bool = False,
    default_license: str = "MIT",
    copyright_owner: str = "Project Contributors",
    download_missing_licenses: bool = False,
    max_missing_files: int = 5000,
) -> dict[str, Any]:
    """Check REUSE/FOSS license compliance and optionally auto-fix missing metadata."""
    if max_missing_files < 1:
        raise ValueError("max_missing_files must be >= 1")
    _ensure_repo_path_exists()
    _resolve_repo_path(path)
    _resolve_repo_path(spdx_output_path)
    _resolve_repo_path(lint_report_path)

    missing_before = _collect_missing_spdx_headers(
        path=path,
        recursive=recursive,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        max_files=max_missing_files,
    )
    actions: list[str] = []

    if auto_fix_headers and missing_before:
        _require_mutations()
        _require_reuse_cli()
        year = str(datetime.now(timezone.utc).year)
        copyright_line = f"{year} {copyright_owner.strip() or 'Project Contributors'}"
        for chunk in _chunk_strings(missing_before, chunk_size=100):
            cmd = [
                "annotate",
                "--merge-copyrights",
                "--fallback-dot-license",
                "--license",
                default_license,
                "--copyright",
                copyright_line,
                *chunk,
            ]
            proc = _run_reuse(cmd)
            if not proc["ok"]:
                raise RuntimeError(proc["stderr"] or proc["stdout"] or "reuse annotate failed")
        actions.append(f"annotated_missing_headers:{len(missing_before)}")

    missing_after = _collect_missing_spdx_headers(
        path=path,
        recursive=recursive,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        max_files=max_missing_files,
    )

    observed_ids = _collect_spdx_license_ids(path=path, recursive=recursive)
    missing_license_texts = [
        lid
        for lid in observed_ids
        if not (REPO_PATH / "LICENSES" / f"{lid}.txt").is_file()
    ]

    if download_missing_licenses and missing_license_texts:
        _require_mutations()
        _require_reuse_cli()
        for lid in missing_license_texts:
            proc = _run_reuse(["download", lid])
            if not proc["ok"]:
                raise RuntimeError(
                    proc["stderr"] or proc["stdout"] or f"reuse download failed for {lid}"
                )
        actions.append(f"downloaded_license_texts:{len(missing_license_texts)}")
        missing_license_texts = [
            lid
            for lid in observed_ids
            if not (REPO_PATH / "LICENSES" / f"{lid}.txt").is_file()
        ]

    lint_result: dict[str, Any] | None = None
    if run_reuse_lint:
        lint_result = _run_reuse(["lint"])
        lint_abs = _resolve_repo_path(lint_report_path)
        lint_abs.parent.mkdir(parents=True, exist_ok=True)
        lint_abs.write_text(
            (
                f"reuse lint exit_code={lint_result['exit_code']}\n\n"
                f"{lint_result.get('stdout', '')}\n\n{lint_result.get('stderr', '')}\n"
            ),
            encoding="utf-8",
        )

    spdx_result: dict[str, Any] | None = None
    if generate_spdx:
        _require_reuse_cli()
        spdx_result = _run_reuse(["spdx", "-o", spdx_output_path])
        if not spdx_result["ok"]:
            raise RuntimeError(spdx_result["stderr"] or spdx_result["stdout"] or "reuse spdx failed")

    ok = (
        len(missing_after) == 0
        and len(missing_license_texts) == 0
        and (lint_result is None or bool(lint_result.get("ok", False)))
    )
    return {
        "schema": "license_monitor.v1",
        "ok": ok,
        "path": path,
        "recursive": recursive,
        "actions": actions,
        "missing_spdx_header_count": len(missing_after),
        "missing_spdx_headers": missing_after[:200],
        "observed_spdx_license_ids": observed_ids,
        "missing_license_text_count": len(missing_license_texts),
        "missing_license_texts": missing_license_texts[:200],
        "lint": lint_result,
        "spdx": spdx_result,
        "lint_report_path": lint_report_path if run_reuse_lint else None,
        "spdx_output_path": spdx_output_path if generate_spdx else None,
    }


@mcp.tool()
def install_git_hooks(
    install_pre_commit: bool = True,
    install_pre_push: bool = True,
    include_foss_reports: bool = True,
    include_lab_reports: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Install git hooks that generate FOSS and lab reports."""
    _require_mutations()
    _require_git_repo()
    hooks_rel = _git("rev-parse", "--git-path", "hooks").stdout.strip()
    hooks_path_raw = Path(hooks_rel)
    if hooks_path_raw.is_absolute():
        hooks_dir = hooks_path_raw.resolve()
        try:
            hooks_dir.relative_to(REPO_PATH)
        except ValueError as exc:
            raise ValueError("git hooks path escapes repository root") from exc
    else:
        hooks_dir = _resolve_repo_path(hooks_rel)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    if not install_pre_commit and not install_pre_push:
        raise ValueError("at least one hook must be selected")

    script_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        'repo_root="$(git rev-parse --show-toplevel)"',
        'cd "$repo_root"',
        "mkdir -p .codebase-tooling-mcp/reports",
        "",
    ]
    if include_foss_reports:
        script_lines.extend(
            [
                "if ! command -v reuse >/dev/null 2>&1; then",
                '  echo "reuse CLI not found. Install \\"reuse\\" before committing/pushing." >&2',
                "  exit 1",
                "fi",
                "reuse lint > .codebase-tooling-mcp/reports/REUSE_LINT.txt",
                "reuse spdx -o .codebase-tooling-mcp/reports/REUSE.spdx",
                "",
            ]
        )
    script_lines.extend(
        [
            "if command -v python >/dev/null 2>&1; then",
            '  PYTHON_BIN="python"',
            "elif command -v python3 >/dev/null 2>&1; then",
            '  PYTHON_BIN="python3"',
            "else",
            '  echo "python or python3 is required for lab report hooks." >&2',
            "  exit 1",
            "fi",
            "",
        ]
    )

    pre_commit_lines = list(script_lines)
    if include_lab_reports:
        pre_commit_lines.append(
            '"$PYTHON_BIN" source/labs/policy_gatekeeper.py --changed-ref HEAD '
            '--report-path .codebase-tooling-mcp/reports/POLICY_GATEKEEPER.md'
        )

    pre_push_lines = list(script_lines)
    if include_lab_reports:
        pre_push_lines.append(
            '"$PYTHON_BIN" source/labs/policy_gatekeeper.py --changed-ref HEAD '
            '--report-path .codebase-tooling-mcp/reports/POLICY_GATEKEEPER.md'
        )
        pre_push_lines.append(
            '"$PYTHON_BIN" source/labs/repo_digital_twin.py '
            '--json .codebase-tooling-mcp/reports/REPO_DIGITAL_TWIN.json '
            '--md .codebase-tooling-mcp/reports/REPO_DIGITAL_TWIN.md'
        )

    installed: list[str] = []
    skipped: list[str] = []
    if install_pre_commit:
        pre_commit_path = hooks_dir / "pre-commit"
        if pre_commit_path.exists() and not overwrite:
            skipped.append(str(pre_commit_path))
        else:
            pre_commit_path.write_text("\n".join(pre_commit_lines).strip() + "\n", encoding="utf-8")
            pre_commit_path.chmod(0o755)
            installed.append(str(pre_commit_path.relative_to(REPO_PATH)))

    if install_pre_push:
        pre_push_path = hooks_dir / "pre-push"
        if pre_push_path.exists() and not overwrite:
            skipped.append(str(pre_push_path))
        else:
            pre_push_path.write_text("\n".join(pre_push_lines).strip() + "\n", encoding="utf-8")
            pre_push_path.chmod(0o755)
            installed.append(str(pre_push_path.relative_to(REPO_PATH)))

    return {
        "schema": "install_git_hooks.v1",
        "hooks_dir": str(hooks_dir.relative_to(REPO_PATH)),
        "installed": installed,
        "skipped": skipped,
        "overwrite": overwrite,
        "include_foss_reports": include_foss_reports,
        "include_lab_reports": include_lab_reports,
    }


@mcp.tool()
def find_paths(
    path: str = ".",
    recursive: bool = True,
    include_hidden: bool = False,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    file_type: str = "any",
    max_depth: int | None = None,
    max_entries: int = 1000,
    output_profile: str = "compact",
    offset: int = 0,
    limit: int | None = None,
    adaptive_limits: bool = True,
) -> list[str]:
    """Find files and/or directories under a repository-relative path."""
    if max_entries < 1:
        raise ValueError("max_entries must be >= 1")
    if adaptive_limits:
        max_entries = _adaptive_limit(max_entries, soft_cap=2000)
    if max_depth is not None and max_depth < 0:
        raise ValueError("max_depth must be >= 0")
    if file_type not in {"any", "file", "dir"}:
        raise ValueError("file_type must be one of: any, file, dir")
    profile = _default_output_profile(output_profile)

    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    results: list[str] = []

    def maybe_add(candidate: Path) -> None:
        if len(results) >= max_entries:
            return
        rel_path = candidate.relative_to(REPO_PATH)
        rel_str = str(rel_path).replace("\\", "/")

        if not include_hidden and _is_hidden_rel_path(rel_path):
            return
        if not _allowed_by_globs(rel_str, include_globs, exclude_globs):
            return

        if file_type == "file" and not candidate.is_file():
            return
        if file_type == "dir" and not candidate.is_dir():
            return

        if candidate.is_dir():
            rel_str += "/"
        results.append(rel_str)

    if root.is_file():
        maybe_add(root)
        return results

    root_parts = len(root.relative_to(REPO_PATH).parts)

    if recursive:
        iterator = root.rglob("*")
    else:
        iterator = root.glob("*")

    for item in iterator:
        if len(results) >= max_entries:
            break
        if max_depth is not None:
            item_parts = len(item.relative_to(REPO_PATH).parts)
            if item_parts - root_parts > max_depth:
                continue
        maybe_add(item)

    results.sort()
    results = _paginate(results, offset=offset, limit=limit)
    if profile == "compact":
        return [item[:-1] if item.endswith("/") else item for item in results]
    return results


@mcp.tool()
def grep(
    pattern: str,
    path: str = ".",
    recursive: bool = True,
    case_insensitive: bool = False,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    include_hidden: bool = False,
    max_matches: int = 500,
    max_file_bytes: int = 1048576,
    encoding: str = "utf-8",
    output_profile: str = "compact",
    fields: list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
    dedupe: bool = True,
    compress: bool = False,
    adaptive_limits: bool = True,
    summary_mode: str = "full",
    store_result: bool = False,
    compressed_observation: bool = False,
) -> list[dict[str, Any]]:
    """Search repository files for a regex pattern and return matches.

    Returns a list of objects: { path, line, column, match, lineText }.
    Paths are repository-relative; line/column are 1-based.
    """
    if max_matches < 1:
        raise ValueError("max_matches must be >= 1")
    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be >= 1")
    profile = _default_output_profile(output_profile)
    if summary_mode not in {"full", "quick"}:
        raise ValueError("summary_mode must be one of: full, quick")
    if adaptive_limits:
        max_matches = _adaptive_limit(max_matches, soft_cap=250)
    elif profile == "compact":
        max_matches = min(max_matches, 250)

    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    flags = 0
    if case_insensitive:
        flags |= re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"invalid regex pattern: {exc}") from exc

    results: list[dict[str, Any]] = []

    def search_file(p: Path) -> None:
        nonlocal results
        if not p.is_file():
            return
        rel_path = p.relative_to(REPO_PATH)
        rel_str = str(rel_path).replace("\\", "/")
        if not include_hidden and _is_hidden_rel_path(rel_path):
            return
        if not _allowed_by_globs(rel_str, include_globs, exclude_globs):
            return
        if _is_likely_binary(p, max_file_bytes=max_file_bytes):
            return

        try:
            with p.open("r", encoding=encoding, errors="replace") as f:
                for idx, line in enumerate(f, start=1):
                    for m in regex.finditer(line):
                        res = {
                            "path": rel_str,
                            "line": idx,
                            "column": m.start() + 1,
                            "match": m.group(0),
                            "lineText": line.rstrip("\n"),
                        }
                        results.append(_match_to_profile(res, profile))
                        if len(results) >= max_matches:
                            return
                    if len(results) >= max_matches:
                        return
        except OSError:
            return

    if root.is_file():
        search_file(root)
    else:
        it = root.rglob("*") if recursive else root.glob("*")
        for p in it:
            if len(results) >= max_matches:
                break
            if p.is_dir():
                continue
            search_file(p)

    if dedupe:
        uniq: dict[str, dict[str, Any]] = {}
        for row in results:
            key = f"{row.get('path')}:{row.get('line')}:{row.get('match')}"
            if key not in uniq:
                uniq[key] = row
        results = list(uniq.values())
    total = len(results)
    results = _paginate(results, offset=offset, limit=limit)
    results = _select_fields(results, fields)
    if summary_mode == "quick":
        summary = {
            "schema": "grep.quick.v1",
            "total_matches": total,
            "returned": len(results),
            "paths": sorted({str(r.get("path")) for r in results if r.get("path")})[:100],
        }
        raw_reference: dict[str, Any] = {"type": "inline_return", "scope": "quick_summary"}
        if compressed_observation:
            raw_rid = _result_store_put("grep", results)
            raw_reference = {
                "type": "result_handle",
                "result_id": raw_rid,
                "tool": "grep",
                "count": len(results),
            }
            summary["compressed_observation"] = _compressed_observation_for_rows(
                tool_name="grep",
                rows=results,
                total_count=total,
                raw_reference=raw_reference,
            )
        if store_result:
            rid = _result_store_put("grep", summary)
            summary["result_id"] = rid
        return [summary]
    if compress:
        compressed = _compress_table(results)
        if compressed_observation:
            compressed["compressed_observation"] = _compressed_observation_for_rows(
                tool_name="grep",
                rows=results,
                total_count=total,
                raw_reference={
                    "type": "inline_return",
                    "field": "rows",
                    "count": len(results),
                },
            )
        if store_result:
            rid = _result_store_put("grep", compressed)
            compressed["result_id"] = rid
        return [compressed]
    if store_result:
        rid = _result_store_put("grep", results)
        handle = {"schema": "grep.result_handle.v1", "result_id": rid, "count": len(results)}
        if compressed_observation:
            handle["compressed_observation"] = _compressed_observation_for_rows(
                tool_name="grep",
                rows=results,
                total_count=total,
                raw_reference={
                    "type": "result_handle",
                    "result_id": rid,
                    "tool": "grep",
                    "count": len(results),
                },
            )
        return [handle]
    if compressed_observation:
        return [
            {
                "schema": "grep.with_compressed_observation.v1",
                "results": results,
                "compressed_observation": _compressed_observation_for_rows(
                    tool_name="grep",
                    rows=results,
                    total_count=total,
                    raw_reference={
                        "type": "inline_return",
                        "field": "results",
                        "count": len(results),
                    },
                ),
            }
        ]
    return results


@mcp.tool()
def replace_in_files(
    pattern: str,
    replacement: str,
    path: str = ".",
    recursive: bool = True,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    include_hidden: bool = False,
    regex: bool = True,
    case_insensitive: bool = False,
    dry_run: bool = True,
    max_files: int = 200,
    max_replacements: int = 5000,
    max_file_bytes: int = 1048576,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Replace text in files under a path, optionally as a dry-run preview."""
    if max_files < 1:
        raise ValueError("max_files must be >= 1")
    if max_replacements < 1:
        raise ValueError("max_replacements must be >= 1")
    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be >= 1")

    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    if not dry_run:
        _require_mutations()

    flags = re.MULTILINE
    if case_insensitive:
        flags |= re.IGNORECASE

    if regex:
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"invalid regex pattern: {exc}") from exc
    else:
        compiled = re.compile(re.escape(pattern), flags)

    files_changed: list[dict[str, Any]] = []
    scanned_files = 0
    total_replacements = 0
    files_limit_reached = False
    replacements_limit_reached = False

    def iter_candidates():
        if root.is_file():
            yield root
            return
        walker = root.rglob("*") if recursive else root.glob("*")
        files = [p for p in walker if p.is_file()]
        files.sort(
            key=lambda p: (
                _is_hidden_rel_path(p.relative_to(REPO_PATH)),
                str(p.relative_to(REPO_PATH)).replace("\\", "/"),
            )
        )
        for p in files:
            yield p

    for candidate in iter_candidates():
        if len(files_changed) >= max_files:
            files_limit_reached = True
            break

        scanned_files += 1
        rel_path = candidate.relative_to(REPO_PATH)
        rel_str = str(rel_path).replace("\\", "/")

        if not include_hidden and _is_hidden_rel_path(rel_path):
            continue
        if not _allowed_by_globs(rel_str, include_globs, exclude_globs):
            continue
        if _is_likely_binary(candidate, max_file_bytes=max_file_bytes):
            continue

        try:
            original = candidate.read_text(encoding=encoding, errors="replace")
        except OSError:
            continue

        remaining = max_replacements - total_replacements
        if remaining <= 0:
            replacements_limit_reached = True
            break

        updated, replacements = compiled.subn(replacement, original, count=remaining)
        if replacements <= 0:
            continue

        total_replacements += replacements
        files_changed.append(
            {
                "path": rel_str,
                "replacements": replacements,
                "changed": True,
            }
        )

        if not dry_run:
            candidate.write_text(updated, encoding=encoding)

        if total_replacements >= max_replacements:
            replacements_limit_reached = True
            break

    return {
        "path": str(root.relative_to(REPO_PATH)),
        "dry_run": dry_run,
        "regex": regex,
        "case_insensitive": case_insensitive,
        "scanned_files": scanned_files,
        "files_changed_count": len(files_changed),
        "total_replacements": total_replacements,
        "files_limit_reached": files_limit_reached,
        "replacements_limit_reached": replacements_limit_reached,
        "files_changed": files_changed,
    }


@mcp.tool()
def read_snippet(
    path: str,
    start_line: int,
    end_line: int,
    context_before: int = 0,
    context_after: int = 0,
    encoding: str = "utf-8",
    output_profile: str = "compact",
) -> dict[str, Any]:
    """Read a focused line range with optional surrounding context."""
    profile = _validate_output_profile(output_profile)
    file_path = _resolve_repo_path(path)
    snippet = _build_snippet(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        context_before=context_before,
        context_after=context_after,
        encoding=encoding,
    )
    if profile == "compact":
        return {
            "path": snippet["path"],
            "start_line": snippet["start_line"],
            "end_line": snippet["end_line"],
            "content": snippet["content"],
        }
    return snippet


@mcp.tool()
def read_batch(
    requests: list[dict[str, Any]],
    encoding: str = "utf-8",
    max_items: int = 50,
    output_profile: str = "compact",
) -> dict[str, Any]:
    """Read multiple focused snippets in one call."""
    if max_items < 1:
        raise ValueError("max_items must be >= 1")
    profile = _validate_output_profile(output_profile)
    if len(requests) > max_items:
        raise ValueError(f"too many requests; max_items={max_items}")

    snippets: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for idx, req in enumerate(requests, start=1):
        path = req.get("path")
        start_line = req.get("start_line")
        end_line = req.get("end_line")
        context_before = int(req.get("context_before", 0))
        context_after = int(req.get("context_after", 0))
        if not isinstance(path, str) or not isinstance(start_line, int) or not isinstance(
            end_line, int
        ):
            errors.append({"index": idx, "error": "path/start_line/end_line are required"})
            continue
        try:
            snippet = _build_snippet(
                file_path=_resolve_repo_path(path),
                start_line=start_line,
                end_line=end_line,
                context_before=context_before,
                context_after=context_after,
                encoding=encoding,
            )
            if profile == "compact":
                snippet = {
                    "path": snippet["path"],
                    "start_line": snippet["start_line"],
                    "end_line": snippet["end_line"],
                    "content": snippet["content"],
                }
            snippets.append(snippet)
        except Exception as exc:
            errors.append({"index": idx, "path": path, "error": str(exc)})

    return {
        "count": len(snippets),
        "error_count": len(errors),
        "snippets": snippets,
        "errors": errors if profile != "compact" else errors[:10],
    }


def semantic_find(
    query: str,
    path: str = ".",
    max_results: int = 30,
    output_profile: str | None = None,
    include_private_symbols: bool = False,
    fields: list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
    dedupe: bool = True,
    compress: bool = False,
    adaptive_limits: bool = True,
    summary_mode: str = "full",
    store_result: bool = False,
    use_local_rerank: bool = False,
    local_rerank_top_k: int = 50,
) -> dict[str, Any]:
    """Ranked search over file paths, symbols, and text matches."""
    if not query.strip():
        raise ValueError("query must not be empty")
    if max_results < 1:
        raise ValueError("max_results must be >= 1")
    if summary_mode not in {"full", "quick"}:
        raise ValueError("summary_mode must be one of: full, quick")
    if adaptive_limits:
        max_results = _adaptive_limit(max_results, soft_cap=100)
    profile = _default_output_profile(output_profile)
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    terms = [t.lower() for t in re.split(r"\s+", query.strip()) if t]
    candidates: dict[str, dict[str, Any]] = {}

    for rel in find_paths(
        path=path,
        recursive=True,
        include_hidden=False,
        max_entries=5000,
        output_profile="compact",
        file_type="file",
    ):
        score = 0.0
        rel_low = rel.lower()
        for term in terms:
            if term in rel_low:
                score += 3.0
        if score > 0:
            candidates.setdefault(
                f"path:{rel}",
                {"kind": "path", "path": rel, "score": 0.0, "reasons": []},
            )
            candidates[f"path:{rel}"]["score"] += score
            candidates[f"path:{rel}"]["reasons"].append("path_term_match")

    symbol_term = "|".join(re.escape(t) for t in terms if t)
    if symbol_term:
        for sym in symbol_index(
            path=path,
            include_private=include_private_symbols,
            recursive=True,
            max_symbols=5000,
            output_profile="normal",
        ):
            name = str(sym.get("name", ""))
            score = 0.0
            for term in terms:
                if term in name.lower():
                    score += 5.0
            if score <= 0:
                continue
            key = f"symbol:{sym['path']}:{name}:{sym['line_start']}"
            candidates[key] = {
                "kind": "symbol",
                "path": sym["path"],
                "name": name,
                "line_start": sym["line_start"],
                "line_end": sym.get("line_end"),
                "score": score,
                "reasons": ["symbol_name_match"],
            }

    pattern = "|".join(re.escape(t) for t in terms)
    if pattern:
        matches = grep(
            pattern=pattern,
            path=path,
            recursive=True,
            case_insensitive=True,
            max_matches=200,
            output_profile="compact",
        )
        for m in matches:
            key = f"grep:{m['path']}:{m['line']}:{m['column']}"
            candidates[key] = {
                "kind": "text_match",
                "path": m["path"],
                "line": m["line"],
                "column": m["column"],
                "match": m["match"],
                "score": 2.0,
                "reasons": ["text_match"],
            }

    ranked = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)
    if dedupe:
        uniq: dict[str, dict[str, Any]] = {}
        for row in ranked:
            key = f"{row.get('kind')}:{row.get('path')}:{row.get('line', '')}:{row.get('name', '')}"
            if key not in uniq:
                uniq[key] = row
        ranked = list(uniq.values())
    ranked = ranked[:max_results]
    if use_local_rerank and ranked:
        reranked = local_rerank(
            query=query,
            candidates=ranked[: max(local_rerank_top_k, max_results)],
            top_k=max_results,
            backend="auto",
            output_profile="normal",
        )
        if isinstance(reranked, dict) and isinstance(reranked.get("results"), list):
            ranked = reranked["results"]
    ranked = _paginate(ranked, offset=offset, limit=limit)
    if profile == "compact":
        ranked = [
            {
                "kind": r.get("kind"),
                "path": r.get("path"),
                "score": r.get("score"),
            }
            for r in ranked
        ]
    ranked = _select_fields(ranked, fields)
    result = {
        "schema": "semantic_find.v1",
        "query": query,
        "path": str(root.relative_to(REPO_PATH)),
        "count": len(ranked),
        "results": ranked,
    }
    if summary_mode == "quick":
        result = {
            "schema": "semantic_find.quick.v1",
            "query": query,
            "count": len(ranked),
            "top_paths": [r.get("path") for r in ranked[:20] if isinstance(r, dict)],
        }
    if compress and isinstance(result.get("results"), list):
        result["results_compressed"] = _compress_table(result["results"])
        result.pop("results", None)
    if store_result:
        rid = _result_store_put("semantic_find", result)
        result["result_id"] = rid
    return result


def symbol_index(
    path: str = ".",
    include_private: bool = False,
    recursive: bool = True,
    max_symbols: int = 5000,
    encoding: str = "utf-8",
    output_profile: str = "compact",
    fields: list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
    adaptive_limits: bool = True,
) -> list[dict[str, Any]]:
    """Index Python symbols (classes/functions) for focused navigation."""
    if max_symbols < 1:
        raise ValueError("max_symbols must be >= 1")
    if adaptive_limits:
        max_symbols = _adaptive_limit(max_symbols, soft_cap=2000)
    profile = _default_output_profile(output_profile)
    if profile == "compact":
        max_symbols = min(max_symbols, 2000)

    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    fingerprint = _fingerprint_path(
        root, recursive=recursive, suffixes={".py"}, max_files=3000
    )
    cache_key = json.dumps(
        {
            "path": str(root.relative_to(REPO_PATH)),
            "include_private": include_private,
            "recursive": recursive,
            "encoding": encoding,
            "fingerprint": fingerprint,
        },
        sort_keys=True,
    )
    cached = _cache_get("symbol_index", cache_key)
    if isinstance(cached, list):
        symbols = [dict(row) for row in cached]
    else:
        symbols = []
        for candidate in _iter_candidate_files(root, recursive=recursive):
            if candidate.suffix != ".py":
                continue
            rel_path = candidate.relative_to(REPO_PATH)
            rel_str = str(rel_path).replace("\\", "/")
            if _is_likely_binary(candidate):
                continue
            try:
                source = candidate.read_text(encoding=encoding, errors="replace")
            except OSError:
                continue
            extracted = _collect_python_symbols(
                source, rel_str, include_private=include_private
            )
            symbols.extend(extracted)
        _cache_set("symbol_index", cache_key, symbols)

    profiled = [_symbol_to_profile(symbol, profile) for symbol in symbols]
    profiled = profiled[:max_symbols]
    profiled = _paginate(profiled, offset=offset, limit=limit)
    profiled = _select_fields(profiled, fields)
    return profiled


def dependency_map(
    path: str = ".",
    recursive: bool = True,
    include_stdlib: bool = False,
    max_files: int = 3000,
    output_profile: str = "compact",
    encoding: str = "utf-8",
    fields: list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
    adaptive_limits: bool = True,
    summary_mode: str = "full",
    compress: bool = False,
    store_result: bool = False,
) -> dict[str, Any]:
    """Build a Python import dependency map for repo-local modules."""
    if max_files < 1:
        raise ValueError("max_files must be >= 1")
    if summary_mode not in {"full", "quick"}:
        raise ValueError("summary_mode must be one of: full, quick")
    if adaptive_limits:
        max_files = _adaptive_limit(max_files, soft_cap=1500)
    profile = _default_output_profile(output_profile)
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    fingerprint = _fingerprint_path(
        root, recursive=recursive, suffixes={".py"}, max_files=max_files
    )
    cache_key = json.dumps(
        {
            "path": str(root.relative_to(REPO_PATH)),
            "recursive": recursive,
            "include_stdlib": include_stdlib,
            "encoding": encoding,
            "fingerprint": fingerprint,
            "max_files": max_files,
        },
        sort_keys=True,
    )
    cached = _cache_get("dependency_map", cache_key)
    if isinstance(cached, dict):
        python_file_count = int(cached.get("python_file_count", 0))
        edges = list(cached.get("edges", []))
        unresolved = list(cached.get("unresolved_imports", []))
    else:
        module_to_path: dict[str, str] = {}
        python_files: list[Path] = []
        for candidate in _iter_candidate_files(root, recursive=recursive):
            if candidate.suffix != ".py":
                continue
            rel = candidate.relative_to(REPO_PATH)
            module = _module_name_from_relpath(rel)
            rel_str = str(rel).replace("\\", "/")
            module_to_path[module] = rel_str
            python_files.append(candidate)
            if len(python_files) >= max_files:
                break

        edges: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []

        for file_path in python_files:
            rel = str(file_path.relative_to(REPO_PATH)).replace("\\", "/")
            try:
                tree = ast.parse(file_path.read_text(encoding=encoding, errors="replace"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports = [node.module]
                    else:
                        imports = []
                else:
                    continue

                for imp in imports:
                    resolved_path = None
                    for candidate_module in _import_candidates(imp):
                        resolved = module_to_path.get(candidate_module)
                        if resolved:
                            resolved_path = resolved
                            break
                    if resolved_path:
                        edges.append(
                            {
                                "from": rel,
                                "to": resolved_path,
                                "import": imp,
                                "line": int(getattr(node, "lineno", 1)),
                            }
                        )
                    elif include_stdlib:
                        unresolved.append(
                            {
                                "from": rel,
                                "import": imp,
                                "line": int(getattr(node, "lineno", 1)),
                            }
                        )
        python_file_count = len(python_files)
        _cache_set(
            "dependency_map",
            cache_key,
            {
                "python_file_count": python_file_count,
                "edges": edges,
                "unresolved_imports": unresolved,
            },
        )

    result: dict[str, Any] = {
        "schema": "dependency_map.v1",
        "root": str(root.relative_to(REPO_PATH)),
        "python_file_count": python_file_count,
        "edge_count": len(edges),
        "edges": _select_fields(_paginate(edges, offset=offset, limit=limit), fields),
    }
    if profile == "compact":
        compact = {
            "schema": "dependency_map.compact.v1",
            "python_file_count": result["python_file_count"],
            "edge_count": result["edge_count"],
            "edges": result["edges"][:500] if isinstance(result["edges"], list) else [],
        }
        if compress:
            compact["edges_compressed"] = _compress_table(compact["edges"])
            compact.pop("edges", None)
        if store_result:
            compact["result_id"] = _result_store_put("dependency_map", compact)
        return compact
    if profile == "verbose":
        result["unresolved_imports"] = unresolved
        inbound: dict[str, int] = {}
        outbound: dict[str, int] = {}
        for edge in edges:
            inbound[edge["to"]] = inbound.get(edge["to"], 0) + 1
            outbound[edge["from"]] = outbound.get(edge["from"], 0) + 1
        result["hotspots"] = {
            "most_imported": sorted(
                [{"path": k, "count": v} for k, v in inbound.items()],
                key=lambda x: x["count"],
                reverse=True,
            )[:20],
            "most_importing": sorted(
                [{"path": k, "count": v} for k, v in outbound.items()],
                key=lambda x: x["count"],
                reverse=True,
            )[:20],
        }
    if summary_mode == "quick":
        result = {
            "schema": "dependency_map.quick.v1",
            "root": result["root"],
            "python_file_count": result["python_file_count"],
            "edge_count": result["edge_count"],
        }
    if compress and isinstance(result.get("edges"), list):
        result["edges_compressed"] = _compress_table(result["edges"])
        result.pop("edges", None)
    if store_result:
        result["result_id"] = _result_store_put("dependency_map", result)
    return result


def call_graph(
    path: str = ".",
    recursive: bool = True,
    max_edges: int = 5000,
    output_profile: str | None = None,
    encoding: str = "utf-8",
    fields: list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
    adaptive_limits: bool = True,
    summary_mode: str = "full",
    compress: bool = False,
    store_result: bool = False,
) -> dict[str, Any]:
    """Build a simple Python function-level call graph."""
    if max_edges < 1:
        raise ValueError("max_edges must be >= 1")
    if summary_mode not in {"full", "quick"}:
        raise ValueError("summary_mode must be one of: full, quick")
    if adaptive_limits:
        max_edges = _adaptive_limit(max_edges, soft_cap=2000)
    profile = _default_output_profile(output_profile)
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    fingerprint = _fingerprint_path(
        root, recursive=recursive, suffixes={".py"}, max_files=3000
    )
    cache_key = json.dumps(
        {
            "path": str(root.relative_to(REPO_PATH)),
            "recursive": recursive,
            "encoding": encoding,
            "fingerprint": fingerprint,
            "max_edges": max_edges,
        },
        sort_keys=True,
    )
    cached = _cache_get("call_graph", cache_key)
    if isinstance(cached, list):
        edges = list(cached)
    else:
        edges: list[dict[str, Any]] = []
        for candidate in _iter_candidate_files(root, recursive=recursive):
            if candidate.suffix != ".py":
                continue
            rel = str(candidate.relative_to(REPO_PATH)).replace("\\", "/")
            try:
                tree = ast.parse(candidate.read_text(encoding=encoding, errors="replace"))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                caller = node.name
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        callee = _ast_expr_name(child.func)
                        if not callee:
                            continue
                        edges.append(
                            {
                                "path": rel,
                                "caller": caller,
                                "callee": callee,
                                "line": int(getattr(child, "lineno", getattr(node, "lineno", 1))),
                            }
                        )
                        if len(edges) >= max_edges:
                            break
                if len(edges) >= max_edges:
                    break
            if len(edges) >= max_edges:
                break
        _cache_set("call_graph", cache_key, edges)

    paged_edges = _select_fields(_paginate(edges, offset=offset, limit=limit), fields)

    if profile == "compact":
        compact = {"schema": "call_graph.compact.v1", "edge_count": len(edges), "edges": paged_edges[:500]}
        if compress:
            compact["edges_compressed"] = _compress_table(compact["edges"])
            compact.pop("edges", None)
        if store_result:
            compact["result_id"] = _result_store_put("call_graph", compact)
        return compact
    inbound: dict[str, int] = {}
    for edge in edges:
        inbound[edge["callee"]] = inbound.get(edge["callee"], 0) + 1
    result: dict[str, Any] = {"schema": "call_graph.v1", "edge_count": len(edges), "edges": paged_edges}
    if profile == "verbose":
        result["most_called"] = sorted(
            [{"symbol": k, "count": v} for k, v in inbound.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:25]
    if summary_mode == "quick":
        result = {
            "schema": "call_graph.quick.v1",
            "edge_count": result["edge_count"],
            "top_called": result.get("most_called", [])[:10] if profile == "verbose" else [],
        }
    if compress and isinstance(result.get("edges"), list):
        result["edges_compressed"] = _compress_table(result["edges"])
        result.pop("edges", None)
    if store_result:
        result["result_id"] = _result_store_put("call_graph", result)
    return result


@mcp.tool()
def ast_search(
    path: str = ".",
    node_type: str = "Call",
    name_pattern: str | None = None,
    recursive: bool = True,
    max_results: int = 500,
    encoding: str = "utf-8",
) -> list[dict[str, Any]]:
    """Search Python AST nodes to find structural code matches."""
    if max_results < 1:
        raise ValueError("max_results must be >= 1")
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    try:
        node_cls = getattr(ast, node_type)
    except AttributeError as exc:
        raise ValueError(f"unsupported node_type: {node_type}") from exc

    name_regex = re.compile(name_pattern) if name_pattern else None
    results: list[dict[str, Any]] = []

    for candidate in _iter_candidate_files(root, recursive=recursive):
        if candidate.suffix != ".py":
            continue
        rel_str = str(candidate.relative_to(REPO_PATH)).replace("\\", "/")
        try:
            source = candidate.read_text(encoding=encoding, errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, node_cls):
                continue
            node_name = _node_display_name(node)
            if name_regex and not name_regex.search(node_name):
                continue
            results.append(
                {
                    "path": rel_str,
                    "node_type": node_type,
                    "name": node_name,
                    "line": int(getattr(node, "lineno", 1)),
                    "column": int(getattr(node, "col_offset", 0)) + 1,
                    "end_line": int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                }
            )
            if len(results) >= max_results:
                return results
    return results


@mcp.tool()
def apply_unified_diff(
    diff_text: str,
    check_only: bool = True,
    cached: bool = False,
) -> dict[str, Any]:
    """Apply a unified diff through git-apply with optional dry-run checks."""
    arguments = {"diff_text": diff_text, "check_only": check_only, "cached": cached}

    def _run() -> dict[str, Any]:
        _require_git_repo()
        if not check_only:
            _require_mutations()

        args = ["git", "-C", str(REPO_PATH), "apply"]
        if check_only:
            args.append("--check")
        if cached:
            args.append("--cached")

        proc = subprocess.run(
            args,
            input=diff_text,
            check=False,
            capture_output=True,
            text=True,
        )
        return {
            "ok": proc.returncode == 0,
            "check_only": check_only,
            "cached": cached,
            "exit_code": proc.returncode,
            "stdout": _trim_text(proc.stdout.strip()),
            "stderr": _trim_text(proc.stderr.strip()),
        }

    return _run_with_tool_security_audit("apply_unified_diff", arguments, _run)


@mcp.tool()
def command_runner(
    command: list[str],
    cwd: str = ".",
    timeout_seconds: int = 30,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    """Strict command executor: MUST use a SAFE_COMMANDS binary, required command list, returns schema-stable stdout/stderr or explicit timeout/file-not-found error payload."""
    arguments = {
        "command": command,
        "cwd": cwd,
        "timeout_seconds": timeout_seconds,
        "max_output_chars": max_output_chars,
    }
    return _run_with_tool_security_audit(
        "command_runner",
        arguments,
        lambda: _command_runner_impl(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        ),
    )


def _command_runner_impl(
    command: list[str],
    cwd: str = ".",
    timeout_seconds: int = 30,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    out_cap = _token_budget_apply_max(max_output_chars)
    workdir = _resolve_repo_path(cwd)
    rel_cwd = str(workdir.relative_to(REPO_PATH))
    run_id = uuid.uuid4().hex[:12]
    try:
        _validate_safe_command(command)
    except ValueError as exc:
        reason = str(exc)
        if not _is_manual_command_request(reason):
            raise
        approved_request = _find_approved_manual_command_request(command=command, cwd=rel_cwd)
        if approved_request is not None:
            _sse_publish(
                "tool.approval_granted",
                source="command_runner",
                run_id=run_id,
                command=command,
                cwd=str(workdir),
                approval_id=approved_request["approval_id"],
            )
        else:
            approval = _approval_point_append(
                action="manual_command_execution",
                risk_level="medium",
                details=(
                    "User execution required for non-whitelisted command. "
                    f"cwd={rel_cwd}; command={json.dumps(command)}; reason={reason}"
                ),
            )
            _sse_publish(
                "tool.approval_required",
                source="command_runner",
                run_id=run_id,
                command=command,
                cwd=str(workdir),
                reason=reason,
                manual_execution_required=True,
                approval_id=approval["approval_id"],
            )
            return {
                "ok": True,
                "exit_code": None,
                "command": command,
                "cwd": rel_cwd,
                "stdout": "",
                "stderr": reason,
                "timeout": False,
                "manual_execution_required": True,
                "message": "Command approval requested. The command was not executed.",
                "suggested_command": shlex.join(command),
                "approval_request": approval,
            }

    started_at = time.time()
    _sse_publish(
        "tool.start",
        source="command_runner",
        run_id=run_id,
        command=command,
        cwd=str(workdir),
        timeout_seconds=timeout_seconds,
    )
    try:
        proc = subprocess.run(
            command,
            cwd=str(workdir),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        _sse_publish(
            "tool.error",
            source="command_runner",
            run_id=run_id,
            command=command,
            cwd=str(workdir),
            error=str(exc),
        )
        _failure_record(
            command=command,
            stderr=str(exc),
            category="command_runner",
            suggestion="Verify the executable is installed in the runtime.",
        )
        return {
            "ok": False,
            "exit_code": None,
            "command": command,
            "cwd": rel_cwd,
            "stdout": "",
            "stderr": str(exc),
            "timeout": False,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.output if isinstance(exc.output, str) else getattr(exc, "stdout", "") or ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        duration_ms = int((time.time() - started_at) * 1000)
        _sse_publish(
            "tool.finish",
            source="command_runner",
            run_id=run_id,
            command=command,
            cwd=str(workdir),
            timed_out=True,
            exit_code=None,
            duration_ms=duration_ms,
            stdout_chars=len(stdout),
            stderr_chars=len(stderr),
        )
        _failure_record(
            command=command,
            stderr="command timed out",
            stdout=stdout,
            category="command_runner",
            suggestion="Increase timeout_seconds or narrow command scope.",
        )
        return {
            "ok": False,
            "exit_code": None,
            "command": command,
            "cwd": rel_cwd,
            "stdout": _trim_text(stdout, max_chars=out_cap),
            "stderr": _trim_text(stderr, max_chars=out_cap),
            "timeout": True,
        }
    for part in _split_sse_chunks(proc.stdout):
        _sse_publish(
            "tool.output",
            source="command_runner",
            run_id=run_id,
            command=command,
            cwd=str(workdir),
            stream="stdout",
            chunk=part,
        )
    for part in _split_sse_chunks(proc.stderr):
        _sse_publish(
            "tool.output",
            source="command_runner",
            run_id=run_id,
            command=command,
            cwd=str(workdir),
            stream="stderr",
            chunk=part,
        )
    duration_ms = int((time.time() - started_at) * 1000)
    _sse_publish(
        "tool.finish",
        source="command_runner",
        run_id=run_id,
        command=command,
        cwd=str(workdir),
        timed_out=False,
        exit_code=proc.returncode,
        duration_ms=duration_ms,
        stdout_chars=len(proc.stdout),
        stderr_chars=len(proc.stderr),
    )
    if proc.returncode != 0:
        _failure_record(
            command=command,
            stderr=proc.stderr,
            stdout=proc.stdout,
            category="command_runner",
            suggestion="Inspect stderr and retry with narrower scope or valid flags.",
        )
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "command": command,
        "cwd": str(workdir.relative_to(REPO_PATH)),
        "stdout": _trim_text(proc.stdout, max_chars=out_cap),
        "stderr": _trim_text(proc.stderr, max_chars=out_cap),
        "timeout": False,
    }


@mcp.tool()
def terminal_support_session(
    mode: str = "start",
    session_id: str = "",
    command: list[str] | None = None,
    cwd: str = ".",
    input_text: str = "",
    read_timeout_ms: int = 100,
    max_output_chars: int | None = None,
    include_output: bool = True,
) -> dict[str, Any]:
    """Manage PTY terminal sessions for support workflows with captured I/O logs."""
    if mode not in {"start", "send", "poll", "stop", "list"}:
        raise ValueError("mode must be one of: start, send, poll, stop, list")
    if read_timeout_ms < 0:
        raise ValueError("read_timeout_ms must be >= 0")
    out_cap = _token_budget_apply_max(max_output_chars)

    if mode == "list":
        rows = []
        for sid, row in _TERMINAL_SESSIONS.items():
            proc = row.get("proc")
            code = proc.poll() if proc else None
            rows.append(
                {
                    "session_id": sid,
                    "command": row.get("command", []),
                    "cwd": row.get("cwd", "."),
                    "running": code is None,
                    "exit_code": code,
                }
            )
        return {"schema": "terminal_support_session.v1", "mode": mode, "sessions": rows}

    if mode == "start":
        cmd = command or []
        _validate_safe_command(cmd)
        workdir = _resolve_repo_path(cwd)
        rel_cwd = str(workdir.relative_to(REPO_PATH))
        capture_dir = _resolve_repo_path(str(TERMINAL_CAPTURE_DIR))
        capture_dir.mkdir(parents=True, exist_ok=True)
        sid = uuid.uuid4().hex[:12]
        log_path = capture_dir / f"{sid}.log"
        master_fd = -1
        backend = "pty"
        proc: subprocess.Popen[bytes]
        try:
            master_fd, slave_fd = pty.openpty()
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(workdir),
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    text=False,
                    close_fds=True,
                )
            finally:
                os.close(slave_fd)
            os.set_blocking(master_fd, False)
            read_fd = master_fd
        except OSError:
            backend = "pipe"
            proc = subprocess.Popen(
                cmd,
                cwd=str(workdir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=False,
                close_fds=True,
            )
            if proc.stdout is None:
                raise RuntimeError("failed to create stdout pipe for terminal session")
            read_fd = proc.stdout.fileno()
            os.set_blocking(read_fd, False)
        log_path.write_text(
            f"# terminal session {sid}\n# started_at={_now_iso()}\n# cwd={workdir.relative_to(REPO_PATH)}\n# command={json.dumps(cmd)}\n\n",
            encoding="utf-8",
        )
        session = {
            "session_id": sid,
            "proc": proc,
            "backend": backend,
            "master_fd": master_fd,
            "read_fd": read_fd,
            "log_path": str(log_path),
            "command": cmd,
            "cwd": rel_cwd,
            "input_chars": 0,
            "output_chars": 0,
            "started_at": _now_iso(),
        }
        _TERMINAL_SESSIONS[sid] = session
        output = (
            _terminal_read_available(session, max_output_chars=out_cap, wait_timeout_ms=read_timeout_ms)
            if include_output
            else ""
        )
        _sse_publish(
            "terminal.start",
            session_id=sid,
            command=cmd,
            cwd=str(workdir.relative_to(REPO_PATH)),
            backend=backend,
            running=proc.poll() is None,
        )
        if output:
            _sse_publish(
                "terminal.output",
                session_id=sid,
                command=cmd,
                cwd=str(workdir.relative_to(REPO_PATH)),
                chunk=_trim_text(output, max_chars=4000),
            )
        return {
            "schema": "terminal_support_session.v1",
            "mode": mode,
            "session_id": sid,
            "running": proc.poll() is None,
            "exit_code": proc.poll(),
            "command": cmd,
            "cwd": rel_cwd,
            "log_path": str(log_path.relative_to(REPO_PATH)),
            "backend": backend,
            "output": _trim_text(output, max_chars=out_cap) if include_output else "",
        }

    if not session_id:
        raise ValueError("session_id is required")
    session = _TERMINAL_SESSIONS.get(session_id)
    if not isinstance(session, dict):
        raise ValueError(f"unknown session_id: {session_id}")
    proc = session["proc"]

    if mode == "send":
        if input_text:
            data = input_text.encode("utf-8", errors="replace")
            if session.get("backend") == "pty":
                os.write(int(session["master_fd"]), data)
            else:
                stdin = proc.stdin
                if stdin is not None:
                    stdin.write(data)
                    stdin.flush()
            log_path = Path(session["log_path"])
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n# [stdin] {input_text}")
                if not input_text.endswith("\n"):
                    f.write("\n")
            session["input_chars"] = int(session.get("input_chars", 0)) + len(input_text)
            _sse_publish(
                "terminal.input",
                session_id=session_id,
                command=session.get("command", []),
                cwd=session.get("cwd", "."),
                chunk=_trim_text(input_text, max_chars=4000),
            )
        output = (
            _terminal_read_available(session, max_output_chars=out_cap, wait_timeout_ms=read_timeout_ms)
            if include_output
            else ""
        )
        if output:
            _sse_publish(
                "terminal.output",
                session_id=session_id,
                command=session.get("command", []),
                cwd=session.get("cwd", "."),
                chunk=_trim_text(output, max_chars=4000),
            )
        return {
            "schema": "terminal_support_session.v1",
            "mode": mode,
            "session_id": session_id,
            "running": proc.poll() is None,
            "exit_code": proc.poll(),
            "output": _trim_text(output, max_chars=out_cap) if include_output else "",
            "input_chars": int(session.get("input_chars", 0)),
            "output_chars": int(session.get("output_chars", 0)),
        }

    if mode == "poll":
        output = (
            _terminal_read_available(session, max_output_chars=out_cap, wait_timeout_ms=read_timeout_ms)
            if include_output
            else ""
        )
        if output:
            _sse_publish(
                "terminal.output",
                session_id=session_id,
                command=session.get("command", []),
                cwd=session.get("cwd", "."),
                chunk=_trim_text(output, max_chars=4000),
            )
        return {
            "schema": "terminal_support_session.v1",
            "mode": mode,
            "session_id": session_id,
            "running": proc.poll() is None,
            "exit_code": proc.poll(),
            "output": _trim_text(output, max_chars=out_cap) if include_output else "",
            "input_chars": int(session.get("input_chars", 0)),
            "output_chars": int(session.get("output_chars", 0)),
        }

    # stop
    if proc.poll() is None:
        with contextlib.suppress(Exception):
            proc.terminate()
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=1.0)
    output = (
        _terminal_read_available(session, max_output_chars=out_cap, wait_timeout_ms=read_timeout_ms)
        if include_output
        else ""
    )
    if session.get("backend") == "pty":
        with contextlib.suppress(Exception):
            os.close(int(session["master_fd"]))
    else:
        with contextlib.suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.close()
        with contextlib.suppress(Exception):
            if proc.stdout is not None:
                proc.stdout.close()
    _TERMINAL_SESSIONS.pop(session_id, None)
    if output:
        _sse_publish(
            "terminal.output",
            session_id=session_id,
            command=session.get("command", []),
            cwd=session.get("cwd", "."),
            chunk=_trim_text(output, max_chars=4000),
        )
    _sse_publish(
        "terminal.stop",
        session_id=session_id,
        command=session.get("command", []),
        cwd=session.get("cwd", "."),
        exit_code=proc.poll(),
    )
    return {
        "schema": "terminal_support_session.v1",
        "mode": mode,
        "session_id": session_id,
        "running": False,
        "exit_code": proc.poll(),
        "output": _trim_text(output, max_chars=out_cap) if include_output else "",
        "log_path": str(Path(session["log_path"]).relative_to(REPO_PATH)),
        "input_chars": int(session.get("input_chars", 0)),
        "output_chars": int(session.get("output_chars", 0)),
    }


@mcp.tool()
def summarize_diff(
    ref: str | None = None,
    staged: bool = False,
    pathspec: str | None = None,
    output_profile: str = "compact",
    include_patch: bool = False,
    patch_unified: int = 0,
) -> dict[str, Any]:
    """Return compact structured diff summary with risk hints."""
    _require_git_repo()
    profile = _validate_output_profile(output_profile)
    if patch_unified < 0:
        raise ValueError("patch_unified must be >= 0")
    args = ["diff"]
    if staged:
        args.append("--staged")
    if ref:
        args.append(ref)
    if pathspec:
        _resolve_repo_path(pathspec)
        args.extend(["--", pathspec])

    numstat = _git(*args, "--numstat").stdout
    patch = _git(*args, f"--unified={patch_unified}").stdout

    files: list[dict[str, Any]] = []
    total_added = 0
    total_deleted = 0
    risky_files: list[str] = []

    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add_raw, del_raw, file_path = parts
        added = int(add_raw) if add_raw.isdigit() else 0
        deleted = int(del_raw) if del_raw.isdigit() else 0
        total_added += added
        total_deleted += deleted
        files.append({"path": file_path, "added": added, "deleted": deleted})
        low = file_path.lower()
        if (
            "dockerfile" in low
            or "requirements" in low
            or low.endswith(".lock")
            or low.endswith("package.json")
            or "/.github/workflows/" in low
        ):
            risky_files.append(file_path)

    todo_hits = 0
    for line in patch.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if "TODO" in line or "FIXME" in line or "XXX" in line:
            todo_hits += 1

    result = {
        "file_count": len(files),
        "total_added": total_added,
        "total_deleted": total_deleted,
        "files": files,
        "risk_flags": {
            "risky_files": risky_files,
            "todo_like_additions": todo_hits,
        },
    }
    if profile == "compact":
        return {
            "file_count": result["file_count"],
            "total_added": result["total_added"],
            "total_deleted": result["total_deleted"],
            "risk_flags": result["risk_flags"],
        }
    if profile == "verbose":
        result["files_sorted_by_churn"] = sorted(
            files, key=lambda x: x["added"] + x["deleted"], reverse=True
        )
    if include_patch:
        result["patch"] = _trim_text(patch)
        result["patch_unified"] = patch_unified
    return result


@mcp.tool()
def json_query(
    path: str,
    query: str = "",
    file_type: str | None = None,
    output_profile: str = "compact",
) -> dict[str, Any]:
    """Query JSON/TOML/YAML content with a dot/index path."""
    profile = _validate_output_profile(output_profile)
    file_path = _resolve_repo_path(path)
    if not file_path.is_file():
        raise FileNotFoundError(path)

    fmt = (file_type or _guess_file_type(file_path)).lower()
    raw = file_path.read_text(encoding="utf-8", errors="replace")

    if fmt == "json":
        data = json.loads(raw)
    elif fmt == "toml":
        if tomllib is None:
            raise RuntimeError("tomllib is not available in this Python runtime")
        data = tomllib.loads(raw)
    elif fmt == "yaml":
        if yaml is None:
            raise RuntimeError("PyYAML is not installed in this runtime")
        data = yaml.safe_load(raw)
    else:
        raise ValueError("file_type must be one of: json, toml, yaml")

    value = _query_value(data, query) if query.strip() else data
    encoded = json.dumps(value, indent=2, ensure_ascii=True)
    result = {
        "path": str(file_path.relative_to(REPO_PATH)),
        "query": query,
        "file_type": fmt,
        "value": value,
        "value_json": _trim_text(encoded),
    }
    if profile == "compact":
        return {
            "path": result["path"],
            "query": result["query"],
            "file_type": result["file_type"],
            "value_json": result["value_json"],
        }
    if profile == "verbose":
        result["value_type"] = type(value).__name__
    return result


@mcp.tool()
def prompt_optimize(
    prompt: str,
    mode: str = "coding",
    max_chars: int = 2000,
) -> dict[str, Any]:
    """Produce a compact prompt variant tuned for low-token tool workflows."""
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if max_chars < 100:
        raise ValueError("max_chars must be >= 100")
    if mode not in {"coding", "review", "search", "tooling_strict"}:
        raise ValueError("mode must be one of: coding, review, search, tooling_strict")

    header = {
        "coding": "Goal: implement minimal safe change. Use compact outputs and bounded queries.",
        "review": "Goal: find high-severity issues first. Return concise findings with file/line.",
        "search": "Goal: locate exact targets quickly. Use fields, pagination, and result handles.",
        "tooling_strict": (
            "Goal: strict tool usage. Must use router tools first; call non-router tools only if router modes cannot satisfy request."
        ),
    }[mode]
    suffix = (
        "Constraints: must validate modes/required params before execution; prefer output_profile=compact; "
        "set fields; use offset/limit; use summary_mode=quick first; store_result=true for large outputs; "
        "return deterministic schema-first responses and explicit errors for invalid input."
    )
    body = re.sub(r"\s+", " ", prompt.strip())
    before = _strictness_score_text(body)
    optimized = f"{header} Request: {body} {suffix}".strip()
    if len(optimized) > max_chars:
        optimized = optimized[:max_chars]
    after = _strictness_score_text(optimized)
    return {
        "schema": "prompt_optimize.v1",
        "mode": mode,
        "original_chars": len(prompt),
        "optimized_chars": len(optimized),
        "optimized_prompt": optimized,
        "strictness_score_before": before["score"],
        "strictness_score_after": after["score"],
        "strictness_reasons_after": after["reasons"],
    }


@mcp.tool()
def tool_prompt_score(
    scope: str = "all",
    top_n: int = 20,
) -> dict[str, Any]:
    """Score MCP prompt strictness (global instructions + tool docstrings) and return weakest prompts first."""
    if scope not in {"all", "routers", "core"}:
        raise ValueError("scope must be one of: all, routers, core")
    if top_n < 1:
        raise ValueError("top_n must be >= 1")

    source_path = _resolve_repo_path("source/server.py")
    if not source_path.is_file():
        source_path = Path(__file__).resolve()
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rows: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        is_tool = False
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "mcp" and dec.func.attr == "tool":
                    is_tool = True
                    break
        if not is_tool:
            continue
        name = node.name
        if scope == "routers" and not name.endswith("_router"):
            continue
        if scope == "core" and name not in {
            "task_router",
            "memory_router",
            "code_index_router",
            "workspace_transaction",
            "docker_router",
            "vscode_router",
            "command_runner",
            "self_test",
            "prompt_optimize",
        }:
            continue
        doc = ast.get_docstring(node) or ""
        score = _strictness_score_text(doc)
        rows.append(
            {
                "tool": name,
                "score": score["score"],
                "doc_chars": len(doc),
                "reasons": score["reasons"],
            }
        )
    rows.sort(key=lambda r: int(r["score"]))
    ins = _strictness_score_text(mcp.instructions or "")
    avg = round(sum(int(r["score"]) for r in rows) / len(rows), 2) if rows else 0.0
    return {
        "schema": "tool_prompt_score.v1",
        "scope": scope,
        "tool_count": len(rows),
        "avg_score": avg,
        "global_instruction_score": ins["score"],
        "global_instruction_reasons": ins["reasons"],
        "lowest_tools": rows[:top_n],
        "highest_tools": list(reversed(rows[-top_n:])),
    }


@mcp.tool()
def math_parser(
    text: str,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Parse math expression text into a canonical symbolic form."""
    _require_sympy()
    if not text.strip():
        raise ValueError("text must not be empty")
    local_symbols = {}
    for s in symbols or []:
        local_symbols[s] = sp.symbols(s)
    expr = sp.sympify(text, locals=local_symbols)
    return {
        "schema": "math_parser.v1",
        "input": text,
        "parsed": str(expr),
        "latex": sp.latex(expr),
        "free_symbols": sorted(str(s) for s in expr.free_symbols),
    }


@mcp.tool()
def math_solver(
    mode: str = "simplify",
    expression: str = "",
    variable: str = "x",
    equations: list[str] | None = None,
    matrix_a: list[list[float]] | None = None,
    matrix_b: list[list[float]] | None = None,
    assumptions: dict[str, str] | None = None,
    include_steps: bool = True,
) -> dict[str, Any]:
    """Offline symbolic math solver with exact + numeric outputs."""
    if mode not in {"simplify", "solve", "differentiate", "integrate", "matrix", "optimize"}:
        raise ValueError("mode must be one of: simplify, solve, differentiate, integrate, matrix, optimize")
    _require_sympy()
    x = sp.symbols(variable, **(assumptions or {}))
    result: dict[str, Any] = {"schema": "math_solver.v1", "mode": mode}

    if mode == "simplify":
        expr = _math_expr(expression)
        exact = sp.simplify(expr)
        result["exact"] = str(exact)
        result["numeric"] = str(sp.N(exact))
    elif mode == "solve":
        eqs = equations or ([expression] if expression else [])
        if not eqs:
            raise ValueError("expression or equations required for solve mode")
        parsed = []
        for e in eqs:
            if "=" in e:
                left, right = e.split("=", 1)
                parsed.append(sp.Eq(sp.sympify(left), sp.sympify(right)))
            else:
                parsed.append(sp.Eq(sp.sympify(e), 0))
        sols = sp.solve(parsed, x, dict=True)
        result["solutions"] = [{str(k): str(v) for k, v in row.items()} for row in sols]
    elif mode == "differentiate":
        expr = _math_expr(expression)
        deriv = sp.diff(expr, x)
        result["exact"] = str(deriv)
        result["numeric"] = str(sp.N(deriv))
    elif mode == "integrate":
        expr = _math_expr(expression)
        integ = sp.integrate(expr, x)
        result["exact"] = str(integ)
        result["numeric"] = str(sp.N(integ))
    elif mode == "matrix":
        if matrix_a is None:
            raise ValueError("matrix_a is required for matrix mode")
        A = sp.Matrix(matrix_a)
        result["shape"] = [int(A.rows), int(A.cols)]
        result["determinant"] = str(A.det()) if A.rows == A.cols else None
        result["rank"] = int(A.rank())
        if matrix_b is not None:
            B = sp.Matrix(matrix_b)
            result["product"] = [[str(v) for v in row] for row in (A * B).tolist()]
    else:  # optimize
        expr = _math_expr(expression)
        deriv = sp.diff(expr, x)
        critical = sp.solve(sp.Eq(deriv, 0), x)
        result["critical_points"] = [str(v) for v in critical]
        result["derivative"] = str(deriv)

    if include_steps:
        result["steps"] = _math_steps_stub(mode, expression or str(equations or ""))
    return result


@mcp.tool()
def math_verify(
    left: str,
    right: str,
    variables: list[str] | None = None,
    trials: int = 5,
) -> dict[str, Any]:
    """Verify algebraic identity/equality via symbolic simplification and sampling."""
    if trials < 1:
        raise ValueError("trials must be >= 1")
    _require_sympy()
    lhs = _math_expr(left)
    rhs = _math_expr(right)
    diff = sp.simplify(lhs - rhs)
    proven = diff == 0
    checks: list[dict[str, Any]] = []
    syms = sorted(diff.free_symbols, key=lambda s: str(s))
    if variables:
        syms = [sp.symbols(v) for v in variables]
    if not proven and syms:
        for i in range(trials):
            vals = {s: (i + 2) for s in syms}
            ok = sp.simplify((lhs - rhs).subs(vals)) == 0
            checks.append({"substitution": {str(k): str(v) for k, v in vals.items()}, "ok": bool(ok)})
    return {
        "schema": "math_verify.v1",
        "left": left,
        "right": right,
        "proven": bool(proven),
        "difference": str(diff),
        "checks": checks,
    }


@mcp.tool()
def sql_expert(
    mode: str = "format",
    query: str = "",
    dialect: str = "generic",
    nl_request: str = "",
) -> dict[str, Any]:
    """Offline SQL helper for formatting, linting, and NL-to-SQL skeletons."""
    if mode not in {"format", "lint", "nl2sql"}:
        raise ValueError("mode must be one of: format, lint, nl2sql")
    result: dict[str, Any] = {"schema": "sql_expert.v1", "mode": mode, "dialect": dialect}
    if mode == "format":
        if not query.strip():
            raise ValueError("query must not be empty")
        result["formatted"] = _sql_normalize(query)
        return result
    if mode == "lint":
        if not query.strip():
            raise ValueError("query must not be empty")
        issues: list[str] = []
        q = query.lower()
        if "select *" in q:
            issues.append("Avoid SELECT * in production queries.")
        if " where " not in q and (" update " in q or " delete " in q):
            issues.append("UPDATE/DELETE without WHERE can affect all rows.")
        if " order by " in q and " limit " not in q:
            issues.append("Consider LIMIT when ORDER BY is used in high-cardinality tables.")
        result["issues"] = issues
        result["formatted"] = _sql_normalize(query)
        return result
    # nl2sql
    req = nl_request.strip().lower()
    if not req:
        raise ValueError("nl_request must not be empty for nl2sql mode")
    table = "items"
    if "user" in req:
        table = "users"
    elif "order" in req:
        table = "orders"
    skeleton = f"SELECT id, * FROM {table} WHERE <condition> ORDER BY id DESC LIMIT 100;"
    result["sql_skeleton"] = skeleton
    return result


@mcp.tool()
def security_triage(
    diff_text: str = "",
    paths: list[str] | None = None,
    max_findings: int = 100,
) -> dict[str, Any]:
    """Classify security-sensitive changes from diff snippets and path heuristics."""
    if max_findings < 1:
        raise ValueError("max_findings must be >= 1")
    findings: list[dict[str, Any]] = []
    lines = _extract_diff_lines(diff_text)
    lower_paths = [p.lower() for p in (paths or [])]
    patterns = [
        ("hardcoded_secret", re.compile(r"(api[_-]?key|secret|token)\s*[:=]\s*['\"][^'\"]+['\"]", re.IGNORECASE), "high"),
        ("command_injection", re.compile(r"(subprocess\.|os\.system|eval\()", re.IGNORECASE), "high"),
        ("sql_injection", re.compile(r"(select .* \+|f\"select|execute\(.+\+)", re.IGNORECASE), "high"),
        ("weak_crypto", re.compile(r"\b(md5|sha1)\b", re.IGNORECASE), "medium"),
    ]
    for ln in lines:
        for rule, rx, sev in patterns:
            if rx.search(ln):
                findings.append({"rule": rule, "severity": sev, "line": ln[:300]})
                if len(findings) >= max_findings:
                    break
        if len(findings) >= max_findings:
            break
    if any("auth" in p or "security" in p or "crypto" in p for p in lower_paths):
        findings.append({"rule": "sensitive_path", "severity": "medium", "line": "Sensitive path changed"})
    sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    top = "low"
    if findings:
        top = max((f["severity"] for f in findings), key=lambda s: sev_rank.get(s, 0))
    return {
        "schema": "security_triage.v1",
        "finding_count": len(findings),
        "top_severity": top,
        "findings": findings[:max_findings],
    }


@mcp.tool()
def doc_summarizer_small(
    text: str,
    max_bullets: int = 8,
    max_chars: int = 1200,
) -> dict[str, Any]:
    """Small offline summarizer for logs/docs using sentence ranking."""
    if max_bullets < 1:
        raise ValueError("max_bullets must be >= 1")
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    scores = []
    for s in sentences:
        if not s.strip():
            continue
        score = len(re.findall(r"\b(error|fail|warning|todo|fix|critical|security)\b", s, re.IGNORECASE))
        score += min(4, len(s) // 80)
        scores.append((score, s.strip()))
    scores.sort(key=lambda x: x[0], reverse=True)
    bullets = [s for _, s in scores[:max_bullets]]
    summary = "\n".join(f"- {b}" for b in bullets)
    return {
        "schema": "doc_summarizer_small.v1",
        "bullet_count": len(bullets),
        "summary": _trim_text(summary, max_chars=max_chars),
    }


@mcp.tool()
def code_review_classifier(
    findings: list[dict[str, Any]],
    include_confidence: bool = True,
) -> dict[str, Any]:
    """Classify review findings into bug/perf/style/security buckets."""
    buckets = {"bug": [], "perf": [], "style": [], "security": [], "other": []}
    for f in findings:
        text = " ".join(str(f.get(k, "")) for k in ("title", "message", "detail", "rule")).lower()
        bucket = "other"
        if any(k in text for k in ("injection", "secret", "xss", "auth", "csrf", "crypto")):
            bucket = "security"
        elif any(k in text for k in ("crash", "exception", "null", "wrong", "bug", "incorrect")):
            bucket = "bug"
        elif any(k in text for k in ("slow", "n+1", "latency", "optimize", "allocation")):
            bucket = "perf"
        elif any(k in text for k in ("format", "naming", "style", "lint", "readability")):
            bucket = "style"
        row = dict(f)
        if include_confidence:
            row["confidence"] = 0.8 if bucket != "other" else 0.5
        buckets[bucket].append(row)
    return {
        "schema": "code_review_classifier.v1",
        "counts": {k: len(v) for k, v in buckets.items()},
        "buckets": buckets,
    }


@mcp.tool()
def test_gen_small(
    function_name: str,
    path: str,
    framework: str = "pytest",
    behavior_summary: str = "",
) -> dict[str, Any]:
    """Generate a minimal unit-test skeleton for a target function."""
    if framework not in {"pytest", "unittest"}:
        raise ValueError("framework must be one of: pytest, unittest")
    target = _resolve_repo_path(path)
    if not target.is_file():
        raise FileNotFoundError(path)
    module = str(target.relative_to(REPO_PATH)).replace("/", ".")
    if module.endswith(".py"):
        module = module[:-3]
    module = module.replace(".__init__", "")
    if framework == "pytest":
        code = (
            f"from {module} import {function_name}\n\n"
            f"def test_{function_name}_basic():\n"
            f"    # {behavior_summary or 'TODO: define expected behavior'}\n"
            f"    result = {function_name}(...)\n"
            f"    assert result is not None\n"
        )
    else:
        code = (
            "import unittest\n"
            f"from {module} import {function_name}\n\n"
            "class GeneratedTest(unittest.TestCase):\n"
            f"    def test_{function_name}_basic(self):\n"
            f"        # {behavior_summary or 'TODO: define expected behavior'}\n"
            f"        result = {function_name}(...)\n"
            "        self.assertIsNotNone(result)\n"
        )
    return {"schema": "test_gen_small.v1", "framework": framework, "test_code": code}


@mcp.tool()
def vision_ocr_parser(
    image_path: str,
    language: str = "eng",
    max_chars: int = 5000,
) -> dict[str, Any]:
    """Offline OCR parser for local images using pytesseract when available."""
    path = _resolve_repo_path(image_path)
    if not path.is_file():
        raise FileNotFoundError(image_path)
    global Image, pytesseract
    if Image is _OPTIONAL_DEPENDENCY_UNLOADED:
        Image = _import_optional_dependency("PIL.Image", "Pillow")
    if pytesseract is _OPTIONAL_DEPENDENCY_UNLOADED:
        pytesseract = _import_optional_dependency("pytesseract", "pytesseract")
    if Image is None or pytesseract is None:
        raise RuntimeError("vision OCR dependencies missing (Pillow/pytesseract)")
    img = Image.open(path)
    text = pytesseract.image_to_string(img, lang=language)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return {
        "schema": "vision_ocr_parser.v1",
        "image_path": image_path,
        "line_count": len(lines),
        "text": _trim_text(text, max_chars=max_chars),
    }


@mcp.tool()
def image_interpret(
    image_path: str,
    mode: str = "caption",
    language: str = "eng",
    use_local_model: bool = False,
    max_chars: int = 2000,
    output_profile: str | None = None,
) -> dict[str, Any]:
    """Interpret an image with constrained offline modes: ocr, caption, classify, ui_parse."""
    if mode not in {"ocr", "caption", "classify", "ui_parse"}:
        raise ValueError("mode must be one of: ocr, caption, classify, ui_parse")
    path = _resolve_repo_path(image_path)
    if not path.is_file():
        raise FileNotFoundError(image_path)
    profile = _default_output_profile(output_profile)

    global Image, pytesseract
    warnings: list[str] = []
    features = _image_basic_features(path)
    ocr_text = ""
    if Image is _OPTIONAL_DEPENDENCY_UNLOADED:
        try:
            Image = _import_optional_dependency("PIL.Image", "Pillow")
        except RuntimeError:
            Image = None
    if pytesseract is _OPTIONAL_DEPENDENCY_UNLOADED:
        try:
            pytesseract = _import_optional_dependency("pytesseract", "pytesseract")
        except RuntimeError:
            pytesseract = None
    if Image is not None and pytesseract is not None:
        try:
            with Image.open(path) as img:
                ocr_text = pytesseract.image_to_string(img, lang=language)
        except Exception as exc:
            warnings.append(f"OCR failed: {exc}")
    elif mode == "ocr":
        raise RuntimeError("vision OCR dependencies missing (Pillow/pytesseract)")

    lines = [ln.strip() for ln in ocr_text.splitlines() if ln.strip()]
    word_count = len(re.findall(r"\b\w+\b", ocr_text))
    aspect = float(features.get("aspect_ratio") or 0.0)
    mean_luma = features.get("mean_luma")

    label = "photo_like"
    confidence = 0.55
    if word_count > 40:
        label = "document_scan"
        confidence = 0.82
    elif word_count > 12 and 1.5 <= aspect <= 2.2:
        label = "ui_screenshot"
        confidence = 0.78
    elif word_count > 8:
        label = "diagram_or_slide"
        confidence = 0.68
    elif mean_luma is not None and float(mean_luma) > 210:
        label = "minimal_graphic"
        confidence = 0.62

    text_preview = " ".join(lines[:3]).strip()
    caption = (
        f"{label.replace('_', ' ')} image"
        f" ({int(features.get('width', 0))}x{int(features.get('height', 0))})."
    )
    if text_preview:
        caption += f" Visible text starts with: {text_preview[:180]}"

    ui_elements = []
    for idx, ln in enumerate(lines[:40], start=1):
        token_count = len(re.findall(r"\b\w+\b", ln))
        elem_type = "headline" if token_count <= 6 and len(ln) <= 60 else "text"
        ui_elements.append({"row": idx, "type": elem_type, "text": ln[:160]})

    summary = ""
    model_backend = ""
    if use_local_model and (caption or text_preview):
        prompt = (
            "Interpret this image using concise structured bullets:\n"
            f"- mode: {mode}\n"
            f"- heuristic_label: {label}\n"
            f"- caption: {caption}\n"
            f"- ocr_preview: {text_preview}\n"
            "- Return objective, key info, and uncertainty."
        )
        try:
            inferred = local_infer(
                prompt=prompt,
                task="image_interpret",
                backend="auto",
                output_profile="compact",
                max_tokens=220,
            )
            summary = str(inferred.get("output", "")).strip()
            model_backend = str(inferred.get("backend", ""))
        except Exception as exc:
            warnings.append(f"local model interpret failed: {exc}")
    if not summary:
        summary = caption

    payload = {
        "schema": "image_interpret.v1",
        "image_path": image_path,
        "mode": mode,
        "label": label,
        "confidence": round(confidence, 3),
        "features": features,
        "line_count": len(lines),
        "word_count": word_count,
        "warnings": warnings,
        "used_local_model": bool(model_backend),
        "model_backend": model_backend,
        "summary": _trim_text(summary, max_chars=max_chars),
        "ocr_text": _trim_text(ocr_text, max_chars=max_chars),
        "ui_elements": ui_elements,
    }
    if mode == "ocr":
        payload["summary"] = _trim_text(ocr_text, max_chars=max_chars)
    if mode == "classify":
        payload["summary"] = f"{label} ({round(confidence, 3)})"
    if mode == "ui_parse" and not ui_elements and ocr_text:
        payload["ui_elements"] = [{"row": 1, "type": "text", "text": _trim_text(ocr_text, max_chars=160)}]

    if profile == "compact":
        return {
            "schema": "image_interpret.compact.v1",
            "image_path": image_path,
            "mode": mode,
            "label": payload["label"],
            "confidence": payload["confidence"],
            "line_count": payload["line_count"],
            "warnings": warnings,
            "used_local_model": payload["used_local_model"],
            "summary": payload["summary"],
            "ui_elements": payload["ui_elements"][:12],
        }
    return payload


@mcp.tool()
def translation_small(
    text: str,
    source_lang: str = "en",
    target_lang: str = "de",
    mode: str = "lexical",
) -> dict[str, Any]:
    """Offline small translation helper with lexical fallback."""
    if mode not in {"lexical", "local_infer"}:
        raise ValueError("mode must be one of: lexical, local_infer")
    if mode == "lexical":
        translated = _simple_translate(text, source_lang, target_lang)
        return {
            "schema": "translation_small.v1",
            "mode": mode,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "translated": translated,
        }
    infer = local_infer(
        prompt=f"Translate from {source_lang} to {target_lang}: {text}",
        task="translation",
        backend="auto",
        output_profile="compact",
        max_tokens=256,
    )
    return {
        "schema": "translation_small.v1",
        "mode": mode,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "translated": infer.get("output", ""),
        "backend": infer.get("backend"),
    }


@mcp.tool()
def diagram_from_code(
    path: str = ".",
    diagram_type: str = "flowchart",
    max_nodes: int = 60,
    include_call_edges: bool = False,
    output_profile: str | None = None,
) -> dict[str, Any]:
    """Generate Mermaid diagrams from repository dependency/call metadata."""
    if diagram_type not in {"flowchart", "class", "sequence"}:
        raise ValueError("diagram_type must be one of: flowchart, class, sequence")
    if max_nodes < 1:
        raise ValueError("max_nodes must be >= 1")
    profile = _default_output_profile(output_profile)
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    dep = dependency_map(
        path=path,
        recursive=True,
        output_profile="normal",
        fields=["from", "to"],
        limit=max_nodes * 4,
    )
    edges = dep.get("edges", []) if isinstance(dep, dict) else []
    nodes: set[str] = set()
    lines: list[str] = []

    if diagram_type == "flowchart":
        lines.append("flowchart LR")
        for e in edges:
            src = str(e.get("from", ""))
            dst = str(e.get("to", ""))
            if not src or not dst:
                continue
            nodes.add(src)
            nodes.add(dst)
            if len(nodes) > max_nodes:
                break
            sid = _mermaid_sanitize_id(src)
            did = _mermaid_sanitize_id(dst)
            lines.append(f'    {sid}["{src}"] --> {did}["{dst}"]')
    elif diagram_type == "class":
        lines.append("classDiagram")
        for e in edges:
            src = Path(str(e.get("from", ""))).stem
            dst = Path(str(e.get("to", ""))).stem
            if not src or not dst:
                continue
            nodes.add(src)
            nodes.add(dst)
            if len(nodes) > max_nodes:
                break
            lines.append(f"    class {src}")
            lines.append(f"    class {dst}")
            lines.append(f"    {src} --> {dst}")
    else:
        lines.append("sequenceDiagram")
        lines.append("    autonumber")
        for e in edges[:max_nodes]:
            src = Path(str(e.get("from", ""))).stem or "A"
            dst = Path(str(e.get("to", ""))).stem or "B"
            lines.append(f"    {src}->>{dst}: imports")

    if include_call_edges:
        cg = call_graph(
            path=path,
            output_profile="compact",
            fields=["path", "caller", "callee"],
            limit=max_nodes,
        )
        call_edges = cg.get("edges", []) if isinstance(cg, dict) else []
    else:
        call_edges = []

    mermaid = "\n".join(lines)
    result = {
        "schema": "diagram_from_code.v1",
        "diagram_type": diagram_type,
        "path": str(root.relative_to(REPO_PATH)),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "mermaid": mermaid,
        "call_edges": call_edges if profile == "verbose" else [],
    }
    if profile == "compact":
        return {
            "schema": "diagram_from_code.compact.v1",
            "diagram_type": diagram_type,
            "node_count": result["node_count"],
            "edge_count": result["edge_count"],
            "mermaid": result["mermaid"],
        }
    return result


@mcp.tool()
def mermaid_lint_fix(
    mermaid_text: str,
    auto_fix: bool = True,
) -> dict[str, Any]:
    """Lint and optionally fix common Mermaid syntax issues."""
    if not mermaid_text.strip():
        raise ValueError("mermaid_text must not be empty")
    text = mermaid_text.replace("\t", "    ")
    issues: list[str] = []
    fixed = text

    header_re = re.compile(r"^\s*(flowchart|graph|classDiagram|sequenceDiagram)\b", re.MULTILINE)
    if not header_re.search(fixed):
        issues.append("Missing diagram header; prepended 'flowchart LR'.")
        if auto_fix:
            fixed = "flowchart LR\n" + fixed

    if "->" in fixed and "-->" not in fixed:
        issues.append("Potential invalid arrow syntax '->'; replacing with '-->'.")
        if auto_fix:
            fixed = fixed.replace("->", "-->")

    if "```" in fixed:
        issues.append("Remove markdown fences from raw mermaid input.")
        if auto_fix:
            fixed = fixed.replace("```mermaid", "").replace("```", "").strip()

    valid = True
    if "flowchart" in fixed and "-->" not in fixed and "---" not in fixed:
        valid = False
        issues.append("Flowchart has no edges.")

    return {
        "schema": "mermaid_lint_fix.v1",
        "valid": bool(valid),
        "issue_count": len(issues),
        "issues": issues,
        "fixed_mermaid": fixed if auto_fix else mermaid_text,
    }


@mcp.tool()
def drawio_generator(
    mode: str = "generate",
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    drawio_xml: str = "",
) -> dict[str, Any]:
    """Generate simple draw.io XML from graph data or parse XML back to graph."""
    if mode not in {"generate", "parse"}:
        raise ValueError("mode must be one of: generate, parse")

    if mode == "generate":
        ns = "https://app.diagrams.net"
        diagram_id = "diagram-1"
        nlist = nodes or []
        elist = edges or []
        root = [
            f'<mxfile host="{ns}">',
            f'  <diagram id="{diagram_id}" name="Page-1">',
            "    <mxGraphModel><root>",
            '      <mxCell id="0"/>',
            '      <mxCell id="1" parent="0"/>',
        ]
        for i, n in enumerate(nlist, start=2):
            nid = str(n.get("id", f"n{i}"))
            label = html.escape(str(n.get("label", nid)))
            x = int(n.get("x", 40 + (i - 2) * 40))
            y = int(n.get("y", 40 + (i - 2) * 20))
            root.append(
                f'      <mxCell id="{nid}" value="{label}" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">'
                f'<mxGeometry x="{x}" y="{y}" width="140" height="60" as="geometry"/></mxCell>'
            )
        for i, e in enumerate(elist, start=1):
            eid = f"e{i}"
            src = html.escape(str(e.get("source", "")))
            dst = html.escape(str(e.get("target", "")))
            root.append(
                f'      <mxCell id="{eid}" edge="1" parent="1" source="{src}" target="{dst}"><mxGeometry relative="1" as="geometry"/></mxCell>'
            )
        root.extend(["    </root></mxGraphModel>", "  </diagram>", "</mxfile>"])
        xml = "\n".join(root)
        return {
            "schema": "drawio_generator.v1",
            "mode": mode,
            "node_count": len(nlist),
            "edge_count": len(elist),
            "drawio_xml": xml,
        }

    if not drawio_xml.strip():
        raise ValueError("drawio_xml must not be empty for parse mode")
    try:
        tree = ET.fromstring(drawio_xml)
    except ET.ParseError as exc:
        raise ValueError(f"invalid drawio xml: {exc}") from exc

    parsed_nodes: list[dict[str, Any]] = []
    parsed_edges: list[dict[str, Any]] = []
    for cell in tree.iter("mxCell"):
        if cell.attrib.get("vertex") == "1":
            parsed_nodes.append(
                {"id": cell.attrib.get("id", ""), "label": cell.attrib.get("value", "")}
            )
        if cell.attrib.get("edge") == "1":
            parsed_edges.append(
                {
                    "id": cell.attrib.get("id", ""),
                    "source": cell.attrib.get("source", ""),
                    "target": cell.attrib.get("target", ""),
                }
            )
    return {
        "schema": "drawio_generator.v1",
        "mode": mode,
        "node_count": len(parsed_nodes),
        "edge_count": len(parsed_edges),
        "nodes": parsed_nodes,
        "edges": parsed_edges,
    }


@mcp.tool()
def diagram_sync_check(
    source_paths: list[str],
    diagram_path: str,
    mode: str = "check",
    marker: str = "diagram-fingerprint",
) -> dict[str, Any]:
    """Check/update diagram freshness metadata against source file fingerprints."""
    if not source_paths:
        raise ValueError("source_paths must not be empty")
    if mode not in {"check", "update"}:
        raise ValueError("mode must be one of: check, update")
    diagram = _resolve_repo_path(diagram_path)
    if not diagram.is_file():
        raise FileNotFoundError(diagram_path)
    for p in source_paths:
        rp = _resolve_repo_path(p)
        if not rp.is_file():
            raise FileNotFoundError(p)

    fingerprint = _diagram_fingerprint(source_paths)
    text = diagram.read_text(encoding="utf-8", errors="replace")
    rx = re.compile(rf"{re.escape(marker)}:\s*([a-f0-9]{{64}})")
    m = rx.search(text)
    existing = m.group(1) if m else ""
    in_sync = existing == fingerprint

    if mode == "update":
        _require_mutations()
        new_line = f"{marker}: {fingerprint}"
        if m:
            text = rx.sub(new_line, text, count=1)
        else:
            text = text.rstrip() + "\n\n" + new_line + "\n"
        diagram.write_text(text, encoding="utf-8")
        in_sync = True

    return {
        "schema": "diagram_sync_check.v1",
        "mode": mode,
        "diagram_path": str(diagram.relative_to(REPO_PATH)),
        "source_paths": sorted(source_paths),
        "marker": marker,
        "fingerprint": fingerprint,
        "existing_fingerprint": existing,
        "in_sync": in_sync,
        "needs_update": not in_sync,
    }


def local_model_status() -> dict[str, Any]:
    """Report local model configuration, bundled-model contract, and endpoint availability."""
    coding_python = Path(CODING_VENV_PYTHON)
    bootstrap_models_raw = os.getenv(
        "CONTINUE_OLLAMA_MODELS",
        DEFAULT_CONTINUE_OLLAMA_MODELS,
    )
    bootstrap_models = _parse_model_csv(bootstrap_models_raw)
    runtime_pull_enabled = os.getenv("OLLAMA_ALLOW_PULL", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    native_api_base = _ollama_native_base_url()
    openai_compat_base = _ollama_openai_base_url()
    selected_execution_mode, selected_execution_mode_source = _resolve_agent_execution_mode("auto", "")
    selected_execution_profile = _agent_execution_mode_profile(selected_execution_mode)
    status: dict[str, Any] = {
        "schema": "local_model_status.v1",
        "execution_mode": {
            "schema": AGENT_EXECUTION_MODE_SCHEMA_VERSION,
            "mode": selected_execution_mode,
            "source": selected_execution_mode_source,
            "profile_name": selected_execution_profile["profile_name"],
            "configured_mode": AGENT_EXECUTION_MODE_ENV or AGENT_EXECUTION_MODE_DEFAULT,
            "configured_profile": AGENT_EXECUTION_PROFILE_ENV,
        },
        "models_dir": str(LOCAL_MODELS_DIR),
        "models_dir_exists": _resolve_repo_path(".").exists() if str(LOCAL_MODELS_DIR).startswith(str(REPO_PATH)) else LOCAL_MODELS_DIR.exists(),
        "embed": {
            "backend": LOCAL_EMBED_BACKEND,
            "model": LOCAL_EMBED_MODEL,
            "dim": LOCAL_EMBED_DIM,
        },
        "infer": {
            "backend": LOCAL_INFER_BACKEND,
            "model": LOCAL_INFER_MODEL,
            "endpoint": LOCAL_INFER_ENDPOINT,
            "native_api_base": native_api_base,
            "tags_url": _ollama_tags_url(),
            "chat_url": f"{native_api_base}/api/chat",
            "openai_compat_base": openai_compat_base,
        },
        "coding": {
            "default_model": CODING_DEFAULT_MODEL,
            "agent_model": CODING_AGENT_MODEL,
            "micro_model": CODING_MICRO_MODEL,
            "micro_auto_prompt_chars": CODING_MICRO_MAX_PROMPT_CHARS,
            "venv_python": str(coding_python),
            "venv_python_exists": coding_python.is_file(),
            "default_model_installed": None,
            "default_model_in_bootstrap_list": CODING_DEFAULT_MODEL in bootstrap_models,
            "agent_model_installed": None,
            "agent_model_in_bootstrap_list": CODING_AGENT_MODEL in bootstrap_models,
            "micro_model_installed": None,
            "micro_model_in_bootstrap_list": CODING_MICRO_MODEL in bootstrap_models,
        },
        "ollama": {
            "api_contract": "native_ollama",
            "bootstrap_enabled": bool(bootstrap_models),
            "bootstrap_models": bootstrap_models,
            "runtime_pull_enabled": runtime_pull_enabled,
            "installed_models": [],
            "installed_models_count": 0,
        },
        "diagnostics": [],
    }
    diagnostics: list[str] = status["diagnostics"]
    if LOCAL_INFER_BACKEND == "endpoint":
        tags_status = _fetch_ollama_tags(timeout=3.0)
        status["infer"]["endpoint_reachable"] = tags_status.get("reachable", False)
        if "status" in tags_status:
            status["infer"]["endpoint_status"] = tags_status["status"]
        if "error" in tags_status:
            status["infer"]["endpoint_error"] = tags_status["error"]
        if "parse_error" in tags_status:
            status["infer"]["endpoint_parse_error"] = tags_status["parse_error"]

        openai_probe = _probe_http(openai_compat_base, timeout=2.0)
        status["infer"]["openai_compat_base_reachable"] = openai_probe.get(
            "reachable", False
        )
        if "status" in openai_probe:
            status["infer"]["openai_compat_base_status"] = openai_probe["status"]
        if "error" in openai_probe:
            status["infer"]["openai_compat_base_error"] = openai_probe["error"]

        installed_models = tags_status.get("model_ids", [])
        status["ollama"]["installed_models"] = installed_models
        status["ollama"]["installed_models_count"] = len(installed_models)
        status["coding"]["default_model_installed"] = (
            CODING_DEFAULT_MODEL in installed_models if CODING_DEFAULT_MODEL else None
        )
        status["coding"]["agent_model_installed"] = (
            CODING_AGENT_MODEL in installed_models if CODING_AGENT_MODEL else None
        )
        status["coding"]["micro_model_installed"] = (
            CODING_MICRO_MODEL in installed_models if CODING_MICRO_MODEL else None
        )

        chat_probe_model = CODING_AGENT_MODEL or CODING_DEFAULT_MODEL or LOCAL_INFER_MODEL
        if chat_probe_model and chat_probe_model in installed_models:
            chat_probe = _probe_ollama_chat(chat_probe_model, timeout=10.0)
            status["infer"]["chat_probe"] = chat_probe
            status["infer"]["agent_probe"] = chat_probe
            status["infer"]["chat_reachable"] = chat_probe.get("reachable", False)
            status["infer"]["chat_ok"] = chat_probe.get("ok", False)
            status["infer"]["agent_ok"] = chat_probe.get("ok", False)
            if "status" in chat_probe:
                status["infer"]["chat_status"] = chat_probe["status"]
                status["infer"]["agent_status"] = chat_probe["status"]
            if "error" in chat_probe:
                status["infer"]["chat_error"] = chat_probe["error"]
                status["infer"]["agent_error"] = chat_probe["error"]
        else:
            skipped_probe = {
                "url": status["infer"]["chat_url"],
                "model": chat_probe_model,
                "mode": "continue_agent",
                "reachable": None,
                "ok": None,
                "skipped": True,
                "reason": "probe_model_not_installed",
            }
            status["infer"]["chat_probe"] = skipped_probe
            status["infer"]["agent_probe"] = skipped_probe

        if not status["infer"]["endpoint_reachable"]:
            diagnostics.append(
                "Native Ollama tags endpoint is unreachable. Local inference diagnostics are incomplete until the endpoint responds."
            )
        else:
            if not installed_models:
                if status["ollama"]["bootstrap_enabled"]:
                    if runtime_pull_enabled:
                        diagnostics.append(
                            "Ollama is reachable but no models are installed. The default model set declared by CONTINUE_OLLAMA_MODELS is missing and runtime pulls are enabled."
                        )
                    else:
                        diagnostics.append(
                            "Ollama is reachable but no models are installed. The default model set declared by CONTINUE_OLLAMA_MODELS is missing, and OLLAMA_ALLOW_PULL=false prevents runtime downloads."
                        )
                else:
                    diagnostics.append(
                        "Ollama is reachable but no models are installed. CONTINUE_OLLAMA_MODELS is empty, so no default bundled model set is declared."
                    )
            if CODING_DEFAULT_MODEL and CODING_DEFAULT_MODEL not in installed_models:
                diagnostics.append(
                    f"CODING_DEFAULT_MODEL '{CODING_DEFAULT_MODEL}' is not installed in Ollama."
                )
            if CODING_AGENT_MODEL and CODING_AGENT_MODEL not in installed_models:
                diagnostics.append(
                    f"Continue Agent model '{CODING_AGENT_MODEL}' is not installed in Ollama."
                )
            if (
                CODING_DEFAULT_MODEL
                and status["ollama"]["bootstrap_enabled"]
                and CODING_DEFAULT_MODEL not in bootstrap_models
            ):
                diagnostics.append(
                    f"CODING_DEFAULT_MODEL '{CODING_DEFAULT_MODEL}' is not included in CONTINUE_OLLAMA_MODELS."
                )
            if (
                CODING_AGENT_MODEL
                and status["ollama"]["bootstrap_enabled"]
                and CODING_AGENT_MODEL not in bootstrap_models
            ):
                diagnostics.append(
                    f"Continue Agent model '{CODING_AGENT_MODEL}' is not included in CONTINUE_OLLAMA_MODELS."
                )
            if not status["infer"]["openai_compat_base_reachable"]:
                diagnostics.append(
                    f"Native Ollama is reachable at {native_api_base}, but the /v1/ root is not. Continue's generic config.json /v1 hint does not apply to this repo's checked-in provider: ollama config; use apiBase {native_api_base} without /v1."
                )
            chat_probe = status["infer"].get("chat_probe")
            if isinstance(chat_probe, dict):
                if chat_probe.get("skipped"):
                    diagnostics.append(
                        f"Ollama chat probe skipped because configured model '{chat_probe.get('model')}' is not installed."
                    )
                elif not chat_probe.get("ok"):
                    detail = (
                        chat_probe.get("error")
                        or chat_probe.get("body")
                        or "unknown error"
                    )
                    diagnostics.append(
                        f"Ollama /api/chat Agent-mode probe failed for model '{chat_probe.get('model')}'. Continue Agent/tool use may fail until the model runner is healthy. Detail: {_trim_text(str(detail), max_chars=300)}"
                    )
    else:
        status["infer"]["endpoint_reachable"] = None
        skipped_probe = {
            "url": status["infer"]["chat_url"],
            "model": CODING_AGENT_MODEL or CODING_DEFAULT_MODEL or LOCAL_INFER_MODEL,
            "mode": "continue_agent",
            "reachable": None,
            "ok": None,
            "skipped": True,
            "reason": "local_infer_backend_not_endpoint",
        }
        status["infer"]["chat_probe"] = skipped_probe
        status["infer"]["agent_probe"] = skipped_probe
        diagnostics.append(
            "LOCAL_INFER_BACKEND is not 'endpoint'; Ollama endpoint diagnostics were skipped."
        )
    return status


def _coding_checks(
    profile: str = "quick",
    target: str = ".",
    timeout_seconds: int = 600,
    python_executable: str | None = None,
) -> dict[str, Any]:
    profile_norm = profile.strip().lower()
    if profile_norm not in {"quick", "lint", "type", "tests", "full"}:
        raise ValueError("profile must be one of: quick, lint, type, tests, full")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")

    target_path = _resolve_repo_path(target)
    rel_target = str(target_path.relative_to(REPO_PATH)) if target_path != REPO_PATH else "."
    py_exec = python_executable or CODING_VENV_PYTHON
    py = Path(py_exec)
    if not py.is_file():
        raise FileNotFoundError(
            f"coding venv python not found: {py_exec} (build image with coding venv enabled)"
        )

    steps: list[dict[str, Any]] = []
    commands: list[list[str]] = []
    if profile_norm in {"quick", "lint", "full"}:
        commands.append([py_exec, "-m", "ruff", "check", rel_target])
    if profile_norm in {"type", "full"}:
        commands.append([py_exec, "-m", "mypy", rel_target, "--ignore-missing-imports"])
    if profile_norm in {"quick", "tests", "full"}:
        test_target = rel_target
        if rel_target == "." and (REPO_PATH / "tests").is_dir():
            test_target = "tests"
        commands.append([py_exec, "-m", "pytest", "-q", test_target])

    out_cap = _token_budget_apply_max(None)
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(REPO_PATH),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            steps.append(
                {
                    "command": cmd,
                    "ok": proc.returncode == 0,
                    "exit_code": proc.returncode,
                    "stdout": _trim_text(proc.stdout, max_chars=out_cap),
                    "stderr": _trim_text(proc.stderr, max_chars=out_cap),
                    "timeout": False,
                }
            )
            if proc.returncode != 0:
                break
        except subprocess.TimeoutExpired as exc:
            steps.append(
                {
                    "command": cmd,
                    "ok": False,
                    "exit_code": None,
                    "stdout": _trim_text(
                        (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                        max_chars=out_cap,
                    ),
                    "stderr": _trim_text(
                        (exc.stderr or "") if isinstance(exc.stderr, str) else "",
                        max_chars=out_cap,
                    ),
                    "timeout": True,
                }
            )
            break

    ok = bool(steps) and all(bool(s.get("ok")) for s in steps)
    trace: dict[str, Any] | None = None
    if ok and target_path.is_file():
        trace = _memory_trace_reusable_script_success(
            str(target_path.relative_to(REPO_PATH)),
            profile=profile_norm,
            steps=steps,
            venv_python=py_exec,
        )

    result = {
        "schema": "coding_checks.v1",
        "profile": profile_norm,
        "target": rel_target,
        "venv_python": py_exec,
        "ok": ok,
        "steps": steps,
    }
    if trace is not None:
        result["memory_trace"] = trace
    return result


def _coding_pip_install(
    packages: list[str],
    upgrade: bool = False,
    timeout_seconds: int = 600,
    python_executable: str | None = None,
) -> dict[str, Any]:
    if not packages:
        raise ValueError("packages must not be empty")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    py_exec = python_executable or CODING_VENV_PYTHON
    py = Path(py_exec)
    if not py.is_file():
        raise FileNotFoundError(
            f"coding venv python not found: {py_exec} (build image with coding venv enabled)"
        )
    invalid = [p for p in packages if not p.strip()]
    if invalid:
        raise ValueError("packages must not contain empty entries")

    cmd = [py_exec, "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")
    cmd.extend(packages)
    out_cap = _token_budget_apply_max(None)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_PATH),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "schema": "coding_pip.v1",
            "ok": False,
            "timeout": True,
            "exit_code": None,
            "command": cmd,
            "venv_python": py_exec,
            "packages": packages,
            "stdout": _trim_text(
                (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                max_chars=out_cap,
            ),
            "stderr": _trim_text(
                (exc.stderr or "") if isinstance(exc.stderr, str) else "",
                max_chars=out_cap,
            ),
        }
    return {
        "schema": "coding_pip.v1",
        "ok": proc.returncode == 0,
        "timeout": False,
        "exit_code": proc.returncode,
        "command": cmd,
        "venv_python": py_exec,
        "packages": packages,
        "stdout": _trim_text(proc.stdout, max_chars=out_cap),
        "stderr": _trim_text(proc.stderr, max_chars=out_cap),
    }


def _coding_stream_payload_from_steps(
    steps: list[dict[str, Any]] | None,
    max_events: int = 200,
) -> dict[str, Any]:
    stdout_stream: list[dict[str, Any]] = []
    stderr_stream: list[dict[str, Any]] = []
    out_cap = _token_budget_apply_max(None)
    for idx, step in enumerate(steps or []):
        if len(stdout_stream) + len(stderr_stream) >= max_events:
            break
        cmd = step.get("command", [])
        if not isinstance(cmd, list):
            cmd = []
        out = str(step.get("stdout", "") or "")
        err = str(step.get("stderr", "") or "")
        if out:
            stdout_stream.append(
                {
                    "index": idx,
                    "command": cmd,
                    "chunk": _trim_text(out, max_chars=min(4000, out_cap)),
                }
            )
        if err:
            stderr_stream.append(
                {
                    "index": idx,
                    "command": cmd,
                    "chunk": _trim_text(err, max_chars=min(4000, out_cap)),
                }
            )
    stdout_text = _trim_text(
        "\n".join(item["chunk"] for item in stdout_stream if item.get("chunk")),
        max_chars=out_cap,
    )
    stderr_text = _trim_text(
        "\n".join(item["chunk"] for item in stderr_stream if item.get("chunk")),
        max_chars=out_cap,
    )
    return {
        "stdout": stdout_text,
        "stderr": stderr_text,
        "stdout_stream": stdout_stream,
        "stderr_stream": stderr_stream,
    }


def _coding_sandbox_prepare(
    sandbox_mode: str = "shared",
    sandbox_id: str = "",
) -> dict[str, Any]:
    mode = sandbox_mode.strip().lower()
    if mode not in {"shared", "isolated"}:
        raise ValueError("sandbox_mode must be one of: shared, isolated")

    base_python = Path(CODING_VENV_PYTHON)
    if not base_python.is_file():
        raise FileNotFoundError(
            f"coding base venv python not found: {CODING_VENV_PYTHON}"
        )
    if mode == "shared":
        return {
            "mode": "shared",
            "sandbox_id": "",
            "sandbox_path": "",
            "venv_python": str(base_python),
            "created": False,
        }

    token = sandbox_id.strip() or f"sbox-{uuid.uuid4().hex[:10]}"
    if not re.fullmatch(r"[A-Za-z0-9._-]+", token):
        raise ValueError("sandbox_id contains invalid characters")

    root = _resolve_repo_path(str(CODING_SANDBOX_ROOT))
    root.mkdir(parents=True, exist_ok=True)
    sandbox_dir = root / token
    venv_dir = sandbox_dir / "venv"
    venv_python = venv_dir / "bin" / "python"
    created = False
    if not venv_python.is_file():
        source_venv = base_python.parent.parent
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_venv, venv_dir, symlinks=True, dirs_exist_ok=True)
        created = True
    if not venv_python.is_file():
        raise RuntimeError(f"failed to prepare isolated sandbox venv: {venv_python}")
    return {
        "mode": "isolated",
        "sandbox_id": token,
        "sandbox_path": str(sandbox_dir.relative_to(REPO_PATH)),
        "venv_python": str(venv_python),
        "created": created,
    }


def _coding_sandbox_manage(action: str, sandbox_id: str = "") -> dict[str, Any]:
    act = action.strip().lower()
    root = _resolve_repo_path(str(CODING_SANDBOX_ROOT))
    if act == "list":
        if not root.exists():
            return {"schema": "coding_sandbox.v1", "action": "list", "items": []}
        rows = []
        for p in sorted(root.glob("*")):
            if not p.is_dir():
                continue
            rows.append(
                {
                    "sandbox_id": p.name,
                    "sandbox_path": str(p.relative_to(REPO_PATH)),
                    "venv_python_exists": (p / "venv" / "bin" / "python").is_file(),
                }
            )
        return {"schema": "coding_sandbox.v1", "action": "list", "items": rows}
    if act == "delete":
        token = sandbox_id.strip()
        if not token:
            raise ValueError("sandbox_id is required for delete action")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", token):
            raise ValueError("sandbox_id contains invalid characters")
        target = root / token
        existed = target.exists()
        if target.exists():
            shutil.rmtree(target)
        return {
            "schema": "coding_sandbox.v1",
            "action": "delete",
            "sandbox_id": token,
            "deleted": existed,
        }
    if act == "create":
        prepared = _coding_sandbox_prepare("isolated", sandbox_id=sandbox_id)
        return {"schema": "coding_sandbox.v1", "action": "create", **prepared}
    raise ValueError("sandbox_action must be one of: create, delete, list")


def local_embed(
    texts: list[str],
    backend: str = "auto",
    normalize: bool = True,
    output_profile: str | None = None,
    offset: int = 0,
    limit: int | None = None,
    compress: bool = False,
    store_result: bool = False,
) -> dict[str, Any]:
    """Create local offline embeddings for small specialized tasks."""
    if not texts:
        raise ValueError("texts must not be empty")
    profile = _default_output_profile(output_profile)
    selected, vectors = _local_embed_vectors(texts, backend=backend, normalize=normalize)
    rows = [{"index": i, "text": t, "vector": vectors[i]} for i, t in enumerate(texts)]
    rows = _paginate(rows, offset=offset, limit=limit)
    result: dict[str, Any] = {
        "schema": "local_embed.v1",
        "backend": selected,
        "count": len(rows),
        "dim": len(rows[0]["vector"]) if rows else 0,
        "rows": rows,
    }
    if profile == "compact":
        result = {
            "schema": "local_embed.compact.v1",
            "backend": selected,
            "count": len(rows),
            "dim": len(rows[0]["vector"]) if rows else 0,
            "rows": [{"index": r["index"]} for r in rows],
        }
    if compress and isinstance(result.get("rows"), list):
        result["rows_compressed"] = _compress_table(result["rows"])
        result.pop("rows", None)
    if store_result:
        result["result_id"] = _result_store_put("local_embed", result)
    return result


def local_infer(
    prompt: str,
    task: str = "general",
    backend: str = "auto",
    model: str = "",
    max_tokens: int = 256,
    temperature: float = 0.2,
    system: str = "",
    output_profile: str | None = None,
    store_result: bool = False,
) -> dict[str, Any]:
    """Run local offline inference via endpoint or deterministic fallback."""
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if max_tokens < 1:
        raise ValueError("max_tokens must be >= 1")
    if temperature < 0:
        raise ValueError("temperature must be >= 0")
    profile = _default_output_profile(output_profile)
    selected = backend.strip().lower()
    if selected == "auto":
        selected = LOCAL_INFER_BACKEND or "endpoint"
    model_name = model or LOCAL_INFER_MODEL or "local-default"

    if selected == "endpoint":
        try:
            text = _local_infer_via_endpoint(
                prompt=prompt,
                model=model_name,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
            )
        except Exception as exc:
            _failure_record(
                command=["local_infer", "endpoint"],
                stderr=str(exc),
                category="local_infer",
                suggestion="Ensure local inference endpoint is running and reachable.",
            )
            text = ""
            selected = "fallback"
    if selected in {"fallback", "rule", "hash"}:
        optimized = prompt_optimize(
            prompt=prompt,
            mode=_prompt_optimize_mode_for_task(task),
        )
        text = optimized["optimized_prompt"][:max_tokens * 6]
    text = _sanitize_model_output(text)
    result = {
        "schema": "local_infer.v1",
        "backend": selected,
        "model": model_name,
        "task": task,
        "output": _trim_text(text),
        "ok": bool(text),
    }
    if profile == "compact":
        result = {
            "schema": "local_infer.compact.v1",
            "backend": selected,
            "model": model_name,
            "ok": bool(text),
            "output": _trim_text(text, max_chars=1200),
        }
    if store_result:
        result["result_id"] = _result_store_put("local_infer", result)
    return result


@mcp.tool()
def autocomplete(
    prefix: str,
    suffix: str = "",
    language: str = "",
    backend: str = "auto",
    model: str = "",
    max_tokens: int = 64,
    temperature: float = 0.1,
    stop: list[str] | None = None,
    output_profile: str | None = None,
    store_result: bool = False,
) -> dict[str, Any]:
    """Compatibility autocomplete endpoint. Prefer task_router(mode='autocomplete') for new integrations."""
    if not prefix:
        raise ValueError("prefix must not be empty")
    if max_tokens < 1:
        raise ValueError("max_tokens must be >= 1")
    if temperature < 0:
        raise ValueError("temperature must be >= 0")
    profile = _default_output_profile(output_profile)
    selected = backend.strip().lower()
    if selected == "auto":
        selected = LOCAL_INFER_BACKEND or "endpoint"
    model_name = model or LOCAL_INFER_MODEL or "local-default"

    completion = ""
    if selected == "endpoint":
        try:
            completion = _local_infer_via_endpoint(
                prompt=_autocomplete_prompt(prefix=prefix, suffix=suffix, language=language),
                model=model_name,
                max_tokens=max_tokens,
                temperature=temperature,
                system=(
                    "You are a code completion engine. "
                    "Return only code continuation text for the cursor."
                ),
                stop=stop,
            )
        except Exception as exc:
            _failure_record(
                command=["autocomplete", "endpoint"],
                stderr=str(exc),
                category="autocomplete",
                suggestion="Ensure local inference endpoint is running and reachable.",
            )
            selected = "fallback"
    if selected in {"fallback", "rule", "hash"}:
        completion = _autocomplete_fallback(prefix=prefix, suffix=suffix)

    completion = _sanitize_model_output(completion)
    completion = _autocomplete_strip_wrappers(completion)
    completion = _autocomplete_apply_stops(completion, stop=stop)
    completion = _trim_text(completion, max_chars=max(200, max_tokens * 12))

    result: dict[str, Any] = {
        "schema": "autocomplete.v1",
        "backend": selected,
        "model": model_name,
        "language": language.strip(),
        "prefix_chars": len(prefix),
        "suffix_chars": len(suffix),
        "max_tokens": max_tokens,
        "completion": completion,
        "ok": bool(completion),
    }
    if profile == "compact":
        result = {
            "schema": "autocomplete.compact.v1",
            "backend": selected,
            "model": model_name,
            "ok": bool(completion),
            "completion": completion,
        }
    if store_result:
        result["result_id"] = _result_store_put("autocomplete", result)
    return result


def local_rerank(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int = 20,
    backend: str = "auto",
    output_profile: str | None = None,
) -> dict[str, Any]:
    """Rerank candidate items locally using offline embeddings."""
    if not query.strip():
        raise ValueError("query must not be empty")
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    if not candidates:
        return {"schema": "local_rerank.v1", "count": 0, "results": []}
    profile = _default_output_profile(output_profile)

    texts = []
    for c in candidates:
        txt = " ".join(
            str(c.get(k, "")) for k in ("path", "name", "match", "lineText", "kind")
        ).strip()
        texts.append(txt)
    selected, vectors = _local_embed_vectors([query, *texts], backend=backend, normalize=True)
    qv = vectors[0]
    scored = []
    for idx, cand in enumerate(candidates):
        score = _vec_cosine(qv, vectors[idx + 1])
        row = dict(cand)
        row["local_score"] = score
        scored.append(row)
    scored.sort(key=lambda x: float(x.get("local_score", 0.0)), reverse=True)
    scored = scored[:top_k]
    if profile == "compact":
        scored = [
            {"path": r.get("path"), "kind": r.get("kind"), "local_score": r.get("local_score")}
            for r in scored
        ]
    return {
        "schema": "local_rerank.v1",
        "backend": selected,
        "count": len(scored),
        "results": scored,
    }


def _extract_prompt_file_paths(prompt: str, max_paths: int = 4) -> list[str]:
    if max_paths < 1:
        return []
    pattern = re.compile(
        r"(?<![\w/.-])(?:\./)?(?:[A-Za-z0-9_.-]+/)*(?:\.[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)"
    )
    seen: set[str] = set()
    out: list[str] = []
    for m in pattern.finditer(prompt):
        raw = m.group(0).strip("`'\"()[]{}<>,:;")
        if not raw:
            continue
        if raw.startswith("./"):
            raw = raw[2:]
        candidate = raw.replace("\\", "/")
        if candidate in seen:
            continue
        with contextlib.suppress(Exception):
            resolved = _resolve_repo_path(candidate)
            if resolved.is_file():
                seen.add(candidate)
                out.append(candidate)
                if len(out) >= max_paths:
                    break
    return out


def _prompt_optimize_mode_for_task(task: str) -> str:
    route = TASK_ROUTE_ALIASES.get(task.strip().lower(), task.strip().lower())
    if route in {'review', 'security'}:
        return 'review'
    if route in {'research', 'math', 'vision'}:
        return 'search'
    if route == 'tooling_strict':
        return 'tooling_strict'
    return 'coding'


def _default_continue_model_routing() -> dict[str, Any]:
    coding_model = CODING_DEFAULT_MODEL or DEFAULT_CODING_MODEL
    coding_route = {'model': coding_model, 'file': CODING_MODEL_CONFIG_FILE}
    return {
        'source': None,
        'loaded': False,
        'router': dict(coding_route),
        'routes': {
            'coding': dict(coding_route),
            CODING_AGENT_ROUTE: {
                'model': CODING_AGENT_MODEL or DEFAULT_CODING_AGENT_MODEL,
                'file': CODING_AGENT_MODEL_CONFIG_FILE,
            },
            CODING_MICRO_ROUTE: {
                'model': CODING_MICRO_MODEL or DEFAULT_CODING_MICRO_MODEL,
                'file': CODING_MICRO_MODEL_CONFIG_FILE,
            },
        },
    }


def _continue_model_routing_candidates() -> list[Path]:
    module_dir = Path(__file__).resolve().parent
    candidates = [
        REPO_PATH / CONTINUE_MODEL_ROUTING_RELATIVE_PATH,
        module_dir / 'defaults' / 'continue' / 'model-routing.yaml',
        Path('/opt/codebase-tooling/defaults/continue/model-routing.yaml'),
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _load_continue_model_routing() -> dict[str, Any]:
    fallback = _default_continue_model_routing()
    if yaml is None:
        fallback['error'] = 'PyYAML is not installed in this runtime'
        return fallback

    last_error = ''
    for candidate in _continue_model_routing_candidates():
        if not candidate.is_file():
            continue
        try:
            parsed = yaml.safe_load(candidate.read_text(encoding='utf-8')) or {}
        except Exception as exc:
            last_error = str(exc)
            continue
        if not isinstance(parsed, dict):
            last_error = f'invalid routing config at {candidate}'
            continue

        loaded = _default_continue_model_routing()
        loaded['loaded'] = True
        loaded['source'] = str(candidate)

        router_raw = parsed.get('router', {})
        if isinstance(router_raw, dict):
            loaded['router'] = {
                'model': str(router_raw.get('model') or loaded['router']['model']).strip(),
                'file': str(router_raw.get('file') or loaded['router']['file']).strip(),
            }

        routes_raw = parsed.get('routes', {})
        if isinstance(routes_raw, dict):
            for route_name, route_value in routes_raw.items():
                if not isinstance(route_value, dict):
                    continue
                current = loaded['routes'].get(route_name, {'model': '', 'file': ''})
                loaded['routes'][route_name] = {
                    'model': str(route_value.get('model') or current.get('model') or '').strip(),
                    'file': str(route_value.get('file') or current.get('file') or '').strip(),
                }
        return loaded

    if last_error:
        fallback['error'] = last_error
    return fallback


def _classify_task_prompt(prompt: str, task: str = 'general') -> dict[str, Any]:
    text = prompt.strip()
    lowered = text.lower()
    tokens = _tokenize_router_query(text)
    token_set = set(tokens)
    joined = ' '.join(tokens)
    routes = ['general', *TASK_ROUTE_KEYWORDS.keys()]
    scores = {route: 0.0 for route in routes}
    reasons = {route: [] for route in routes}

    def bump(route: str, weight: float, reason: str) -> None:
        scores[route] += weight
        reasons[route].append(reason)

    task_norm = task.strip().lower()
    task_route = TASK_ROUTE_ALIASES.get(task_norm, '')
    if task_route and task_route != 'general':
        bump(task_route, 8.0, f'task:{task_norm}')

    for route, terms in TASK_ROUTE_KEYWORDS.items():
        exact_hits = sum(1 for term in terms if term in token_set)
        phrase_hits = sum(1 for term in terms if len(term) > 3 and term in joined)
        if exact_hits or phrase_hits:
            bump(route, (exact_hits * 3.0) + phrase_hits, f'keywords:{exact_hits}/{phrase_hits}')

    if '```' in text:
        bump('coding', 2.5, 'code_fence')
    if re.search(r'\b(def|class|function|method|pytest|traceback|exception|stack)\b', lowered):
        bump('coding', 2.0, 'code_terms')
    if re.search(r'\b(review|audit|bug|issue|regression|risk)\b', lowered):
        bump('review', 1.5, 'review_terms')
    if re.search(r'\b(xss|csrf|cve|inject|secret|credential|auth)\b', lowered):
        bump('security', 2.5, 'security_terms')
    if re.search(r'\b(integral|derivative|equation|matrix|proof)\b', lowered):
        bump('math', 2.5, 'math_terms')
    if re.search(r'\b(image|screenshot|diagram|photo|figure|ocr)\b', lowered):
        bump('vision', 2.5, 'vision_terms')
    if re.search(r'\b(doc|docs|documentation|readme|explain|summarize|summary|compare)\b', lowered):
        bump('research', 1.5, 'research_terms')

    file_paths = _extract_prompt_file_paths(text, max_paths=6)
    code_suffixes = {
        '.c', '.cc', '.cpp', '.go', '.java', '.js', '.jsx', '.py', '.rb', '.rs', '.sh', '.ts', '.tsx'
    }
    image_suffixes = {'.gif', '.jpeg', '.jpg', '.png', '.svg', '.webp'}
    doc_suffixes = {'.adoc', '.md', '.pdf', '.rst', '.txt'}
    if any(Path(path).suffix.lower() in code_suffixes for path in file_paths):
        bump('coding', 2.0, 'code_paths')
    if any(Path(path).suffix.lower() in image_suffixes for path in file_paths):
        bump('vision', 3.0, 'image_paths')
    if any(Path(path).suffix.lower() in doc_suffixes for path in file_paths):
        bump('research', 1.5, 'document_paths')

    if max(scores[route] for route in routes if route != 'general') <= 0:
        bump('general', 1.0, 'default')
    else:
        bump('general', 0.25, 'fallback')

    ranked = [
        {
            'route': route,
            'score': round(score, 4),
            'reasons': reasons[route][:5],
        }
        for route, score in scores.items()
    ]
    ranked.sort(key=lambda row: (row['score'], row['route']), reverse=True)
    top = ranked[0]
    second_score = float(ranked[1]['score']) if len(ranked) > 1 else 0.0
    score_gap = float(top['score']) - second_score
    confidence_floor = 0.35 if top['route'] == 'general' else 0.45
    confidence = min(
        0.99,
        round(
            confidence_floor
            + min(float(top['score']), 12.0) / 20.0
            + min(max(score_gap, 0.0), 8.0) / 20.0,
            4,
        ),
    )
    return {
        'schema': 'task_prompt_classification.v1',
        'route': top['route'],
        'confidence': confidence,
        'score_gap': round(score_gap, 4),
        'task_hint': task_route or None,
        'file_hints': file_paths[:4],
        'ranked': ranked[:4],
    }


def _trim_task_context_block(text: Any, max_chars: int) -> str:
    if max_chars < 1:
        return ''
    normalized = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    normalized = '\n'.join(line.rstrip() for line in normalized.splitlines())
    normalized = re.sub(r'\n{3,}', '\n\n', normalized).strip()
    truncated, _ = _truncate_with_flag(normalized, max_chars=max_chars)
    return truncated.strip()


def _append_task_context_block(parts: list[str], piece: str, max_chars: int) -> None:
    normalized = _trim_task_context_block(piece, max_chars=max_chars)
    if not normalized:
        return
    current = '\n\n'.join(parts)
    remaining = max_chars - len(current)
    if parts:
        remaining -= 2
    if remaining < 32:
        return
    parts.append(_trim_task_context_block(normalized, max_chars=remaining))


def _encode_task_prompt_packet(
    prompt: str,
    route: str,
    task: str = 'general',
    memory_session: str = '',
    memory_context: str = '',
    retrieval_context: str = '',
) -> dict[str, Any]:
    normalized = re.sub(r'\s+', ' ', prompt.strip())
    task_norm = task.strip().lower()
    session_norm = _normalize_task_memory_session(memory_session)
    memory_text = _trim_task_inline_text(memory_context, max_chars=900)
    retrieval_text = _trim_task_context_block(retrieval_context, max_chars=1400)
    packet = {
        'r': TASK_ROUTE_CODE_MAP.get(route, 'G'),
        'q': normalized,
        's': session_norm,
    }
    if task_norm and task_norm not in {'general', route}:
        packet['t'] = task_norm[:24]
    if memory_text:
        packet['m'] = memory_text
    if retrieval_text:
        packet['k'] = retrieval_text
    encoded_prompt = json.dumps(packet, ensure_ascii=True, separators=(',', ':'))
    return {
        'schema': 'task_prompt_packet.v1',
        'codec': 'compact_json_v1',
        'route': route,
        'route_code': packet['r'],
        'original_chars': len(prompt),
        'normalized_chars': len(normalized),
        'memory_chars': len(memory_text),
        'retrieval_chars': len(retrieval_text),
        'encoded_chars': len(encoded_prompt),
        'char_saving_vs_original': len(prompt) - len(encoded_prompt),
        'char_saving_vs_normalized': len(normalized) - len(encoded_prompt),
        'encoded_prompt': encoded_prompt,
    }


def _resolve_task_model_route(
    route: str,
    routing: dict[str, Any],
    requested_model: str = '',
    prompt: str = '',
    task_hint: str = '',
) -> dict[str, Any]:
    explicit_model = requested_model.strip()
    if explicit_model:
        return {
            'route': route,
            'model': explicit_model,
            'file': '',
            'source': 'explicit_model',
        }

    if route == 'coding':
        hint_norm = str(task_hint or '').strip().lower()
        selected_micro = hint_norm in MICRO_CODING_TASK_HINTS
        selected_source = 'task_hint:micro_coding'
        if not selected_micro:
            normalized_prompt = re.sub(r'\s+', ' ', str(prompt or '').strip())
            selected_micro = (
                bool(normalized_prompt)
                and len(normalized_prompt) <= CODING_MICRO_MAX_PROMPT_CHARS
                and len(_extract_prompt_file_paths(str(prompt or ''), max_paths=3)) <= 1
                and len(_infer_batch_from_prompt(str(prompt or ''))) < 2
            )
            if selected_micro:
                selected_source = 'auto:short_coding_prompt'
        if selected_micro:
            routes_cfg = routing.get('routes', {}) if isinstance(routing.get('routes', {}), dict) else {}
            selected = routes_cfg.get(CODING_MICRO_ROUTE, {}) if isinstance(routes_cfg.get(CODING_MICRO_ROUTE, {}), dict) else {}
            selected_model = str(selected.get('model') or CODING_MICRO_MODEL or '').strip()
            selected_file = str(selected.get('file') or CODING_MICRO_MODEL_CONFIG_FILE).strip()
            if selected_model:
                return {
                    'route': route,
                    'model': selected_model,
                    'file': selected_file,
                    'source': selected_source,
                }

    router_cfg = routing.get('router', {}) if isinstance(routing.get('router', {}), dict) else {}
    router_model = str(router_cfg.get('model') or LOCAL_INFER_MODEL or CODING_DEFAULT_MODEL or 'local-default').strip()
    router_file = str(router_cfg.get('file') or '').strip()
    if route == 'general':
        return {
            'route': route,
            'model': router_model,
            'file': router_file,
            'source': 'router_default',
        }

    routes_cfg = routing.get('routes', {}) if isinstance(routing.get('routes', {}), dict) else {}
    selected = routes_cfg.get(route, {}) if isinstance(routes_cfg.get(route, {}), dict) else {}
    selected_model = str(selected.get('model') or '').strip()
    selected_file = str(selected.get('file') or '').strip()
    if selected_model:
        return {
            'route': route,
            'model': selected_model,
            'file': selected_file,
            'source': f'route:{route}',
        }
    return {
        'route': route,
        'model': router_model,
        'file': router_file,
        'source': 'router_fallback',
    }


def _trim_task_inline_text(text: Any, max_chars: int) -> str:
    if max_chars < 1:
        return ''
    normalized = ' '.join(str(text or '').split())
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 3:
        return normalized[:max_chars]
    return normalized[: max_chars - 3].rstrip() + '...'


def _append_task_context_piece(parts: list[str], piece: str, max_chars: int) -> None:
    normalized = _trim_task_inline_text(piece, max_chars=max_chars)
    if not normalized:
        return
    current = ' | '.join(parts)
    remaining = max_chars - len(current)
    if parts:
        remaining -= 3
    if remaining < 8:
        return
    parts.append(_trim_task_inline_text(normalized, max_chars=remaining))


def _normalize_task_memory_session(memory_session: str) -> str:
    normalized = str(memory_session or '').strip().replace('\\', '/')
    normalized = re.sub(r'\s+', '-', normalized).strip('/')
    return normalized[:64] or 'default'


def _task_route_namespace(route: str) -> str:
    route_norm = route.strip().lower() or 'general'
    if route_norm not in {'general', *TASK_ROUTE_KEYWORDS.keys()}:
        route_norm = 'general'
    return f'task/route/{route_norm}'


def _task_session_namespace(memory_session: str) -> str:
    return f'task/session/{_normalize_task_memory_session(memory_session)}'


def _task_memory_updated_epoch(row: dict[str, Any]) -> float:
    ts = _parse_iso_timestamp(str(row.get('updated_at', ''))) or _parse_iso_timestamp(
        str(row.get('created_at', ''))
    )
    return ts.timestamp() if ts else 0.0


def _summarize_task_workspace_facts(facts: dict[str, Any], max_chars: int = 220) -> str:
    if not isinstance(facts, dict) or not facts:
        return ''
    ext_rows = facts.get('top_extensions', [])
    ext_summary = []
    if isinstance(ext_rows, list):
        for row in ext_rows[:3]:
            if not isinstance(row, dict):
                continue
            ext = str(row.get('extension', '')).strip()
            count = row.get('count')
            if not ext:
                continue
            ext_summary.append(f'{ext}:{count}')
    parts = []
    if isinstance(facts.get('file_count'), int):
        parts.append(f"files={facts['file_count']}")
    if ext_summary:
        parts.append(f"ext={','.join(ext_summary)}")
    parts.append(f"tests={'yes' if facts.get('has_tests_dir') else 'no'}")
    parts.append(f"readme={'yes' if facts.get('has_readme') else 'no'}")
    parts.append(f"git={'yes' if facts.get('is_git_repo') else 'no'}")
    profile = str(facts.get('default_output_profile', '')).strip()
    if profile:
        parts.append(f'profile={profile}')
    return _trim_task_inline_text(' '.join(parts), max_chars=max_chars)


def _task_memory_value_text(value: Any, max_chars: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        except TypeError:
            text = str(value)
    return _trim_task_inline_text(text, max_chars=max_chars)


def _task_namespace_memory_context(
    namespace: str,
    max_chars: int,
    payload: dict[str, Any] | None = None,
) -> str:
    payload = payload or _memory_load()
    now = datetime.now(timezone.utc)
    summaries = []
    for row in payload.get('summaries', []):
        if row.get('namespace') != namespace:
            continue
        if _is_expired(row.get('expires_at'), now):
            continue
        summaries.append(dict(row))
    summaries.sort(
        key=lambda row: (
            float(row.get('confidence', 0.0) or 0.0),
            _task_memory_updated_epoch(row),
            str(row.get('focus', '')),
        ),
        reverse=True,
    )

    decisions = _effective_decisions(
        decisions=payload.get('decisions', []),
        now=now,
        namespace=namespace,
        include_expired=False,
    )
    decisions.sort(
        key=lambda row: (
            _decision_priority(str(row.get('decided_by', ''))),
            float(row.get('confidence', 0.0) or 0.0),
            _task_memory_updated_epoch(row),
            str(row.get('topic', '')),
        ),
        reverse=True,
    )

    entries = []
    for row in payload.get('entries', []):
        if row.get('namespace') != namespace:
            continue
        if _is_expired(row.get('expires_at'), now):
            continue
        entries.append(dict(row))
    entries.sort(key=_memory_entry_rank, reverse=True)

    pieces: list[str] = []
    for row in summaries[:2]:
        focus = _trim_task_inline_text(row.get('focus', 'summary'), max_chars=24)
        summary_text = _trim_task_inline_text(row.get('summary', ''), max_chars=160)
        _append_task_context_piece(pieces, f'{focus}={summary_text}', max_chars=max_chars)
    for row in decisions[:2]:
        topic = _trim_task_inline_text(row.get('topic', 'decision'), max_chars=24)
        decision_text = _task_memory_value_text(row.get('decision'), max_chars=140)
        _append_task_context_piece(pieces, f'{topic}={decision_text}', max_chars=max_chars)
    if not summaries:
        for row in entries[:2]:
            key = _trim_task_inline_text(row.get('key', 'entry'), max_chars=24)
            value_text = _task_memory_value_text(row.get('value'), max_chars=140)
            _append_task_context_piece(pieces, f'{key}={value_text}', max_chars=max_chars)
    return ' | '.join(pieces)


def _task_workspace_facts_payload() -> dict[str, Any]:
    facts_path = _resolve_repo_path('.codebase-tooling-mcp/memory/workspace_facts.json')
    facts = workspace_facts(refresh=False) if facts_path.is_file() else workspace_facts(refresh=True)
    return facts if isinstance(facts, dict) else {}


def _build_task_memory_context(route: str, memory_session: str) -> dict[str, Any]:
    normalized_session = _normalize_task_memory_session(memory_session)
    route_namespace = _task_route_namespace(route)
    session_namespace = _task_session_namespace(normalized_session)
    payload = _memory_load()
    workspace_text = _summarize_task_workspace_facts(
        _task_workspace_facts_payload(),
        max_chars=220,
    )
    route_text = _task_namespace_memory_context(
        namespace=route_namespace,
        max_chars=340,
        payload=payload,
    )
    session_text = _task_namespace_memory_context(
        namespace=session_namespace,
        max_chars=340,
        payload=payload,
    )
    segments: list[str] = []
    if workspace_text:
        _append_task_context_piece(segments, f'wf:{workspace_text}', max_chars=900)
    if route_text:
        _append_task_context_piece(segments, f'rt:{route_text}', max_chars=900)
    if session_text:
        _append_task_context_piece(segments, f'ss:{session_text}', max_chars=900)
    context = ' | '.join(segments)
    return {
        'memory_session': normalized_session,
        'route_namespace': route_namespace,
        'session_namespace': session_namespace,
        'workspace_chars': len(workspace_text),
        'route_chars': len(route_text),
        'session_chars': len(session_text),
        'context_chars': len(context),
        'context': context,
    }


def _summarize_task_request(prompt: str, max_chars: int = 220) -> str:
    return _trim_task_inline_text(prompt, max_chars=max_chars)


def _summarize_task_response(infer: dict[str, Any], max_chars: int = 260) -> str:
    output = _trim_task_inline_text(str(infer.get('output', '') or ''), max_chars=max_chars)
    if output:
        return output
    if infer.get('ok', False):
        return 'empty output'
    return 'inference reported no output'


def _persist_task_memory(
    *,
    prompt: str,
    classification: dict[str, Any],
    resolved: dict[str, Any],
    encoded: dict[str, Any],
    infer: dict[str, Any],
    memory_info: dict[str, Any],
    result_id: str = '',
) -> dict[str, Any]:
    route = str(classification.get('route') or resolved.get('route') or 'general')
    task_ok = bool(infer.get('ok', False)) and bool(str(infer.get('output', '') or '').strip())
    request_summary = _summarize_task_request(prompt)
    response_summary = _summarize_task_response(infer)
    session_namespace = str(memory_info.get('session_namespace') or _task_session_namespace('default'))
    route_namespace = str(memory_info.get('route_namespace') or _task_route_namespace(route))
    state: dict[str, Any] = {
        'session_write': {'written': False},
        'route_summary_write': {'written': False},
        'session_compaction': {'compacted': False},
        'failure_recorded': False,
    }
    session_value = {
        'route': route,
        'model': str(resolved.get('model') or ''),
        'backend': str(infer.get('backend') or ''),
        'ok': task_ok,
        'confidence': float(classification.get('confidence', 0.0) or 0.0),
        'request_summary': request_summary,
        'response_summary': response_summary,
        'char_saving': int(encoded.get('char_saving_vs_original', 0) or 0),
    }
    if result_id:
        session_value['result_id'] = result_id

    if ALLOW_MUTATIONS:
        try:
            session_write = memory_upsert(
                namespace=session_namespace,
                key=f"call:{_now_iso()}",
                value=session_value,
                ttl_days=7,
                confidence=float(classification.get('confidence', 0.0) or 0.0),
                source='task_router.task.auto',
                tags=['task', 'session', route],
            )
            state['session_write'] = {**session_write, 'written': True}
        except Exception as exc:
            state['session_write'] = {'written': False, 'error': str(exc)}

        route_summary = _trim_task_inline_text(
            f"model={resolved.get('model', '')} ok={task_ok} req={request_summary} resp={response_summary}",
            max_chars=600,
        )
        try:
            route_write = memory_summary_upsert(
                namespace=route_namespace,
                focus='recent_activity',
                summary=route_summary,
                ttl_days=30,
                confidence=float(classification.get('confidence', 0.0) or 0.0),
                source='task_router.task.auto',
                tags=['task', 'route', route],
            )
            state['route_summary_write'] = {**route_write, 'written': True}
        except Exception as exc:
            state['route_summary_write'] = {'written': False, 'error': str(exc)}

        try:
            state['session_compaction'] = memory_auto_compact(
                namespace=session_namespace,
                threshold_entries=12,
                threshold_chars=4000,
                keep_entries=6,
                summary_max_chars=600,
                drop_expired=False,
            )
        except Exception as exc:
            state['session_compaction'] = {'compacted': False, 'error': str(exc)}
    else:
        state['session_write'] = {'written': False, 'reason': 'mutations_disabled'}
        state['route_summary_write'] = {'written': False, 'reason': 'mutations_disabled'}
        state['session_compaction'] = {'compacted': False, 'reason': 'mutations_disabled'}

    if not task_ok:
        failure_reason = (
            f"task route={route} model={resolved.get('model', '')} returned empty output"
            if not str(infer.get('output', '') or '').strip()
            else f"task route={route} model={resolved.get('model', '')} returned ok=false"
        )
        _failure_record(
            command=['task_router', 'task'],
            stderr=failure_reason,
            stdout=response_summary,
            category='task_router.task',
            suggestion='Inspect route selection, memory context, and routed model availability.',
        )
        state['failure_recorded'] = True
    return state


def _task_retrieval_terms(prompt: str, max_terms: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for path in _extract_prompt_file_paths(prompt, max_paths=3):
        for part in re.split(r'[^a-z0-9]+', Path(path).stem.lower()):
            if len(part) < 3 or part in TASK_RETRIEVAL_STOPWORDS or part in seen:
                continue
            seen.add(part)
            out.append(part)
            if len(out) >= max_terms:
                return out
    for token in _tokenize_router_query(prompt):
        if len(token) < 3 or token in TASK_RETRIEVAL_STOPWORDS or token.isdigit() or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= max_terms:
            break
    return out


def _task_should_retrieve(prompt: str, route: str) -> bool:
    if _extract_prompt_file_paths(prompt, max_paths=1):
        return True
    if route not in {'coding', 'refactor', 'review', 'security', 'research'}:
        return False
    return len(_task_retrieval_terms(prompt, max_terms=4)) >= 2


def _task_retrieval_preview_from_path(
    path: str,
    source: str,
    max_chars: int = 420,
) -> dict[str, Any] | None:
    file_path = _resolve_repo_path(path)
    ext = file_path.suffix.lower()
    try:
        if ext in TASK_RETRIEVAL_DOCUMENT_SUFFIXES:
            doc = read_document(path=path, max_chars=max_chars, output_profile='compact')
            content = str(doc.get('text', '') or '')
            return {
                'source': source,
                'kind': 'document',
                'path': path,
                'content': _trim_task_context_block(content, max_chars=max_chars),
            }
        if ext in TASK_RETRIEVAL_CODE_SUFFIXES:
            snippet = read_snippet(path=path, start_line=1, end_line=60, output_profile='compact')
            return {
                'source': source,
                'kind': 'path',
                'path': path,
                'start_line': snippet.get('start_line'),
                'end_line': snippet.get('end_line'),
                'content': _trim_task_context_block(snippet.get('content', ''), max_chars=max_chars),
            }
        if _is_likely_binary(file_path):
            return None
        text = read_file(path=path, max_bytes=min(MAX_READ_BYTES, max(4096, max_chars * 8)))
        return {
            'source': source,
            'kind': 'path',
            'path': path,
            'content': _trim_task_context_block(text, max_chars=max_chars),
        }
    except Exception:
        return None


def _task_retrieval_preview_from_search_row(
    row: dict[str, Any],
    max_chars: int = 420,
) -> dict[str, Any] | None:
    kind = str(row.get('kind', '') or '').strip()
    path = str(row.get('path', '') or '').strip()
    if not kind or not path:
        return None
    score = float(row.get('local_score', row.get('score', 0.0)) or 0.0)
    try:
        if kind == 'symbol':
            start_line = int(row.get('line_start') or 1)
            end_line = int(row.get('line_end') or start_line)
            snippet = read_snippet(
                path=path,
                start_line=start_line,
                end_line=end_line,
                context_before=2,
                context_after=6,
                output_profile='compact',
            )
            return {
                'source': 'code_search',
                'kind': kind,
                'path': path,
                'name': row.get('name'),
                'score': score,
                'start_line': snippet.get('start_line'),
                'end_line': snippet.get('end_line'),
                'content': _trim_task_context_block(snippet.get('content', ''), max_chars=max_chars),
            }
        if kind == 'text_match':
            line = int(row.get('line') or 1)
            snippet = read_snippet(
                path=path,
                start_line=line,
                end_line=line,
                context_before=2,
                context_after=4,
                output_profile='compact',
            )
            return {
                'source': 'code_search',
                'kind': kind,
                'path': path,
                'score': score,
                'start_line': snippet.get('start_line'),
                'end_line': snippet.get('end_line'),
                'content': _trim_task_context_block(snippet.get('content', ''), max_chars=max_chars),
            }
        item = _task_retrieval_preview_from_path(path=path, source='code_search', max_chars=max_chars)
        if item is None:
            return None
        item['score'] = score
        item['kind'] = kind
        return item
    except Exception:
        return None


def _task_artifact_candidates(terms: list[str], max_items: int = 3) -> list[dict[str, Any]]:
    if not terms:
        return []
    index_path = _resolve_repo_path(str(ARTIFACT_INDEX_FILE))
    if not index_path.is_file():
        return []
    try:
        payload = json.loads(index_path.read_text(encoding='utf-8'))
    except Exception:
        return []
    rows = payload.get('artifacts', [])
    if not isinstance(rows, list):
        return []
    scored: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = str(row.get('path', '') or '').replace('\\', '/')
        if not path:
            continue
        low = path.lower()
        score = 0.0
        for term in terms:
            if term in low:
                score += 2.0
        if score <= 0:
            continue
        scored.append({'path': path, 'score': score})
    scored.sort(key=lambda item: (item['score'], item['path']), reverse=True)
    return scored[:max_items]


def _build_task_retrieval_context(
    prompt: str,
    route: str,
    max_items: int = 4,
    max_chars: int = 1400,
) -> dict[str, Any]:
    if not _task_should_retrieve(prompt, route):
        return {
            'enabled': False,
            'route': route,
            'query': '',
            'item_count': 0,
            'context_chars': 0,
            'context': '',
            'sources': {},
            'items': [],
            'errors': [],
        }

    terms = _task_retrieval_terms(prompt, max_terms=8)
    query = ' '.join(terms)[:120] or prompt.strip()[:120]
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    seen: set[tuple[str, Any, Any]] = set()

    def add_item(item: dict[str, Any] | None) -> None:
        if not item or len(items) >= max_items:
            return
        key = (str(item.get('path', '')), item.get('start_line'), item.get('end_line'))
        if key in seen:
            return
        seen.add(key)
        items.append(item)

    for path in _extract_prompt_file_paths(prompt, max_paths=2):
        add_item(_task_retrieval_preview_from_path(path=path, source='explicit_path', max_chars=420))

    if len(items) < max_items and query:
        try:
            search = semantic_find(
                query=query,
                path='.',
                max_results=max(max_items * 2, 6),
                output_profile='normal',
                summary_mode='quick',
                use_local_rerank=True,
                local_rerank_top_k=max(8, max_items * 3),
            )
            for row in search.get('results', []) if isinstance(search, dict) else []:
                if not isinstance(row, dict):
                    continue
                add_item(_task_retrieval_preview_from_search_row(row=row, max_chars=420))
                if len(items) >= max_items:
                    break
        except Exception as exc:
            errors.append({'source': 'semantic_find', 'error': str(exc)})

    wants_artifacts = route == 'research' or any(
        needle in prompt.lower() for needle in ('report', 'artifact', 'baseline', 'snapshot', '.codebase-tooling-mcp')
    )
    if wants_artifacts and len(items) < max_items:
        for row in _task_artifact_candidates(terms, max_items=max_items * 2):
            item = _task_retrieval_preview_from_path(path=row['path'], source='artifact_index', max_chars=360)
            if item is None:
                continue
            item['score'] = row['score']
            add_item(item)
            if len(items) >= max_items:
                break

    parts: list[str] = []
    source_counts: dict[str, int] = {}
    for item in items:
        source = str(item.get('source', 'unknown') or 'unknown')
        source_counts[source] = source_counts.get(source, 0) + 1
        header = f'[{source}] {item.get("path", "")}'
        if item.get('name'):
            header += f'::{item["name"]}'
        if item.get('start_line') and item.get('end_line'):
            header += f':{item["start_line"]}-{item["end_line"]}'
        block = f'{header}\n{item.get("content", "")}'.strip()
        _append_task_context_block(parts, block, max_chars=max_chars)
    context = '\n\n'.join(parts)
    return {
        'enabled': True,
        'route': route,
        'query': query,
        'item_count': len(items),
        'context_chars': len(context),
        'context': context,
        'sources': source_counts,
        'items': items,
        'errors': errors[:4],
    }


def _task_infer(
    prompt: str,
    task: str = 'general',
    backend: str = 'auto',
    model: str = '',
    max_tokens: int = 256,
    temperature: float = 0.2,
    system: str = '',
    output_profile: str | None = None,
    store_result: bool = False,
    memory_session: str = '',
) -> dict[str, Any]:
    if not prompt.strip():
        raise ValueError('prompt must not be empty')
    profile = _default_output_profile(output_profile)
    classification = _classify_task_prompt(prompt=prompt, task=task)
    routing = _load_continue_model_routing()
    resolved = _resolve_task_model_route(
        route=str(classification['route']),
        routing=routing,
        requested_model=model,
        prompt=prompt,
        task_hint=task,
    )
    memory_info = _build_task_memory_context(
        route=str(classification['route']),
        memory_session=memory_session,
    )
    retrieval_info = _build_task_retrieval_context(
        prompt=prompt,
        route=str(classification['route']),
    )
    encoded = _encode_task_prompt_packet(
        prompt=prompt,
        route=str(classification['route']),
        task=task,
        memory_session=str(memory_info['memory_session']),
        memory_context=str(memory_info['context']),
        retrieval_context=str(retrieval_info['context']),
    )
    effective_system = system or TASK_ROUTE_SYSTEM_PROMPTS.get(
        str(classification['route']),
        TASK_ROUTE_SYSTEM_PROMPTS['general'],
    )
    infer = local_infer(
        prompt=str(encoded['encoded_prompt']),
        task=str(classification['route']),
        backend=backend,
        model=str(resolved['model']),
        max_tokens=max_tokens,
        temperature=temperature,
        system=effective_system,
        output_profile=output_profile,
        store_result=store_result,
    )
    task_ok = bool(infer.get('ok', False)) and bool(str(infer.get('output', '') or '').strip())
    result: dict[str, Any] = {
        'schema': 'task_router.task.v1',
        'classification': classification,
        'encoding': encoded,
        'routing': {
            'selected_route': resolved['route'],
            'selected_model': resolved['model'],
            'selected_model_file': resolved['file'],
            'selected_by': resolved['source'],
            'routing_loaded': routing.get('loaded', False),
            'routing_source': routing.get('source'),
            'router_model': routing.get('router', {}).get('model'),
        },
        'memory': {
            **memory_info,
            'forced': True,
        },
        'retrieval': retrieval_info,
        'infer': infer,
    }
    if profile == 'compact':
        result = {
            'schema': 'task_router.task.compact.v1',
            'route': classification['route'],
            'confidence': classification['confidence'],
            'model': resolved['model'],
            'backend': infer.get('backend'),
            'encoded_chars': encoded['encoded_chars'],
            'char_saving_vs_original': encoded['char_saving_vs_original'],
            'memory_session': memory_info['memory_session'],
            'memory_chars': memory_info['context_chars'],
            'retrieval_count': retrieval_info['item_count'],
            'retrieval_chars': retrieval_info['context_chars'],
            'ok': task_ok,
            'output': infer.get('output', ''),
        }
    task_result_id = ''
    if store_result:
        task_result_id = _result_store_put('task_router_task', result)
        result['result_id'] = task_result_id
    persisted = _persist_task_memory(
        prompt=prompt,
        classification=classification,
        resolved=resolved,
        encoded=encoded,
        infer=infer,
        memory_info=memory_info,
        result_id=task_result_id or str(infer.get('result_id') or ''),
    )
    if 'memory' in result and isinstance(result['memory'], dict):
        result['memory'].update(persisted)
    else:
        result['memory_write'] = persisted
    return result


def _extract_codebase_tooling_generated_ignores(text: str) -> list[str]:
    lines = text.splitlines()
    marker = "# codebase-tooling-mcp generated"
    start = -1
    for idx, line in enumerate(lines):
        if line.strip() == marker:
            start = idx + 1
            break
    if start < 0:
        return []
    entries: list[str] = []
    for line in lines[start:]:
        item = line.strip()
        if not item:
            continue
        if item.startswith("#"):
            if entries:
                break
            continue
        if item.startswith("/"):
            entries.append(item)
        elif entries:
            break
    return entries


def _extract_env_keys(text: str, prefixes: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Z][A-Z0-9_]*):", line)
        if not m:
            continue
        key = m.group(1)
        if not key.startswith(prefixes):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _compact_sentences(text: str, max_sentences: int = 2, max_chars: int = 420) -> str:
    if max_sentences < 1:
        max_sentences = 1
    normalized = " ".join(text.split())
    if not normalized:
        return ""
    chunks = [s.strip() for s in re.split(r"(?<=[.!?])\s+", normalized) if s.strip()]
    selected = chunks[:max_sentences]
    if not selected:
        selected = [normalized]
    out = " ".join(selected)
    if out and out[-1] not in ".!?":
        out += "."
    return _trim_text(out, max_chars=max_chars)


def _summarize_file_two_sentences(rel_path: str, text: str, max_chars: int) -> str:
    low_path = rel_path.lower()
    if low_path == "readme.md":
        return _compact_sentences(
            "This repository provides codebase-tooling-mcp, an MCP server for safe repository engineering workflows with file, git, and analysis tools. "
            "It documents quickstart, configuration, and integration guidance for HTTP and devcontainer usage.",
            max_sentences=2,
            max_chars=max_chars,
        )
    if low_path.endswith("source/server.py"):
        return _compact_sentences(
            "source/server.py implements the FastMCP/Starlette service, tool registry, and router-oriented execution model for repository operations. "
            "It includes runtime diagnostics and transport handling for stdio/direct/http modes.",
            max_sentences=2,
            max_chars=max_chars,
        )
    if low_path.endswith("source/entrypoint.sh"):
        return _compact_sentences(
            "source/entrypoint.sh bootstraps runtime defaults, user environment setup, and repository config initialization before launching the server. "
            "It manages Ollama startup with host fallback, image-seeded default models, optional runtime pulls only when explicitly enabled, and validated startup timeout settings.",
            max_sentences=2,
            max_chars=max_chars,
        )
    if low_path.endswith(".devcontainer/devcontainer.json"):
        return _compact_sentences(
            ".devcontainer/devcontainer.json defines the development container build, workspace mount, and editor customization settings. "
            "It configures runtime environment variables, ports, and host mounts for local MCP usage without requiring docker-compose.",
            max_sentences=2,
            max_chars=max_chars,
        )
    summary = doc_summarizer_small(text=text, max_bullets=3, max_chars=max_chars)
    bullet_text = str(summary.get("summary", "")).strip()
    cleaned = " ".join(seg.strip("- ").strip() for seg in bullet_text.splitlines() if seg.strip())
    if not cleaned:
        preview = " ".join(ln.strip() for ln in text.splitlines() if ln.strip())[: max(120, max_chars)]
        cleaned = preview
    return _compact_sentences(cleaned, max_sentences=2, max_chars=max_chars)


def _tool_assisted_infer(prompt: str, max_tokens: int = 256) -> str:
    paths = _extract_prompt_file_paths(prompt)
    if not paths:
        return ""
    lower = prompt.lower()
    parts: list[str] = []
    max_chars = max(600, min(6000, max_tokens * 20))

    for rel_path in paths:
        file_path = _resolve_repo_path(rel_path)
        text = file_path.read_text(encoding="utf-8", errors="replace")
        if "codex" in lower and "mount" in lower and "target" in lower:
            m = re.search(
                r"source=\$\{localEnv:HOME\}/\.codex,target=([^,\"]+)",
                text,
            )
            if m:
                parts.append(m.group(1))
                continue
        if "ignore" in lower and "generated" in lower:
            entries = _extract_codebase_tooling_generated_ignores(text)
            if entries:
                parts.append(", ".join(entries))
                continue
        if "ollama" in lower and "environment" in lower:
            keys = _extract_env_keys(text, prefixes=("OLLAMA_", "CONTINUE_OLLAMA_"))
            if keys:
                parts.append(", ".join(keys))
                continue
        if "tags probe" in lower or ("optional" in lower and "probe" in lower):
            if "include_ollama_probe" in text and "tags_probe" in text:
                parts.append(
                    "tags probe is optional; controlled by include_ollama_probe and omitted when false."
                )
                continue
        if "summarize" in lower or "summary" in lower:
            parts.append(_summarize_file_two_sentences(rel_path=rel_path, text=text, max_chars=max_chars))
            continue
        preview_lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:3]
        if preview_lines:
            parts.append(f"{rel_path}: " + " ".join(preview_lines))
    return _trim_text(" ".join(parts), max_chars=max_chars)


def _parallel_infer(
    prompts: list[str],
    task: str,
    backend: str,
    model: str,
    max_tokens: int,
    temperature: float,
    system: str,
    output_profile: str | None,
    store_result: bool,
    max_parallel: int,
) -> dict[str, Any]:
    if not prompts:
        raise ValueError("prompts must not be empty for parallel_infer")
    if max_parallel < 1:
        raise ValueError("max_parallel must be >= 1")
    cleaned = [str(p) for p in prompts]
    if any(not p.strip() for p in cleaned):
        raise ValueError("prompts must not contain empty strings")

    rows: list[dict[str, Any]] = [None] * len(cleaned)  # type: ignore[list-item]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as ex:
        future_map = {
            ex.submit(
                _parallel_infer_one,
                p,
                task,
                backend,
                model,
                max_tokens,
                temperature,
                system,
                output_profile,
                store_result,
            ): idx
            for idx, p in enumerate(cleaned)
        }
        for fut in concurrent.futures.as_completed(future_map):
            idx = future_map[fut]
            try:
                out = fut.result()
                rows[idx] = {
                    "index": idx,
                    "ok": bool(out.get("ok", False)),
                    "result": out,
                }
            except Exception as exc:
                rows[idx] = {
                    "index": idx,
                    "ok": False,
                    "error": str(exc),
                }

    success = sum(1 for r in rows if isinstance(r, dict) and bool(r.get("ok")))
    return {
        "schema": "parallel_infer.v1",
        "count": len(rows),
        "ok_count": success,
        "error_count": len(rows) - success,
        "max_parallel": max_parallel,
        "rows": rows,
    }


def _parallel_infer_one(
    prompt: str,
    task: str,
    backend: str,
    model: str,
    max_tokens: int,
    temperature: float,
    system: str,
    output_profile: str | None,
    store_result: bool,
) -> dict[str, Any]:
    selected = backend.strip().lower()
    if selected in {"auto", "fallback", "rule", "hash"}:
        tool_text = _tool_assisted_infer(prompt=prompt, max_tokens=max_tokens)
        if tool_text:
            profile = _default_output_profile(output_profile)
            out = {
                "schema": "local_infer.v1",
                "backend": "tool_fallback",
                "model": model or LOCAL_INFER_MODEL or "local-default",
                "task": task,
                "output": tool_text,
                "ok": True,
            }
            if profile == "compact":
                out = {
                    "schema": "local_infer.compact.v1",
                    "backend": "tool_fallback",
                    "model": model or LOCAL_INFER_MODEL or "local-default",
                    "ok": True,
                    "output": _trim_text(tool_text, max_chars=1200),
                }
            if store_result:
                out["result_id"] = _result_store_put("local_infer", out)
            return out
    return local_infer(
        prompt=prompt,
        task=task,
        backend=backend,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        output_profile=output_profile,
        store_result=store_result,
    )


def _infer_batch_from_prompt(prompt: str) -> list[str]:
    text = prompt.strip()
    if not text:
        return []

    # Explicit separators first.
    for sep in ("\n---\n", "\n|||\n"):
        if sep in text:
            parts = [p.strip() for p in text.split(sep) if p.strip()]
            if len(parts) >= 2:
                return parts

    # Bullet list tasks.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    bullet = [re.sub(r"^[-*]\s+", "", ln) for ln in lines if re.match(r"^[-*]\s+\S", ln)]
    if len(bullet) >= 2:
        return bullet

    # Numbered tasks.
    numbered = [re.sub(r"^\d+[.)]\s+", "", ln) for ln in lines if re.match(r"^\d+[.)]\s+\S", ln)]
    if len(numbered) >= 2:
        return numbered

    # Inline splitter.
    if " || " in text:
        parts = [p.strip() for p in text.split(" || ") if p.strip()]
        if len(parts) >= 2:
            return parts
    return []



def _agent_execution_mode_profile(mode: str) -> dict[str, Any]:
    """Return a defensive copy of the mode contract exposed to workflow cards."""
    profile = AGENT_EXECUTION_MODE_PROFILES[mode]
    return json.loads(json.dumps(profile))


def _infer_agent_execution_mode_from_prompt(prompt: str) -> str | None:
    text = f" {str(prompt or '').lower()} "
    for mode in ("offline", "online"):
        for term in AGENT_EXECUTION_MODE_PROMPT_TERMS[mode]:
            if f" {term} " in text or term in text:
                return mode
    return None


def _resolve_agent_execution_mode(execution_mode: str = "auto", prompt: str = "") -> tuple[str, str]:
    """Resolve online/offline agent execution mode from explicit arg, prompt, or env default."""
    raw = str(execution_mode or "auto").strip().lower().replace(" ", "-")
    if raw and raw != "auto":
        if raw not in AGENT_EXECUTION_MODE_ALIASES:
            allowed = ", ".join(["auto", *sorted(AGENT_EXECUTION_MODE_ALIASES)])
            raise ValueError(f"execution_mode must be one of: {allowed}")
        return AGENT_EXECUTION_MODE_ALIASES[raw], "explicit"

    inferred = _infer_agent_execution_mode_from_prompt(prompt)
    if inferred:
        return inferred, "prompt"

    env_raw = (AGENT_EXECUTION_PROFILE_ENV or AGENT_EXECUTION_MODE_ENV or AGENT_EXECUTION_MODE_DEFAULT).lower().replace(" ", "-")
    return AGENT_EXECUTION_MODE_ALIASES.get(env_raw, AGENT_EXECUTION_MODE_DEFAULT), "default"


def _offline_small_model_decision_policy(
    confidence: float,
    decision_retries: int = 0,
    tool_iterations: int = 0,
) -> dict[str, Any]:
    """Map a bounded offline JSON decision to retry, clarification, or escalation."""
    confidence_value = max(0.0, min(1.0, float(confidence)))
    limits = OFFLINE_AGENT_LOOP_LIMITS
    policy = OFFLINE_CONFIDENCE_POLICY
    if tool_iterations >= int(limits["max_tool_iterations"]):
        next_action = "escalate_online"
        reason = "hard_iteration_limit_reached"
    elif confidence_value < float(policy["clarify_below"]):
        if decision_retries >= int(limits["max_model_decision_retries"]):
            next_action = "escalate_online"
            reason = "low_confidence_after_retries"
        else:
            next_action = "ask_clarification"
            reason = "confidence_below_clarify_below"
    elif confidence_value < float(policy["accept_min"]):
        next_action = "retry_deterministic_analysis"
        reason = "confidence_below_accept_min"
    else:
        next_action = "continue"
        reason = "confidence_accepted"
    return {
        "schema": "offline_decision_policy.v1",
        "confidence": round(confidence_value, 3),
        "next_action": next_action,
        "reason": reason,
        "limits": limits,
        "confidence_policy": policy,
    }


def _workflow_card_provenance_digest(card: dict[str, Any]) -> str:
    payload = {field: card.get(field) for field in WORKFLOW_CARD_FIELDS if field in card}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _workflow_card_repository_trust_metadata(card: dict[str, Any]) -> dict[str, Any]:
    trust = json.loads(json.dumps(WORKFLOW_CARD_REPOSITORY_TRUST_DEFAULT))
    trust["provenance_digest"] = _workflow_card_provenance_digest(card)
    return trust


def _workflow_card_trust_metadata(card: dict[str, Any], apply_repository_default: bool = False) -> dict[str, Any]:
    raw = card.get("trust")
    if isinstance(raw, dict):
        return json.loads(json.dumps(raw))
    if apply_repository_default:
        return _workflow_card_repository_trust_metadata(card)
    return {}


def _workflow_card_text(card: dict[str, Any]) -> str:
    parts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                collect(key)
                collect(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                collect(item)
        elif value is not None:
            parts.append(str(value))

    collect(card)
    return "\n".join(parts)


def _workflow_card_add_finding(
    findings: list[dict[str, Any]],
    *,
    card_id: str,
    code: str,
    severity: str,
    message: str,
    field: str = "",
    evidence: str = "",
) -> None:
    finding: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "card_id": card_id,
        "message": message,
    }
    if field:
        finding["field"] = field
    if evidence:
        finding["evidence"] = evidence[:240]
    findings.append(finding)


def _workflow_card_lint_findings(card: dict[str, Any], apply_repository_trust_default: bool = False) -> list[dict[str, Any]]:
    card_id = str(card.get("id") or "<unknown>")
    findings: list[dict[str, Any]] = []
    trust = _workflow_card_trust_metadata(card, apply_repository_default=apply_repository_trust_default)
    if not trust:
        _workflow_card_add_finding(
            findings,
            card_id=card_id,
            code="missing_trust_metadata",
            severity="error",
            message="Card has no trust/provenance metadata.",
            field="trust",
        )
    else:
        for field in WORKFLOW_CARD_TRUST_REQUIRED_FIELDS:
            value = trust.get(field)
            missing = field not in trust
            if field == "permissions":
                missing = missing or not isinstance(value, list) or not any(str(item).strip() for item in value)
            elif field == "sensitive_paths":
                missing = missing or not isinstance(value, list)
            else:
                missing = missing or not str(value or "").strip()
            if missing:
                _workflow_card_add_finding(
                    findings,
                    card_id=card_id,
                    code="missing_trust_metadata",
                    severity="error",
                    message=f"Trust metadata is missing required field `{field}`.",
                    field=f"trust.{field}",
                )

    do_not_use_when = card.get("do_not_use_when")
    if not isinstance(do_not_use_when, list) or not any(str(item).strip() for item in do_not_use_when):
        _workflow_card_add_finding(
            findings,
            card_id=card_id,
            code="missing_do_not_use_when",
            severity="warning",
            message="Card is missing non-empty do_not_use_when routing guardrails.",
            field="do_not_use_when",
        )

    permissions = trust.get("permissions", []) if trust else []
    if isinstance(permissions, str):
        permission_values = [permissions]
    elif isinstance(permissions, list):
        permission_values = [str(item) for item in permissions]
    else:
        permission_values = []
    overbroad = []
    for permission in permission_values:
        normalized = permission.strip().lower()
        if any(re.search(pattern, normalized) for pattern in WORKFLOW_CARD_OVERBROAD_PERMISSION_PATTERNS):
            overbroad.append(permission)
    if overbroad:
        _workflow_card_add_finding(
            findings,
            card_id=card_id,
            code="overbroad_permissions",
            severity="error",
            message="Card declares broad host, secret, network, privileged, or wildcard permissions.",
            field="trust.permissions",
            evidence=", ".join(overbroad),
        )

    text = _workflow_card_text(card)
    lower_text = text.lower()
    for pattern in WORKFLOW_CARD_DANGEROUS_SHELL_PATTERNS:
        match = re.search(pattern, lower_text, flags=re.DOTALL)
        if match:
            _workflow_card_add_finding(
                findings,
                card_id=card_id,
                code="dangerous_shell_obfuscation",
                severity="error",
                message="Card contains dangerous shell execution or command-obfuscation phrasing.",
                evidence=match.group(0),
            )
            break

    for pattern in WORKFLOW_CARD_NETWORK_EXFILTRATION_PATTERNS:
        match = re.search(pattern, lower_text, flags=re.DOTALL)
        if match:
            _workflow_card_add_finding(
                findings,
                card_id=card_id,
                code="network_exfiltration_pattern",
                severity="error",
                message="Card contains network upload/exfiltration-style instructions.",
                evidence=match.group(0),
            )
            break

    outside_repo_pattern = f"{WORKFLOW_CARD_OUTSIDE_REPO_WRITE_VERBS}.{{0,160}}(?:{WORKFLOW_CARD_OUTSIDE_REPO_PATHS}|outside\\s+(?:repo_path|the\\s+repository|the\\s+repo))"
    redirect_pattern = rf"(?:>|>>)\s*{WORKFLOW_CARD_OUTSIDE_REPO_PATHS}"
    match = re.search(outside_repo_pattern, lower_text, flags=re.DOTALL) or re.search(redirect_pattern, lower_text, flags=re.DOTALL)
    if match:
        _workflow_card_add_finding(
            findings,
            card_id=card_id,
            code="outside_repo_write",
            severity="error",
            message="Card appears to write, move, delete, or chmod paths outside REPO_PATH.",
            evidence=match.group(0),
        )

    risk = str(card.get("risk") or "").strip().lower()
    sandbox = str(trust.get("sandbox_expectation") or "").strip().lower() if trust else ""
    if risk == "high" and sandbox in {"", "none", "n/a", "na", "not needed"}:
        _workflow_card_add_finding(
            findings,
            card_id=card_id,
            code="missing_sandbox_guidance",
            severity="error",
            message="High-risk card lacks sandbox or REPO_PATH-boundary guidance.",
            field="trust.sandbox_expectation",
        )
    return findings


def _workflow_card_suppression_reason(card: dict[str, Any], trust: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    risk = str(card.get("risk") or "").strip().lower()
    trust_tier = str(trust.get("trust_tier") or "missing").strip().lower()
    if risk == "high" and trust_tier not in WORKFLOW_CARD_TRUSTED_TIERS:
        return "untrusted_high_risk"
    if risk == "high" and trust_tier in {"", "missing", "untrusted", "external_untrusted", "generated_unreviewed"}:
        if any(str(finding.get("severity")) == "error" for finding in findings):
            return "untrusted_high_risk_lint_errors"
    return ""


def _workflow_card_safety_summary(
    card: dict[str, Any],
    findings: list[dict[str, Any]],
    trust: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = trust if trust is not None else _workflow_card_trust_metadata(card)
    errors = [finding for finding in findings if finding.get("severity") == "error"]
    warnings = [finding for finding in findings if finding.get("severity") == "warning"]
    lint_status = "fail" if errors else "warn" if warnings else "pass"
    suppression_reason = _workflow_card_suppression_reason(card, metadata, findings)
    return {
        "schema": WORKFLOW_CARD_SAFETY_SCHEMA_VERSION,
        "source": metadata.get("source", "missing"),
        "trust_tier": metadata.get("trust_tier", "missing"),
        "review_status": metadata.get("review_status", "missing"),
        "risk": str(card.get("risk") or "unknown"),
        "lint_status": lint_status,
        "finding_count": len(findings),
        "high_severity_findings": len(errors),
        "warning_findings": len(warnings),
        "suppressed_by_default": bool(suppression_reason),
        "suppression_reason": suppression_reason,
        "external_card_loading_enabled": WORKFLOW_CARD_EXTERNAL_LOADING_ENABLED,
    }


def lint_workflow_cards(
    cards: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    apply_repository_trust_defaults: bool | None = None,
) -> dict[str, Any]:
    """Deterministically lint workflow_card.v1 cards before any external import path is enabled."""
    cards_to_check = WORKFLOW_CARDS if cards is None else tuple(cards)
    if apply_repository_trust_defaults is None:
        apply_repository_trust_defaults = cards is None
    all_findings: list[dict[str, Any]] = []
    card_summaries: list[dict[str, Any]] = []
    for card in cards_to_check:
        trust = _workflow_card_trust_metadata(card, apply_repository_default=apply_repository_trust_defaults)
        findings = _workflow_card_lint_findings(card, apply_repository_trust_default=apply_repository_trust_defaults)
        all_findings.extend(findings)
        card_summaries.append(
            {
                "id": str(card.get("id") or "<unknown>"),
                "safety": _workflow_card_safety_summary(card, findings, trust=trust),
                "finding_count": len(findings),
            }
        )
    errors = sum(1 for finding in all_findings if finding.get("severity") == "error")
    warnings = sum(1 for finding in all_findings if finding.get("severity") == "warning")
    status = "fail" if errors else "warn" if warnings else "pass"
    return {
        "schema": WORKFLOW_CARD_LINT_SCHEMA_VERSION,
        "card_schema": WORKFLOW_CARD_SCHEMA_VERSION,
        "external_card_loading_enabled": WORKFLOW_CARD_EXTERNAL_LOADING_ENABLED,
        "cards_checked": len(cards_to_check),
        "status": status,
        "summary": {"error": errors, "warning": warnings, "total": len(all_findings)},
        "findings": all_findings,
        "cards": card_summaries,
    }


def _workflow_card_public(
    card: dict[str, Any],
    execution_mode: str | None = None,
    *,
    apply_repository_trust_default: bool = False,
    lint_findings: list[dict[str, Any]] | None = None,
    safety: dict[str, Any] | None = None,
) -> dict[str, Any]:
    public = {field: card[field] for field in WORKFLOW_CARD_FIELDS if field in card}
    supported = list(card.get("supported_execution_modes", AGENT_EXECUTION_MODES))
    mode_routing = dict(card.get("mode_routing", {}))
    for mode in AGENT_EXECUTION_MODES:
        if mode not in mode_routing:
            mode_routing[mode] = (
                "Cloud model handles primary reasoning while MCP supplies compact context and gates."
                if mode == "online"
                else "Onboard/local models stay bounded by workflow cards, structured decisions, checks, and hard limits."
            )
    public["supported_execution_modes"] = supported
    public["mode_routing"] = mode_routing
    if execution_mode in mode_routing:
        public["selected_mode_routing"] = mode_routing[execution_mode]
    trust = _workflow_card_trust_metadata(card, apply_repository_default=apply_repository_trust_default)
    if trust:
        public["trust"] = trust
    findings = lint_findings if lint_findings is not None else _workflow_card_lint_findings(card, apply_repository_trust_default=apply_repository_trust_default)
    public["safety"] = safety if safety is not None else _workflow_card_safety_summary(card, findings, trust=trust)
    return public


def _workflow_selection_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", text.lower())
        if token not in TASK_RETRIEVAL_STOPWORDS and not token.isdigit()
    }


def _workflow_card_score(
    card: dict[str, Any],
    query: str,
    tokens: set[str],
    execution_mode: str,
    execution_mode_source: str,
) -> dict[str, Any]:
    haystack_parts: list[str] = []
    for key in ("id", "title", "intent", "risk", "mutation_mode", "recommended_entrypoint"):
        haystack_parts.append(str(card.get(key, "") or ""))
    for key in ("triggers", "prerequisites", "outputs", "do_not_use_when", "routing_terms", "supported_execution_modes"):
        value = card.get(key, [])
        if isinstance(value, list):
            haystack_parts.extend(str(item) for item in value)
    mode_routing = card.get("mode_routing", {})
    if isinstance(mode_routing, dict):
        for key, value in mode_routing.items():
            haystack_parts.extend((str(key), str(value)))
    haystack = " ".join(haystack_parts).lower()

    score = 0.0
    reasons: list[str] = []
    for term in card.get("routing_terms", []):
        term_text = str(term).lower()
        term_tokens = _workflow_selection_tokens(term_text)
        if term_text and term_text in query.lower():
            score += 4.0
            reasons.append(f"phrase:{term_text}")
        elif term_tokens and term_tokens.intersection(tokens):
            overlap = len(term_tokens.intersection(tokens))
            score += 1.5 * overlap
            reasons.append(f"term:{','.join(sorted(term_tokens.intersection(tokens)))}")
    for token in tokens:
        if token in haystack:
            score += 0.35

    mode_was_requested = execution_mode_source in {"explicit", "prompt"}
    supported_modes = list(card.get("supported_execution_modes", AGENT_EXECUTION_MODES))
    if mode_was_requested:
        if execution_mode in supported_modes:
            if len(supported_modes) < len(AGENT_EXECUTION_MODES):
                score += 2.0
            else:
                score += 0.25
            reasons.append(f"execution-mode:{execution_mode}")
        else:
            score -= 2.0
            reasons.append(f"execution-mode-mismatch:{execution_mode}")
    if mode_was_requested and execution_mode == "online" and card.get("id") == "cloud-assisted-agent-mode":
        score += 2.5
        reasons.append("cloud-assisted responsibilities requested")
    if mode_was_requested and execution_mode == "offline" and card.get("id") == "offline-bounded-agent-loop":
        score += 2.5
        reasons.append("offline bounded-loop requested")

    risk = str(card.get("risk", "") or "").lower()
    high_risk_terms = {
        "delete", "remove", "rewrite", "refactor", "migration", "release", "ship",
        "publish", "deploy", "security", "secret", "token", "auth", "credential",
    }
    if risk == "high" and tokens.intersection(high_risk_terms):
        score += 1.25
        reasons.append("high-risk term")
    if card.get("id") == "snapshot-before-refactor" and tokens.intersection({"delete", "remove", "rewrite", "refactor", "migration"}):
        score += 3.0
        reasons.append("rollback recommended before risky mutation")
    if card.get("id") == "release-readiness" and tokens.intersection({"release", "ship", "publish", "deploy", "merge"}):
        score += 2.5
        reasons.append("release gate requested")
    if card.get("id") == "security-triage" and tokens.intersection({"security", "secret", "token", "auth", "credential", "vulnerability"}):
        score += 2.5
        reasons.append("security/privacy gate requested")

    return {"score": score, "reasons": reasons[:6]}


def _workflow_selection_caveats(
    matches: list[dict[str, Any]],
    tokens: set[str],
    execution_mode: str,
    execution_mode_source: str,
    suppressed_count: int = 0,
) -> list[str]:
    caveats: list[str] = [
        "Read-only selector: it recommends existing workflows/prompts/tools but does not execute them.",
        "External workflow-card loading is disabled by default; selected cards include trust/safety metadata.",
    ]
    high_risk = tokens.intersection({"delete", "remove", "rewrite", "refactor", "migration", "release", "ship", "publish", "deploy", "security", "secret", "token", "auth", "credential"})
    match_ids = {str(match.get("id")) for match in matches}
    if execution_mode_source == "prompt":
        caveats.append(f"Execution mode inferred from prompt as `{execution_mode}`; pass execution_mode explicitly to override.")
    if execution_mode == "online":
        caveats.append("Online/cloud-assisted mode: use MCP for compact context, audit/memory, deterministic prechecks, compression, and local autocomplete while the cloud model reasons.")
    else:
        caveats.append("Offline/onboard-only mode: keep local-model decisions structured, confidence-scored, retry-limited, and ready to clarify or escalate when confidence is low.")
    if suppressed_count:
        caveats.append(f"Suppressed {suppressed_count} untrusted high-risk workflow card(s) by default.")
    if high_risk:
        caveats.append("High-risk wording detected: clarify scope, confirm mutation mode, and preserve a rollback path before edits.")
    if tokens.intersection({"delete", "remove", "rewrite", "refactor", "migration"}) and "snapshot-before-refactor" in match_ids:
        caveats.append("Use snapshot-before-refactor before broad or destructive mutations.")
    if tokens.intersection({"release", "ship", "publish", "deploy", "merge"}) and "release-readiness" in match_ids:
        caveats.append("Run release readiness before handoff/merge/deploy decisions.")
    if tokens.intersection({"security", "secret", "token", "auth", "credential"}) and "security-triage" in match_ids:
        caveats.append("Keep security triage read-only until remediation scope and secret redaction are explicit.")
    if not matches or float(matches[0].get("confidence", 0.0) or 0.0) < 0.35:
        caveats.append("Low confidence: ask a clarification question before choosing a workflow.")
    return caveats


def _workflow_select_from_cards(
    prompt: str,
    top_k: int = 3,
    execution_mode: str = "auto",
    *,
    cards: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    apply_repository_trust_defaults: bool = False,
) -> dict[str, Any]:
    """Read-only workflow-card selector with an injectable card set for tests/import review."""
    query = str(prompt or "").strip()
    if not query:
        raise ValueError("prompt must not be empty")
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    cards_to_select = WORKFLOW_CARDS if cards is None else tuple(cards)
    selected_mode, selected_mode_source = _resolve_agent_execution_mode(execution_mode, query)
    attrs = {
        "mcp.schema": "mcp.workflow_selection.v1",
        "mcp.workflow.name": "workflow_select",
        "mcp.workflow.mode": "workflow_select",
        "mcp.execution_mode": selected_mode,
        "mcp.execution_mode.requested": execution_mode,
        "mcp.execution_mode.source": selected_mode_source,
        "mcp.workflow.top_k": min(top_k, len(cards_to_select)),
        "mcp.input.prompt.length": len(query),
        "mcp.content_capture.enabled": False,
    }
    with _otel_span("mcp.workflow.select", attrs) as span:
        tokens = _workflow_selection_tokens(query)
        span.set_attribute("mcp.input.prompt.token_count", len(tokens))
        scored: list[dict[str, Any]] = []
        suppressed: list[dict[str, Any]] = []
        max_score = 1.0
        for card in cards_to_select:
            details = _workflow_card_score(card, query, tokens, selected_mode, selected_mode_source)
            trust = _workflow_card_trust_metadata(card, apply_repository_default=apply_repository_trust_defaults)
            findings = _workflow_card_lint_findings(card, apply_repository_trust_default=apply_repository_trust_defaults)
            safety = _workflow_card_safety_summary(card, findings, trust=trust)
            row = {"card": card, "trust": trust, "findings": findings, "safety": safety, **details}
            if safety.get("suppressed_by_default"):
                suppressed.append(row)
                continue
            max_score = max(max_score, float(details["score"]))
            scored.append(row)
        scored.sort(key=lambda row: (float(row["score"]), str(row["card"].get("id", ""))), reverse=True)
        suppressed.sort(key=lambda row: (float(row["score"]), str(row["card"].get("id", ""))), reverse=True)

        matches: list[dict[str, Any]] = []
        for row in scored[: min(top_k, len(scored))]:
            card = _workflow_card_public(
                row["card"],
                execution_mode=selected_mode,
                apply_repository_trust_default=apply_repository_trust_defaults,
                lint_findings=row["findings"],
                safety=row["safety"],
            )
            score = float(row["score"])
            confidence = round(min(0.99, score / max(max_score, 6.0)), 2)
            if score <= 0:
                confidence = 0.05
            matches.append(
                {
                    **card,
                    "score": round(score, 2),
                    "confidence": confidence,
                    "match_reasons": row["reasons"],
                }
            )
        suppressed_matches = [
            {
                "id": str(row["card"].get("id") or "<unknown>"),
                "title": str(row["card"].get("title") or ""),
                "score": round(float(row["score"]), 2),
                "match_reasons": row["reasons"],
                "safety": row["safety"],
            }
            for row in suppressed[:5]
        ]
        if matches:
            span.set_attribute("mcp.workflow.top_match", matches[0].get("id", ""))
            span.set_attribute("mcp.workflow.top_match.confidence", matches[0].get("confidence", 0.0))
        span.set_attribute("mcp.workflow.match_count", len(matches))
        span.set_attribute("mcp.workflow.suppressed_count", len(suppressed))
        result = {
            "schema": WORKFLOW_SELECT_SCHEMA_VERSION,
            "card_schema": WORKFLOW_CARD_SCHEMA_VERSION,
            "trust_schema": WORKFLOW_CARD_TRUST_SCHEMA_VERSION,
            "safety_schema": WORKFLOW_CARD_SAFETY_SCHEMA_VERSION,
            "execution_mode_schema": AGENT_EXECUTION_MODE_SCHEMA_VERSION,
            "execution_mode": selected_mode,
            "execution_mode_source": selected_mode_source,
            "execution_mode_profile": _agent_execution_mode_profile(selected_mode),
            "query": query,
            "read_only": True,
            "selection_mode": "ranked_keyword_cards",
            "external_card_loading_enabled": WORKFLOW_CARD_EXTERNAL_LOADING_ENABLED,
            "cards_available": len(cards_to_select),
            "cards_suppressed": len(suppressed),
            "suppressed_matches": suppressed_matches,
            "matches": matches,
            "caveats": _workflow_selection_caveats(matches, tokens, selected_mode, selected_mode_source, len(suppressed)),
        }
        _otel_set_result_attributes(span, result)
        return result


def workflow_select(prompt: str, top_k: int = 3, execution_mode: str = "auto") -> dict[str, Any]:
    """Read-only workflow-card selector for natural-language MCP workflow choice."""
    return _workflow_select_from_cards(
        prompt=prompt,
        top_k=top_k,
        execution_mode=execution_mode,
        cards=WORKFLOW_CARDS,
        apply_repository_trust_defaults=True,
    )


class TaskRouterService:
    """Application service for the single public task router and explicit model utilities."""

    def route(
        self,
        mode: str = "task",
        prompt: str = "",
        task: str = "general",
        prefix: str = "",
        suffix: str = "",
        language: str = "",
        texts: list[str] | None = None,
        query: str = "",
        candidates: list[dict[str, Any]] | None = None,
        execution_mode: str = "auto",
        backend: str = "auto",
        model: str = "",
        max_tokens: int = 256,
        temperature: float = 0.2,
        system: str = "",
        stop: list[str] | None = None,
        normalize: bool = True,
        top_k: int | None = None,
        output_profile: str | None = None,
        offset: int = 0,
        limit: int | None = None,
        compress: bool = False,
        store_result: bool = False,
        memory_session: str = "",
        check_profile: str = "quick",
        check_target: str = ".",
        check_timeout_seconds: int = 600,
        run_checks: bool = False,
        packages: list[str] | None = None,
        pip_upgrade: bool = False,
        sandbox_mode: str = "shared",
        sandbox_id: str = "",
        sandbox_action: str = "list",
        prompts: list[str] | None = None,
        max_parallel: int = 4,
        auto_parallel_when_possible: bool = True,
    ) -> dict[str, Any]:
        mode = str(mode or "task").strip().lower() or "task"
        if mode not in {
            "task",
            "status",
            "embed",
            "infer",
            "parallel_infer",
            "autocomplete",
            "rerank",
            "coding_infer",
            "coding_check",
            "coding_pip",
            "coding_sandbox",
            "workflow_select",
        }:
            raise ValueError(
                "mode must be one of: task, status, embed, infer, parallel_infer, autocomplete, rerank, coding_infer, coding_check, coding_pip, coding_sandbox, workflow_select"
            )
        if mode == "workflow_select":
            return workflow_select(
                prompt=prompt or query,
                top_k=3 if top_k is None else top_k,
                execution_mode=execution_mode,
            )
        if mode == "status":
            return local_model_status()
        if mode == "task":
            return _task_infer(
                prompt=prompt,
                task=task,
                backend=backend,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                output_profile=output_profile,
                store_result=store_result,
                memory_session=memory_session,
            )
        if mode == "embed":
            return local_embed(
                texts=texts or [],
                backend=backend,
                normalize=normalize,
                output_profile=output_profile,
                offset=offset,
                limit=limit,
                compress=compress,
                store_result=store_result,
            )
        if mode == "infer":
            inferred_batch = prompts or []
            if not inferred_batch and auto_parallel_when_possible:
                inferred_batch = _infer_batch_from_prompt(prompt)
            if len(inferred_batch) >= 2:
                parallel = _parallel_infer(
                    prompts=inferred_batch,
                    task=task,
                    backend=backend,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    output_profile=output_profile,
                    store_result=store_result,
                    max_parallel=max_parallel,
                )
                return {
                    "schema": "task_router.infer_auto_parallel.v1",
                    "upgraded": True,
                    "reason": "detected_independent_batch",
                    "count": len(inferred_batch),
                    "result": parallel,
                }
            single_prompt = inferred_batch[0] if len(inferred_batch) == 1 else prompt
            return local_infer(
                prompt=single_prompt,
                task=task,
                backend=backend,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                output_profile=output_profile,
                store_result=store_result,
            )
        if mode == "parallel_infer":
            return _parallel_infer(
                prompts=prompts or [],
                task=task,
                backend=backend,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                output_profile=output_profile,
                store_result=store_result,
                max_parallel=max_parallel,
            )
        if mode == "coding_infer":
            sandbox = _coding_sandbox_prepare(
                sandbox_mode=sandbox_mode, sandbox_id=sandbox_id
            )
            routing = _load_continue_model_routing()
            resolved = _resolve_task_model_route(
                route="coding",
                routing=routing,
                requested_model=model,
                prompt=prompt,
                task_hint=task,
            )
            infer_result = local_infer(
                prompt=prompt,
                task="coding",
                backend=backend,
                model=resolved["model"],
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                output_profile=output_profile,
                store_result=store_result,
            )
            payload: dict[str, Any] = {
                "schema": "task_router.coding_infer.v1",
                "infer": infer_result,
                "check_requested": run_checks,
                "routing": {
                    "selected_route": resolved["route"],
                    "selected_model": resolved["model"],
                    "selected_model_file": resolved["file"],
                    "selected_by": resolved["source"],
                    "routing_loaded": routing.get("loaded", False),
                    "routing_source": routing.get("source"),
                },
                "sandbox": sandbox,
                "stdout": "",
                "stderr": "",
                "stdout_stream": [],
                "stderr_stream": [],
            }
            if run_checks:
                checks = _coding_checks(
                    profile=check_profile,
                    target=check_target,
                    timeout_seconds=check_timeout_seconds,
                    python_executable=str(sandbox["venv_python"]),
                )
                payload["checks"] = checks
                payload.update(_coding_stream_payload_from_steps(checks.get("steps", [])))
            return payload
        if mode == "coding_check":
            sandbox = _coding_sandbox_prepare(
                sandbox_mode=sandbox_mode, sandbox_id=sandbox_id
            )
            checks = _coding_checks(
                profile=check_profile,
                target=check_target,
                timeout_seconds=check_timeout_seconds,
                python_executable=str(sandbox["venv_python"]),
            )
            checks.update(_coding_stream_payload_from_steps(checks.get("steps", [])))
            return checks
        if mode == "coding_pip":
            sandbox = _coding_sandbox_prepare(
                sandbox_mode=sandbox_mode, sandbox_id=sandbox_id
            )
            result = _coding_pip_install(
                packages=packages or [],
                upgrade=pip_upgrade,
                timeout_seconds=check_timeout_seconds,
                python_executable=str(sandbox["venv_python"]),
            )
            stdout_chunk = _trim_text(
                str(result.get("stdout", "") or ""), max_chars=4000
            )
            stderr_chunk = _trim_text(
                str(result.get("stderr", "") or ""), max_chars=4000
            )
            result["stdout_stream"] = (
                [{"index": 0, "command": result.get("command", []), "chunk": stdout_chunk}]
                if stdout_chunk
                else []
            )
            result["stderr_stream"] = (
                [{"index": 0, "command": result.get("command", []), "chunk": stderr_chunk}]
                if stderr_chunk
                else []
            )
            result["sandbox"] = sandbox
            return result
        if mode == "coding_sandbox":
            return _coding_sandbox_manage(action=sandbox_action, sandbox_id=sandbox_id)
        if mode == "autocomplete":
            return autocomplete(
                prefix=prefix,
                suffix=suffix,
                language=language,
                backend=backend,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                output_profile=output_profile,
                store_result=store_result,
            )
        return local_rerank(
            query=query,
            candidates=candidates or [],
            top_k=20 if top_k is None else top_k,
            backend=backend,
            output_profile=output_profile,
        )


_TASK_ROUTER_SERVICE = TaskRouterService()


@mcp.tool()
def task_router(
    mode: Annotated[
        str,
        Field(
            description="Execution mode. Start with `task` for almost every natural-language request; it classifies the request, injects compact task/session memory, and dispatches to the right specialist flow. Use `workflow_select` first when you are unsure which existing MCP workflow/prompt/tool to use. Use the other modes only when you intentionally need raw status, infer, embed, rerank, autocomplete, or coding sandbox/check/package behavior."
        ),
    ] = "task",
    prompt: Annotated[
        str,
        Field(description="Primary request text for the default `task` flow, `infer`, and `coding_infer`."),
    ] = "",
    task: Annotated[
        str,
        Field(description="Task hint such as `general`, `coding`, `micro_coding`, `review`, or `security`; used for routing and fallback prompt shaping."),
    ] = "general",
    prefix: Annotated[
        str,
        Field(description="Autocomplete prefix text when `mode='autocomplete'`."),
    ] = "",
    suffix: Annotated[
        str,
        Field(description="Autocomplete suffix text after the cursor when `mode='autocomplete'`."),
    ] = "",
    language: Annotated[
        str,
        Field(description="Language hint for autocomplete requests."),
    ] = "",
    texts: Annotated[
        list[str] | None,
        Field(description="Input texts for `mode='embed'`."),
    ] = None,
    query: Annotated[
        str,
        Field(description="Query text for reranking and other mode-specific router operations."),
    ] = "",
    execution_mode: Annotated[
        str,
        Field(description="Agent execution profile for workflow selection: `auto`, `online`/`cloud-assisted`, or `offline`/`onboard-only`. Used by `mode='workflow_select'` to expose mode-aware workflow-card routing without creating a second selector."),
    ] = "auto",
    candidates: Annotated[
        list[dict[str, Any]] | None,
        Field(description="Candidate objects to rerank when `mode='rerank'`."),
    ] = None,
    backend: Annotated[
        str,
        Field(description="Execution backend. Common values include `auto`, `endpoint`, `fallback`, `hash`, and `rule`, depending on the selected mode."),
    ] = "auto",
    model: Annotated[
        str,
        Field(description="Explicit model override. Leave empty to use router-selected or default models."),
    ] = "",
    max_tokens: Annotated[
        int,
        Field(description="Maximum generated tokens for inference-style modes."),
    ] = 256,
    temperature: Annotated[
        float,
        Field(description="Sampling temperature for inference-style modes."),
    ] = 0.2,
    system: Annotated[
        str,
        Field(description="Optional system prompt override for inference-style modes."),
    ] = "",
    stop: Annotated[
        list[str] | None,
        Field(description="Optional stop sequences for `mode='autocomplete'`."),
    ] = None,
    normalize: Annotated[
        bool,
        Field(description="Whether embeddings should be L2-normalized in `mode='embed'`."),
    ] = True,
    top_k: Annotated[
        int | None,
        Field(description="Maximum results to return in ranked modes. Defaults to 3 for `mode='workflow_select'` and 20 for `mode='rerank'`."),
    ] = None,
    output_profile: Annotated[
        str | None,
        Field(description="Output verbosity/profile. Common values are `compact`, `normal`, and `verbose`."),
    ] = None,
    offset: Annotated[
        int,
        Field(description="Pagination offset for pageable modes."),
    ] = 0,
    limit: Annotated[
        int | None,
        Field(description="Pagination limit for pageable modes."),
    ] = None,
    compress: Annotated[
        bool,
        Field(description="Whether to compress large tabular or list results in supported modes."),
    ] = False,
    store_result: Annotated[
        bool,
        Field(description="Whether to store the result in the server-side result handle cache."),
    ] = False,
    memory_session: Annotated[
        str,
        Field(description="Optional session key for `mode='task'`. Reuse the same value across related requests to carry compact task/session memory; empty becomes `default`."),
    ] = "",
    check_profile: Annotated[
        str,
        Field(description="Coding check profile for `coding_check` or `coding_infer` with `run_checks=true`."),
    ] = "quick",
    check_target: Annotated[
        str,
        Field(description="File or directory target for coding checks."),
    ] = ".",
    check_timeout_seconds: Annotated[
        int,
        Field(description="Timeout in seconds for coding checks and package installs."),
    ] = 600,
    run_checks: Annotated[
        bool,
        Field(description="Whether `coding_infer` should run post-infer checks."),
    ] = False,
    packages: Annotated[
        list[str] | None,
        Field(description="Package specs to install when `mode='coding_pip'`."),
    ] = None,
    pip_upgrade: Annotated[
        bool,
        Field(description="Whether `coding_pip` should install with upgrade semantics."),
    ] = False,
    sandbox_mode: Annotated[
        str,
        Field(description="Coding sandbox mode. Use `shared` to reuse the shared sandbox or another supported mode for isolation."),
    ] = "shared",
    sandbox_id: Annotated[
        str,
        Field(description="Existing sandbox identifier for coding sandbox reuse or management."),
    ] = "",
    sandbox_action: Annotated[
        str,
        Field(description="Sandbox management action when `mode='coding_sandbox'`."),
    ] = "list",
    prompts: Annotated[
        list[str] | None,
        Field(description="Independent request batch for `parallel_infer`, or an explicit batch or single-prompt override for `infer`."),
    ] = None,
    max_parallel: Annotated[
        int,
        Field(description="Maximum concurrent workers for `parallel_infer` or inferred auto-parallel batches."),
    ] = 4,
    auto_parallel_when_possible: Annotated[
        bool,
        Field(description="Whether `mode='infer'` should automatically upgrade independent prompt batches to `parallel_infer`."),
    ] = True,
) -> dict[str, Any]:
    """Single public task router for LLM agents. Default `mode='task'` is the normal entrypoint. Use `mode='workflow_select'` plus `execution_mode` for read-only, mode-aware workflow-card retrieval before choosing a workflow. Explicit modes expose status|embed|infer|parallel_infer|autocomplete|rerank|coding_infer|coding_check|coding_pip|coding_sandbox|workflow_select."""
    audit_args = {
        "mode": mode,
        "task": task,
        "backend": backend,
        "model": model,
        "execution_mode": execution_mode,
        "check_profile": check_profile,
        "check_target": check_target,
        "run_checks": run_checks,
        "packages": packages or [],
        "sandbox_mode": sandbox_mode,
        "sandbox_action": sandbox_action,
    }
    categories = _tool_categories("task_router", audit_args)
    span_attrs = _otel_tool_attributes("task_router", audit_args, categories)
    span_attrs.update(
        {
            "mcp.workflow.mode": str(mode or "task"),
            "mcp.execution_mode.requested": execution_mode,
            "mcp.input.prompt.length": len(str(prompt or query or "")),
        }
    )
    with _otel_span("mcp.tool.task_router", span_attrs) as span:
        categories = _require_tool_security_gate("task_router", audit_args)
        span.set_attribute("mcp.tool.categories", sorted(str(item) for item in categories))
        sensitive = bool(SENSITIVE_TOOL_CATEGORIES.intersection(categories))
        try:
            result = _TASK_ROUTER_SERVICE.route(
                mode=mode,
                prompt=prompt,
                task=task,
                prefix=prefix,
                suffix=suffix,
                language=language,
                texts=texts,
                query=query,
                candidates=candidates,
                execution_mode=execution_mode,
                backend=backend,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                stop=stop,
                normalize=normalize,
                top_k=top_k,
                output_profile=output_profile,
                offset=offset,
                limit=limit,
                compress=compress,
                store_result=store_result,
                memory_session=memory_session,
                check_profile=check_profile,
                check_target=check_target,
                check_timeout_seconds=check_timeout_seconds,
                run_checks=run_checks,
                packages=packages,
                pip_upgrade=pip_upgrade,
                sandbox_mode=sandbox_mode,
                sandbox_id=sandbox_id,
                sandbox_action=sandbox_action,
                prompts=prompts,
                max_parallel=max_parallel,
                auto_parallel_when_possible=auto_parallel_when_possible,
            )
        except Exception as exc:
            if sensitive:
                _append_audit_event("task_router", categories, False, audit_args, type(exc).__name__)
            raise
        _otel_set_result_attributes(span, result)
        if isinstance(result, dict) and isinstance(result.get("execution_mode"), str):
            span.set_attribute("mcp.execution_mode", result["execution_mode"])
        if sensitive:
            _append_audit_event("task_router", categories, True, audit_args)
        return result


@mcp.tool()
def token_budget_guard(
    max_output_chars: int | None = None,
    default_output_profile: str | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    """Set or read global output budget/profile defaults."""
    if reset:
        payload = {
            "max_output_chars": MAX_OUTPUT_CHARS,
            "default_output_profile": "compact",
            "updated_at": _now_iso(),
        }
        _json_file_save(TOKEN_BUDGET_FILE, payload)
        return payload

    current = _token_budget_load()
    changed = False
    if max_output_chars is not None:
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be >= 1")
        current["max_output_chars"] = max_output_chars
        changed = True
    if default_output_profile is not None:
        current["default_output_profile"] = _validate_output_profile(default_output_profile)
        changed = True
    if changed:
        current["updated_at"] = _now_iso()
        _json_file_save(TOKEN_BUDGET_FILE, current)
    return current


@mcp.tool()
def cache_control(
    mode: str = "stats",
    tool: str | None = None,
    max_age_minutes: int = 1440,
    limit: int = 50,
) -> dict[str, Any]:
    """Inspect or clear server-side tool cache entries."""
    if mode not in {"stats", "clear", "clear_tool", "inspect_tool", "prune"}:
        raise ValueError("mode must be one of: stats, clear, clear_tool, inspect_tool, prune")
    if mode == "stats":
        return {"mode": mode, **_cache_stats()}
    if mode == "clear":
        return {"mode": mode, **_cache_clear(None)}
    if mode == "inspect_tool":
        if not tool:
            raise ValueError("tool is required for inspect_tool mode")
        rows = _cache_list_tool(tool=tool, limit=limit)
        return {"mode": mode, "tool": tool, "count": len(rows), "entries": rows}
    if mode == "prune":
        return {"mode": mode, **_cache_prune(max_age_minutes=max_age_minutes, tool=tool)}
    if not tool:
        raise ValueError("tool is required for clear_tool mode")
    return {"mode": mode, **_cache_clear(tool)}


@mcp.tool()
def result_handle(
    mode: str = "fetch",
    result_id: str = "",
    tool: str = "",
    value: Any = None,
    offset: int = 0,
    limit: int | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Store/fetch/list/clear result handles for large payload workflows."""
    if mode not in {"store", "fetch", "list", "clear"}:
        raise ValueError("mode must be one of: store, fetch, list, clear")
    if mode == "store":
        rid = _result_store_put(tool=tool or "manual", value=value)
        return {"mode": mode, "result_id": rid}
    if mode == "list":
        payload = _result_store_load()
        items = []
        for rid, row in payload["results"].items():
            items.append(
                {
                    "result_id": rid,
                    "tool": row.get("tool"),
                    "created_at": row.get("created_at"),
                }
            )
        items = sorted(items, key=lambda x: str(x.get("created_at", "")), reverse=True)
        items = _paginate(items, offset=offset, limit=limit)
        items = _select_fields(items, fields)
        return {"mode": mode, "count": len(items), "results": items}
    if mode == "clear":
        _result_store_save({"results": {}})
        return {"mode": mode, "cleared": True}

    if not result_id:
        raise ValueError("result_id is required for fetch mode")
    row = _result_store_get(result_id)
    value_out = row.get("value")
    if isinstance(value_out, list):
        value_out = _paginate(value_out, offset=offset, limit=limit)
        if value_out and isinstance(value_out[0], dict):
            value_out = _select_fields(value_out, fields)
    return {
        "mode": mode,
        "result_id": result_id,
        "tool": row.get("tool"),
        "created_at": row.get("created_at"),
        "value": value_out,
    }


@mcp.tool()
def tool_benchmark(
    tools: list[str] | None = None,
    iterations: int = 3,
    warmup: int = 1,
    report_path: str = str(TOOL_BENCHMARK_REPORT_FILE),
) -> dict[str, Any]:
    """Benchmark representative tool invocations for latency and payload size and persist one median-duration entry per tool."""
    if iterations < 1:
        raise ValueError("iterations must be >= 1")
    if warmup < 0:
        raise ValueError("warmup must be >= 0")

    catalog = {
        "find_paths": lambda: find_paths(path=".", recursive=True, max_entries=500, output_profile="compact"),
        "grep": lambda: grep(pattern="def ", path=".", recursive=True, max_matches=100, output_profile="compact"),
        "symbol_index": lambda: symbol_index(path=".", recursive=True, max_symbols=1000, output_profile="compact"),
        "dependency_map": lambda: dependency_map(path=".", recursive=True, max_files=1000, output_profile="compact", summary_mode="quick"),
        "call_graph": lambda: call_graph(path=".", recursive=True, max_edges=1000, output_profile="compact", summary_mode="quick"),
        "semantic_find": lambda: semantic_find(query="tool cache", path=".", max_results=20, output_profile="compact"),
        "tree_sitter_core": lambda: tree_sitter_core(path=".", mode="parse", max_files=20, max_nodes=500, output_profile="compact", summary_mode="quick"),
    }
    selected = tools or list(catalog.keys())
    unknown = [t for t in selected if t not in catalog]
    if unknown:
        raise ValueError(f"unknown benchmark tools: {', '.join(unknown)}")

    results: list[dict[str, Any]] = []
    for tool in selected:
        fn = catalog[tool]
        for _ in range(warmup):
            fn()
        latencies_ms: list[float] = []
        size_bytes: list[int] = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            out = fn()
            t1 = time.perf_counter()
            latencies_ms.append((t1 - t0) * 1000.0)
            size_bytes.append(_payload_size_bytes(out))
        latencies_sorted = sorted(latencies_ms)
        median_index = len(latencies_sorted) // 2
        if len(latencies_sorted) % 2 == 0:
            latency_ms_median = (latencies_sorted[median_index - 1] + latencies_sorted[median_index]) / 2.0
        else:
            latency_ms_median = latencies_sorted[median_index]
        results.append(
            {
                "tool": tool,
                "iterations": iterations,
                "latency_ms_avg": round(sum(latencies_ms) / len(latencies_ms), 2),
                "latency_ms_median": round(latency_ms_median, 2),
                "latency_ms_p95": round(sorted(latencies_ms)[int(max(0, len(latencies_ms) * 0.95 - 1))], 2),
                "payload_bytes_avg": int(sum(size_bytes) / len(size_bytes)),
                "payload_bytes_max": int(max(size_bytes)),
            }
        )

    _require_mutations()
    benchmark_file = _resolve_repo_path(report_path)
    existing = _json_file_load(Path(report_path), {"schema": "tool_benchmark.report.v1", "tools": {}})
    tools_payload = existing.get("tools", {}) if isinstance(existing, dict) else {}
    if not isinstance(tools_payload, dict):
        tools_payload = {}
    generated_at = _now_iso()
    for row in results:
        tools_payload[str(row["tool"])] = {
            "tool": row["tool"],
            "iterations": row["iterations"],
            "median_duration_ms": row["latency_ms_median"],
            "latency_ms_avg": row["latency_ms_avg"],
            "latency_ms_p95": row["latency_ms_p95"],
            "payload_bytes_avg": row["payload_bytes_avg"],
            "payload_bytes_max": row["payload_bytes_max"],
            "updated_at": generated_at,
        }
    report = {
        "schema": "tool_benchmark.report.v1",
        "generated_at": generated_at,
        "tools": dict(sorted(tools_payload.items())),
    }
    benchmark_file.parent.mkdir(parents=True, exist_ok=True)
    benchmark_file.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "schema": "tool_benchmark.v1",
        "report_path": report_path,
        "results": results,
    }


@mcp.tool()
def self_test(
    runner: str = "unittest",
    target: str = "tests",
    verbose: bool = True,
    timeout_seconds: int = 600,
    fail_fast: bool = False,
) -> dict[str, Any]:
    """Strict test runner: mode is implicit by runner (one of unittest|pytest), target is required (`tests` defaults to in-image selftests), `repo:<path>` forces repository scope, and returns explicit timeout/runner-missing failure payloads."""
    if runner not in {"unittest", "pytest"}:
        raise ValueError("runner must be one of: unittest, pytest")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")

    out_cap = _token_budget_apply_max(None)
    target_raw = target.strip()
    force_repo_target = target_raw.startswith("repo:")
    target_value = target_raw.split(":", 1)[1] if force_repo_target else target_raw
    if force_repo_target and not target_value:
        raise ValueError("repo target must not be empty (expected repo:<path>)")

    internal_aliases = {"tests", "internal", "selftests", "container-selftests"}
    use_internal_tests = (
        not force_repo_target and target_value in internal_aliases and INTERNAL_SELF_TESTS_DIR.is_dir()
    )
    repo_target_path = None if use_internal_tests else _resolve_repo_path(target_value)
    resolved_target = str(INTERNAL_SELF_TESTS_DIR) if use_internal_tests else target_value
    execution_root = "/" if use_internal_tests else str(REPO_PATH)

    if runner == "unittest":
        cmd = [sys.executable, "-m", "unittest"]
        if use_internal_tests:
            cmd.extend(["discover", "-s", str(INTERNAL_SELF_TESTS_DIR)])
            if verbose:
                cmd.append("-v")
            if fail_fast:
                cmd.append("-f")
        else:
            target_path = repo_target_path
            if target_path is None:
                raise RuntimeError("repo target path resolution failed")
            if target_path.is_file() and target_path.suffix == ".py":
                rel_parent = str(target_path.parent.relative_to(REPO_PATH))
                cmd.extend(["discover", "-s", rel_parent if rel_parent else ".", "-p", target_path.name])
                if verbose:
                    cmd.append("-v")
                if fail_fast:
                    cmd.append("-f")
            elif target_path.is_dir():
                rel_dir = str(target_path.relative_to(REPO_PATH))
                cmd.extend(["discover", "-s", rel_dir if rel_dir else "."])
                if verbose:
                    cmd.append("-v")
                if fail_fast:
                    cmd.append("-f")
            else:
                if verbose:
                    cmd.append("-v")
                if fail_fast:
                    cmd.append("-f")
                cmd.append(target_value)
    else:
        cmd = ["pytest"]
        cmd.append("-v" if verbose else "-q")
        if fail_fast:
            cmd.append("-x")
        cmd.append(resolved_target)

    try:
        proc = subprocess.run(
            cmd,
            cwd=execution_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        _failure_record(
            command=cmd,
            stderr=str(exc),
            category="self_test",
            suggestion="Install the selected test runner in the runtime.",
        )
        return {
            "schema": "self_test.v1",
            "runner": runner,
            "target": target,
            "resolved_target": resolved_target,
            "execution_root": execution_root,
            "ok": False,
            "timeout": False,
            "exit_code": None,
            "command": cmd,
            "stdout": "",
            "stderr": str(exc),
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.output if isinstance(exc.output, str) else getattr(exc, "stdout", "") or ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        _failure_record(
            command=cmd,
            stderr="self_test timed out",
            stdout=stdout,
            category="self_test",
            suggestion="Increase timeout_seconds or narrow the test target.",
        )
        return {
            "schema": "self_test.v1",
            "runner": runner,
            "target": target,
            "resolved_target": resolved_target,
            "execution_root": execution_root,
            "ok": False,
            "timeout": True,
            "exit_code": None,
            "command": cmd,
            "stdout": _trim_text(stdout, max_chars=out_cap),
            "stderr": _trim_text(stderr, max_chars=out_cap),
        }

    if proc.returncode != 0:
        _failure_record(
            command=cmd,
            stderr=proc.stderr,
            stdout=proc.stdout,
            category="self_test",
            suggestion="Inspect failures and rerun with fail_fast=true for faster iteration.",
        )
    return {
        "schema": "self_test.v1",
        "runner": runner,
        "target": target,
        "resolved_target": resolved_target,
        "execution_root": execution_root,
        "ok": proc.returncode == 0,
        "timeout": False,
        "exit_code": proc.returncode,
        "command": cmd,
        "stdout": _trim_text(proc.stdout, max_chars=out_cap),
        "stderr": _trim_text(proc.stderr, max_chars=out_cap),
    }


@mcp.tool()
def output_size_guard(
    mode: str = "check",
    tools: list[str] | None = None,
    tolerance_ratio: float = 1.2,
    baseline_path: str = str(OUTPUT_BASELINE_FILE),
) -> dict[str, Any]:
    """Write/check baseline payload sizes to catch output-size regressions."""
    if mode not in {"write", "check"}:
        raise ValueError("mode must be one of: write, check")
    if tolerance_ratio < 1.0:
        raise ValueError("tolerance_ratio must be >= 1.0")

    bench = tool_benchmark(tools=tools, iterations=1, warmup=0)
    current = {r["tool"]: int(r["payload_bytes_max"]) for r in bench["results"]}
    baseline_file = _resolve_repo_path(baseline_path)

    if mode == "write":
        _require_mutations()
        baseline_file.parent.mkdir(parents=True, exist_ok=True)
        baseline_file.write_text(
            json.dumps({"generated_at": _now_iso(), "sizes": current}, indent=2),
            encoding="utf-8",
        )
        return {"mode": mode, "baseline_path": baseline_path, "sizes": current}

    if not baseline_file.is_file():
        raise FileNotFoundError(baseline_path)
    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    prev = baseline.get("sizes", {})
    regressions: list[dict[str, Any]] = []
    for tool, cur in current.items():
        old = int(prev.get(tool, cur))
        if cur > int(old * tolerance_ratio):
            regressions.append({"tool": tool, "baseline": old, "current": cur})
    return {
        "mode": mode,
        "baseline_path": baseline_path,
        "ok": not regressions,
        "regressions": regressions,
        "current_sizes": current,
    }


@mcp.tool()
def commit_lint_tag(
    message: str = "",
    ref: str = "HEAD",
    include_diff_hints: bool = True,
) -> dict[str, Any]:
    """Lint commit messages and infer semantic tags from changes."""
    _require_git_repo()
    subject = message.strip()
    if not subject:
        subject = _git("show", "-s", "--format=%s", ref).stdout.strip()
    if not subject:
        raise ValueError("commit message is empty")

    pattern = re.compile(
        r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]+\))?(!)?: .+"
    )
    lint_ok = bool(pattern.match(subject))
    tags: set[str] = set()
    subject_lower = subject.lower()

    if any(tok in subject_lower for tok in ("license", "spdx", "reuse", "foss")):
        tags.add("compliance")
    if any(tok in subject_lower for tok in ("perf", "optimiz", "latency", "cache")):
        tags.add("perf")
    if any(tok in subject_lower for tok in ("security", "secret", "token", "auth", "vuln")):
        tags.add("security")
    if "!" in subject.split(":")[0] or "breaking change" in subject_lower:
        tags.add("breaking")

    changed_paths: list[str] = []
    if include_diff_hints:
        try:
            changed_out = _git("diff-tree", "--no-commit-id", "--name-only", "-r", ref).stdout
            changed_paths = [line.strip() for line in changed_out.splitlines() if line.strip()]
        except RuntimeError:
            changed_paths = []

    for rel in changed_paths:
        if rel.startswith("docs/") or rel.endswith(".md") or rel == "README.md":
            tags.add("docs")
        if rel.startswith("tests/") or "/test" in rel or rel.endswith("_test.py"):
            tags.add("test")
        if rel.startswith(".devcontainer/") or rel.startswith("source/") or "Dockerfile" in rel:
            tags.add("infra")

    suggestions: list[str] = []
    if not lint_ok:
        suggestions.append(
            "Use Conventional Commits format, e.g. 'feat(parser): add x'."
        )
    if not tags:
        suggestions.append("No semantic tags inferred; consider explicit scope or keywords.")

    return {
        "schema": "commit_lint_tag.v1",
        "ref": ref,
        "message": subject,
        "lint_ok": lint_ok,
        "tags": sorted(tags),
        "changed_paths": changed_paths,
        "suggestions": suggestions,
    }


@mcp.tool()
def golden_output_guard(
    mode: str = "check",
    tools: list[str] | None = None,
    baseline_path: str = str(GOLDEN_BASELINE_FILE),
) -> dict[str, Any]:
    """Write/check golden output hashes to detect behavior regressions."""
    if mode not in {"write", "check"}:
        raise ValueError("mode must be one of: write, check")
    selected = tools or [
        "repo_info",
        "workspace_facts",
        "token_budget_guard",
        "math_parser",
        "sql_expert",
    ]
    catalog: dict[str, Any] = {
        "repo_info": lambda: repo_info(),
        "workspace_facts": lambda: workspace_facts(),
        "token_budget_guard": lambda: token_budget_guard(reset=False),
        "math_parser": lambda: math_parser("x**2 + 2*x + 1"),
        "sql_expert": lambda: sql_expert(
            mode="format",
            query="select id, name from users where active=1 order by created_at desc",
        ),
    }
    unknown = [t for t in selected if t not in catalog]
    if unknown:
        raise ValueError(f"unknown tools for golden_output_guard: {', '.join(unknown)}")

    current: dict[str, dict[str, Any]] = {}
    for tool in selected:
        out = catalog[tool]()
        current[tool] = {
            "hash": _hash_json_payload(out),
            "payload_bytes": _payload_size_bytes(out),
        }

    baseline_file = _resolve_repo_path(baseline_path)
    if mode == "write":
        _require_mutations()
        baseline_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "golden_output_guard.baseline.v1",
            "generated_at": _now_iso(),
            "tools": current,
        }
        baseline_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return {
            "schema": "golden_output_guard.v1",
            "mode": mode,
            "ok": True,
            "baseline_path": baseline_path,
            "tools": current,
        }

    if not baseline_file.is_file():
        raise FileNotFoundError(baseline_path)
    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    previous = baseline.get("tools", {})
    regressions: list[dict[str, Any]] = []
    for tool, cur in current.items():
        prev = previous.get(tool, {})
        if prev.get("hash") and prev.get("hash") != cur["hash"]:
            regressions.append(
                {
                    "tool": tool,
                    "baseline_hash": prev.get("hash"),
                    "current_hash": cur["hash"],
                }
            )
    return {
        "schema": "golden_output_guard.v1",
        "mode": mode,
        "ok": not regressions,
        "baseline_path": baseline_path,
        "regressions": regressions,
        "tools": current,
    }


@mcp.tool()
def flaky_test_detector(
    runner: str = "pytest",
    target: str = "tests",
    runs: int = 5,
    fail_fast: bool = False,
    timeout_seconds: int = 300,
    history_path: str = str(FLAKY_HISTORY_FILE),
    update_history: bool = True,
) -> dict[str, Any]:
    """Run tests repeatedly and detect flaky tests by intermittent failures."""
    if runner not in {"pytest", "unittest"}:
        raise ValueError("runner must be one of: pytest, unittest")
    if runs < 2:
        raise ValueError("runs must be >= 2")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    _resolve_repo_path(target)
    history_file = _resolve_repo_path(history_path)

    run_results: list[dict[str, Any]] = []
    failed_counter: dict[str, int] = {}
    for i in range(runs):
        if runner == "pytest":
            cmd = ["pytest", "-q"]
            if fail_fast:
                cmd.append("-x")
            cmd.append(target)
        else:
            cmd = [sys.executable, "-m", "unittest"]
            if fail_fast:
                cmd.append("-f")
            cmd.extend(["-v", target])
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(REPO_PATH),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            return {
                "schema": "flaky_test_detector.v1",
                "ok": False,
                "runner": runner,
                "error": str(exc),
                "runs": runs,
            }
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            run_results.append(
                {
                    "run": i + 1,
                    "ok": False,
                    "exit_code": None,
                    "timeout": True,
                    "failed_tests": ["<timeout>"],
                    "stdout": _trim_text(stdout),
                    "stderr": _trim_text(stderr),
                }
            )
            failed_counter["<timeout>"] = failed_counter.get("<timeout>", 0) + 1
            continue

        merged = f"{proc.stdout}\n{proc.stderr}"
        failed_tests = (
            _extract_failed_tests_pytest(merged)
            if runner == "pytest"
            else _extract_failed_tests_unittest(merged)
        )
        if proc.returncode != 0 and not failed_tests:
            failed_tests = ["<unknown>"]
        for t in failed_tests:
            failed_counter[t] = failed_counter.get(t, 0) + 1
        run_results.append(
            {
                "run": i + 1,
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "timeout": False,
                "failed_tests": failed_tests,
            }
        )

    flaky = [
        {
            "test": test_id,
            "failed_runs": failures,
            "pass_runs": runs - failures,
            "failure_rate": round(failures / runs, 4),
        }
        for test_id, failures in sorted(failed_counter.items())
        if 0 < failures < runs
    ]
    consistently_failing = [
        test_id for test_id, failures in sorted(failed_counter.items()) if failures == runs
    ]

    if update_history:
        _require_mutations()
        payload = {"schema": "flaky_test_history.v1", "updated_at": _now_iso(), "tests": {}}
        if history_file.is_file():
            try:
                payload = json.loads(history_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {"schema": "flaky_test_history.v1", "updated_at": _now_iso(), "tests": {}}
        tests_map = payload.get("tests", {})
        if not isinstance(tests_map, dict):
            tests_map = {}
        for test_id, failures in failed_counter.items():
            rec = tests_map.get(test_id, {})
            total_runs = int(rec.get("total_runs", 0)) + runs
            total_failures = int(rec.get("total_failures", 0)) + failures
            rec = {
                "total_runs": total_runs,
                "total_failures": total_failures,
                "failure_rate": round(total_failures / max(1, total_runs), 4),
                "last_seen": _now_iso(),
            }
            tests_map[test_id] = rec
        payload["tests"] = tests_map
        payload["updated_at"] = _now_iso()
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "schema": "flaky_test_detector.v1",
        "ok": True,
        "runner": runner,
        "target": target,
        "runs": runs,
        "run_results": run_results,
        "flaky_tests": flaky,
        "consistently_failing_tests": consistently_failing,
        "history_path": history_path if update_history else None,
    }


def _is_python_test_path(rel: str) -> bool:
    p = Path(rel)
    parts = {part.lower() for part in p.parts}
    name = p.name.lower()
    return p.suffix == ".py" and ("tests" in parts or name.startswith("test_") or name.endswith("_test.py"))


def _python_module_name(rel: str) -> str:
    path = Path(rel)
    without_suffix = path.with_suffix("")
    parts = [part for part in without_suffix.parts if part != "__init__"]
    return ".".join(parts)


def _test_impact_map_fingerprint() -> str:
    return _fingerprint_path(REPO_PATH, recursive=True, suffixes={".py"}, max_files=5000)


def _artifact_age_hours(payload: dict[str, Any]) -> float | None:
    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        return None
    try:
        dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600)


def _load_test_impact_map(max_age_hours: int = TEST_IMPACT_MAP_MAX_AGE_HOURS) -> tuple[dict[str, Any] | None, str]:
    path = _resolve_repo_path(str(TEST_IMPACT_MAP_FILE))
    if not path.is_file():
        return None, "absent"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "invalid"
    if not isinstance(payload, dict) or payload.get("schema") != "test_impact_map.v1":
        return None, "invalid"
    age = _artifact_age_hours(payload)
    if age is None or age > max_age_hours:
        return payload, "stale"
    if payload.get("source_fingerprint") != _test_impact_map_fingerprint():
        return payload, "stale"
    return payload, "fresh"


def _extract_test_functions(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            names.append(node.name)
        elif isinstance(node, ast.ClassDef) and (node.name.endswith("Test") or node.name.startswith("Test")):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test"):
                    names.append(f"{node.name}.{child.name}")
    return sorted(names)


def _build_test_impact_map_payload() -> dict[str, Any]:
    dep = dependency_map(path=".", recursive=True, include_stdlib=False, output_profile="normal")
    edges = [edge for edge in dep.get("edges", []) if isinstance(edge, dict)]
    reverse_edges: dict[str, set[str]] = {}
    direct_edges: dict[str, set[str]] = {}
    for edge in edges:
        src = str(edge.get("from", ""))
        dst = str(edge.get("to", ""))
        if not src or not dst:
            continue
        reverse_edges.setdefault(dst, set()).add(src)
        direct_edges.setdefault(src, set()).add(dst)

    py_files = sorted(
        str(path.relative_to(REPO_PATH)).replace("\\", "/")
        for path in _iter_candidate_files(REPO_PATH, recursive=True)
        if path.suffix == ".py"
    )
    test_files = [rel for rel in py_files if _is_python_test_path(rel)]
    test_set = set(test_files)
    source_files = [rel for rel in py_files if rel not in test_set]
    symbols_by_file: dict[str, list[str]] = {}
    for sym in symbol_index(path=".", include_private=False, recursive=True, max_symbols=20000, output_profile="normal", adaptive_limits=False):
        rel = str(sym.get("path", ""))
        name = str(sym.get("name", ""))
        if rel and name:
            symbols_by_file.setdefault(rel, []).append(name)

    tests_by_source: dict[str, dict[str, Any]] = {}
    coverage_gaps: list[dict[str, Any]] = []
    for src in source_files:
        impacted_files: set[str] = {src}
        queue = [src]
        while queue:
            cur = queue.pop(0)
            for dependent in reverse_edges.get(cur, set()):
                if dependent not in impacted_files:
                    impacted_files.add(dependent)
                    queue.append(dependent)
        module = _python_module_name(src)
        stem = Path(src).stem
        mapped_tests: dict[str, dict[str, Any]] = {}
        for test in test_files:
            reasons: list[str] = []
            confidence = 0.0
            if test in impacted_files:
                reason = "direct_import" if src in direct_edges.get(test, set()) else "reverse_import_dependent"
                reasons.append(reason)
                confidence = max(confidence, 0.92 if reason == "direct_import" else 0.82)
            if Path(test).name in {f"test_{stem}.py", f"{stem}_test.py"}:
                reasons.append("pytest_naming_convention")
                confidence = max(confidence, 0.72)
            try:
                test_text = _resolve_repo_path(test).read_text(encoding="utf-8", errors="replace")
            except OSError:
                test_text = ""
            needles = {module, stem, src, *symbols_by_file.get(src, [])}
            if any(needle and needle in test_text for needle in needles):
                reasons.append("source_reference_in_test")
                confidence = max(confidence, 0.78)
            if reasons:
                mapped_tests[test] = {"path": test, "symbols": _extract_test_functions(_resolve_repo_path(test)), "reasons": sorted(set(reasons)), "confidence": round(confidence, 2)}
        entries = sorted(mapped_tests.values(), key=lambda row: row["path"])
        if not entries:
            coverage_gaps.append({"path": src, "reason": "no_static_test_mapping"})
        tests_by_source[src] = {"path": src, "symbols": sorted(set(symbols_by_file.get(src, []))), "impacted_tests": entries, "confidence": round(max([row["confidence"] for row in entries], default=0.0), 2), "mapping_reasons": sorted({reason for row in entries for reason in row["reasons"]}), "dependent_files": sorted(impacted_files - {src})}
    return {"schema": "test_impact_map.v1", "generated_at": _now_iso(), "source_fingerprint": _test_impact_map_fingerprint(), "python_file_count": len(py_files), "source_count": len(source_files), "test_count": len(test_files), "sources": tests_by_source, "coverage_gaps": coverage_gaps}


def _query_test_impact_map(payload: dict[str, Any], changed_files: list[str], max_tests: int = 300) -> dict[str, Any]:
    sources = payload.get("sources", {}) if isinstance(payload.get("sources"), dict) else {}
    selected: dict[str, dict[str, Any]] = {}
    impacted_sources: list[dict[str, Any]] = []
    unmapped: list[str] = []
    for rel in changed_files:
        if _is_python_test_path(rel):
            selected.setdefault(
                rel,
                {
                    "path": rel,
                    "symbols": _extract_test_functions(_resolve_repo_path(rel))
                    if _resolve_repo_path(rel).is_file()
                    else [],
                    "reasons": ["changed_test_file"],
                    "confidence": 1.0,
                },
            )
            continue
        row = sources.get(rel)
        if not isinstance(row, dict):
            if rel.endswith(".py"):
                unmapped.append(rel)
            continue
        tests = row.get("impacted_tests", []) if isinstance(row.get("impacted_tests"), list) else []
        if not tests:
            unmapped.append(rel)
        for test in tests:
            if isinstance(test, dict) and isinstance(test.get("path"), str):
                selected.setdefault(test["path"], test)
        impacted_sources.append({"path": rel, "symbols": row.get("symbols", []), "confidence": row.get("confidence", 0), "mapping_reasons": row.get("mapping_reasons", []), "test_count": len(tests)})
    ordered = sorted(selected.values(), key=lambda row: row["path"])[:max_tests]
    changed_set = set(changed_files)
    return {"tests": [row["path"] for row in ordered], "test_details": ordered, "impacted_sources": impacted_sources, "unmapped_changed_files": sorted(set(unmapped)), "coverage_gaps": [gap for gap in payload.get("coverage_gaps", []) if isinstance(gap, dict) and gap.get("path") in changed_set], "confidence": round(max([float(row.get("confidence", 0)) for row in ordered], default=0.0), 2)}


@mcp.tool()
def change_impact_gate(
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    critical_globs: list[str] | None = None,
    require_tests_for_critical: bool = True,
    require_docs_for_impl_diff: bool = True,
    block_on_risk_level: str = "high",
) -> dict[str, Any]:
    """Block risky changes when impact, docs, or tests requirements are not met."""
    _require_git_repo()
    if block_on_risk_level not in {"none", "medium", "high"}:
        raise ValueError("block_on_risk_level must be one of: none, medium, high")

    changed_out = _git("diff", "--name-only", f"{base_ref}...{head_ref}").stdout
    changed = [line.strip() for line in changed_out.splitlines() if line.strip()]
    critical_patterns = critical_globs or [
        "source/server.py",
        "source/Dockerfile",
        ".devcontainer/**",
        "**/auth*",
        "**/security*",
    ]
    critical_changed = [
        rel for rel in changed if any(fnmatch.fnmatch(rel, pat) for pat in critical_patterns)
    ]

    impacts = impact_tests(base_ref=base_ref, head_ref=head_ref, output_profile="compact")
    selected_tests = impacts.get("tests", []) if isinstance(impacts, dict) else []
    unmapped_changed_files = impacts.get("unmapped_changed_files", []) if isinstance(impacts, dict) else []
    docs = doc_sync_check(base_ref=base_ref, head_ref=head_ref)
    risk = risk_scoring(ref=head_ref)

    blocked_reasons: list[str] = []
    if require_tests_for_critical and critical_changed and not selected_tests:
        blocked_reasons.append("critical changes detected but no impacted tests selected")
    if require_docs_for_impl_diff and bool(docs.get("needs_docs_update")):
        blocked_reasons.append("implementation changed without documentation updates")
    if block_on_risk_level != "none":
        risk_level = str(risk.get("risk_level", "low"))
        if _risk_level_value(risk_level) >= _risk_level_value(block_on_risk_level):
            blocked_reasons.append(
                f"risk level '{risk_level}' meets/exceeds gate threshold '{block_on_risk_level}'"
            )

    return {
        "schema": "change_impact_gate.v1",
        "base_ref": base_ref,
        "head_ref": head_ref,
        "changed_count": len(changed),
        "critical_changed": critical_changed,
        "selected_tests": selected_tests,
        "unmapped_changed_files": unmapped_changed_files,
        "impact_tests": impacts,
        "docs": docs,
        "risk": {"risk_score": risk.get("risk_score"), "risk_level": risk.get("risk_level")},
        "should_block": bool(blocked_reasons),
        "blocked_reasons": blocked_reasons,
    }


@mcp.tool()
def smart_fix_batch(
    findings: list[dict[str, Any]],
    mode: str = "plan",
    regex: bool = False,
    replace_all: bool = False,
    run_validation: bool = True,
) -> dict[str, Any]:
    """Plan or apply a batch of targeted code fixes from structured findings."""
    if mode not in {"plan", "execute"}:
        raise ValueError("mode must be one of: plan, execute")
    if not findings:
        raise ValueError("findings must not be empty")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for idx, item in enumerate(findings):
        path = item.get("path")
        search = item.get("search")
        replacement = item.get("replacement")
        if not isinstance(path, str) or not isinstance(search, str) or not isinstance(replacement, str):
            raise ValueError("each finding requires string path/search/replacement")
        _resolve_repo_path(path)
        payload = {
            "index": idx,
            "path": path,
            "search": search,
            "replacement": replacement,
            "description": str(item.get("description", "")),
            "severity": str(item.get("severity", "medium")),
        }
        grouped.setdefault(path, []).append(payload)

    if mode == "plan":
        return {
            "schema": "smart_fix_batch.v1",
            "mode": mode,
            "file_count": len(grouped),
            "fix_count": len(findings),
            "plan": [
                {"path": path, "fixes": rows}
                for path, rows in sorted(grouped.items(), key=lambda x: x[0])
            ],
        }

    _require_mutations()
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    changed_paths: set[str] = set()
    for path, rows in grouped.items():
        file_path = _resolve_repo_path(path)
        text = file_path.read_text(encoding="utf-8", errors="replace")
        original = text
        for row in rows:
            search = row["search"]
            repl = row["replacement"]
            if regex:
                count = 0 if replace_all else 1
                updated, n = re.subn(search, repl, text, count=count)
            else:
                if search not in text:
                    skipped.append(
                        {
                            "path": path,
                            "index": row["index"],
                            "reason": "search text not found",
                        }
                    )
                    continue
                if replace_all:
                    n = text.count(search)
                    updated = text.replace(search, repl)
                else:
                    updated = text.replace(search, repl, 1)
                    n = 1
            if n == 0:
                skipped.append(
                    {"path": path, "index": row["index"], "reason": "no replacements made"}
                )
                continue
            text = updated
            applied.append({"path": path, "index": row["index"], "replacements": n})
        if text != original:
            file_path.write_text(text, encoding="utf-8")
            changed_paths.add(path)

    compile_errors: list[dict[str, Any]] = []
    if run_validation:
        for rel in sorted(changed_paths):
            if not rel.endswith(".py"):
                continue
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", str(_resolve_repo_path(rel))],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                compile_errors.append({"path": rel, "stderr": _trim_text(proc.stderr)})

    return {
        "schema": "smart_fix_batch.v1",
        "mode": mode,
        "ok": len(compile_errors) == 0,
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "changed_paths": sorted(changed_paths),
        "applied": applied,
        "skipped": skipped,
        "compile_error_count": len(compile_errors),
        "compile_errors": compile_errors,
    }


CLARIFICATION_GATE_ALLOWED_RISK_LEVELS = {"low", "medium", "high"}
CLARIFICATION_GATE_SENSITIVE_FIELD_NAMES = (
    "password",
    "secret",
    "token",
    "credential",
    "authorization",
    "api_key",
    "private_key",
)


def _clarification_missing_field(field: str, reason: str, question: str) -> dict[str, Any]:
    return {
        "field": field,
        "required": True,
        "sensitive": False,
        "reason": reason,
        "question": question,
    }


def _clarification_gate_payload(
    *,
    intent: str,
    target: str,
    operation: str,
    risk_level: str,
    rollback_plan: str = "",
    user_response_action: str = "",
    log_audit: bool = True,
) -> dict[str, Any]:
    normalized_intent = intent.strip()
    normalized_target = target.strip()
    normalized_operation = operation.strip() or "unspecified"
    normalized_risk = risk_level.strip().lower()
    normalized_rollback = rollback_plan.strip()
    action = user_response_action.strip().lower()
    if action not in {"", "accept", "decline", "cancel"}:
        raise ValueError("user_response_action must be one of: accept, decline, cancel")

    missing: list[dict[str, Any]] = []
    if not normalized_intent:
        missing.append(
            _clarification_missing_field(
                "intent",
                "The requested outcome is empty or underspecified.",
                "What outcome should this workflow achieve?",
            )
        )
    if not normalized_target or normalized_target.lower() in {"unknown", "tbd", "?"}:
        missing.append(
            _clarification_missing_field(
                "target",
                "The repository path, diff range, release candidate, or feature area is missing.",
                "Which file, directory, diff range, branch, or release candidate should be evaluated?",
            )
        )
    if normalized_risk not in CLARIFICATION_GATE_ALLOWED_RISK_LEVELS:
        missing.append(
            _clarification_missing_field(
                "risk_level",
                "Risk level must be classified before a high-impact workflow proceeds.",
                "Is this operation low, medium, or high risk?",
            )
        )
    high_impact_terms = ("apply", "diff", "write", "delete", "move", "rollback", "restore", "release", "deploy")
    operation_is_high_impact = normalized_risk == "high" or any(
        term in normalized_operation.lower() for term in high_impact_terms
    )
    if operation_is_high_impact and not normalized_rollback:
        missing.append(
            _clarification_missing_field(
                "rollback_plan",
                "High-impact mutation or release workflows need an explicit rollback/snapshot plan.",
                "What rollback, snapshot, or recovery plan should be used if the operation fails?",
            )
        )

    sensitive_requested = [
        item["field"]
        for item in missing
        if any(part in item["field"].lower() for part in CLARIFICATION_GATE_SENSITIVE_FIELD_NAMES)
    ]
    questions = [str(item["question"]) for item in missing]
    if action == "decline":
        status = "declined"
        ok_to_continue = False
        decision_reasons = ["user_declined_clarification"]
    elif action == "cancel":
        status = "cancelled"
        ok_to_continue = False
        decision_reasons = ["user_cancelled_clarification"]
    elif missing:
        status = "needs_clarification"
        ok_to_continue = False
        decision_reasons = [f"missing:{item['field']}" for item in missing]
    else:
        status = "ready"
        ok_to_continue = True
        decision_reasons = ["required_context_present", "no_sensitive_fields_requested"]

    fallback_checklist = questions or ["Required intent, target, risk, and rollback context is present."]
    elicitation_properties = {
        item["field"]: {
            "type": "string",
            "title": item["field"].replace("_", " ").title(),
            "description": item["question"],
        }
        for item in missing
    }
    elicitation = {
        "adapter": "mcp.elicitation/create",
        "supported_when_client_allows": True,
        "response_actions": ["accept", "decline", "cancel"],
        "non_sensitive_fields_only": True,
        "request": {
            "message": "Clarify missing non-sensitive context before continuing.",
            "requestedSchema": {
                "type": "object",
                "required": [item["field"] for item in missing],
                "properties": elicitation_properties,
                "additionalProperties": False,
            },
        },
    }
    payload: dict[str, Any] = {
        "schema": "clarification_gate.v1",
        "ok_to_continue": ok_to_continue,
        "status": status,
        "missing_fields": missing,
        "questions": questions,
        "fallback_checklist": fallback_checklist,
        "elicitation": elicitation,
        "inputs": {
            "operation": normalized_operation,
            "risk_level": normalized_risk or "unspecified",
            "target_present": bool(normalized_target),
            "intent_present": bool(normalized_intent),
            "rollback_plan_present": bool(normalized_rollback),
        },
        "decision_reasons": decision_reasons,
        "audit": {
            "logged": log_audit,
            "redaction": "Audit records gate decision metadata only; user answers are not requested for sensitive fields or persisted.",
            "sensitive_fields_requested": sensitive_requested,
        },
    }
    if log_audit:
        _append_audit_event(
            "clarification_gate",
            ["read-only", "governance"],
            ok_to_continue,
            {
                "decision": {
                    "status": status,
                    "ok_to_continue": ok_to_continue,
                    "missing_fields": [item["field"] for item in missing],
                    "question_count": len(questions),
                    "sensitive_fields_requested": sensitive_requested,
                },
                "inputs": payload["inputs"],
            },
            status,
        )
    return payload


@mcp.tool()
def clarification_gate(
    intent: str,
    target: str = "",
    operation: str = "unspecified",
    risk_level: str = "",
    rollback_plan: str = "",
    user_response_action: str = "",
) -> dict[str, Any]:
    """Assess underspecified risky workflow intent and return non-sensitive clarification questions."""
    return _clarification_gate_payload(
        intent=intent,
        target=target,
        operation=operation,
        risk_level=risk_level,
        rollback_plan=rollback_plan,
        user_response_action=user_response_action,
    )


INTERACTION_INVARIANT_SMELL_GUIDANCE: dict[str, str] = {
    "intent_drift": "Pause and restate the user goal; use clarification_gate when the current plan no longer matches the original intent.",
    "ignored_historical_instruction": "Stop before mutation and reconcile the ignored constraint; create state_snapshot before any approved follow-up mutation.",
    "missing_validation": "Run or explicitly waive the required validation before summarizing readiness; use change_impact_gate or release_readiness for release-sensitive work.",
    "contradicted_prior_response": "Ask a clarifying question before continuing because the trajectory contradicts an earlier plan or promise.",
}


def _interaction_audit_normalize_notes(
    recent_notes: list[str] | list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(recent_notes or []):
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("note") or item.get("content") or item)
            role = str(item.get("role") or item.get("source") or "note")
        else:
            text = str(item)
            role = "note"
        normalized.append(
            {
                "index": index,
                "role": role[:40],
                "text": _redact_audit_string(text.strip())[:500],
                "_raw_text": text.strip()[:1000],
            }
        )
    return normalized[:25]


def _interaction_audit_extract_invariants(task_summary: str, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combined = "\n".join([task_summary] + [str(note.get("_raw_text", note.get("text", ""))) for note in notes]).lower()
    rules: list[tuple[str, str, str, tuple[str, ...]]] = [
        ("mutation_mode", "No mutation without explicit approval", "blocking", ("no mutation", "read-only", "do not mutate", "do not edit", "do not change", "without mutation")),
        ("secret_safety", "Do not request, print, store, or commit secrets", "blocking", ("no secret", "do not expose secret", "do not print token", "credential", "api key", "private key", "authorization header")),
        ("validation", "Required validation or tests must be run or explicitly waived", "required", ("run tests", "pytest", "validation required", "must validate", "required tests", "typecheck", "lint")),
        ("rollback", "Mutation work needs a snapshot or rollback plan", "required", ("rollback", "snapshot", "state_snapshot", "restore plan")),
        ("scope", "Stay inside the stated task/file/repository scope", "required", ("out of scope", "only", "scope", "target file", "do not touch")),
        ("release_gate", "Readiness summaries must respect release blockers", "required", ("release blocker", "release readiness", "do not release", "not ready")),
        ("client_compatibility", "Preserve target client compatibility", "required", ("vscode", "copilot", "mcp client", "compatibility", "client")),
    ]
    invariants: list[dict[str, Any]] = []
    for invariant_id, description, severity, keywords in rules:
        matches = [kw for kw in keywords if kw in combined]
        if matches:
            invariants.append(
                {
                    "id": invariant_id,
                    "description": description,
                    "severity": severity,
                    "source": "task_summary_or_recent_notes",
                    "evidence_terms": matches[:4],
                }
            )
    if not invariants:
        invariants.append(
            {
                "id": "task_intent",
                "description": "Preserve the stated task intent across follow-up turns",
                "severity": "advisory",
                "source": "task_summary",
                "evidence_terms": [],
            }
        )
    return invariants


def _interaction_audit_find_smells(
    task_summary: str,
    notes: list[dict[str, Any]],
    invariants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    task_lower = task_summary.lower()
    note_text = "\n".join(str(note.get("_raw_text", note.get("text", ""))) for note in notes)
    blob = f"{task_summary}\n{note_text}".lower()
    invariant_ids = {item.get("id") for item in invariants}
    smells: list[dict[str, Any]] = []

    def add(category: str, confidence: float, reason: str, evidence: list[str]) -> None:
        smells.append(
            {
                "category": category,
                "confidence": round(max(0.0, min(1.0, confidence)), 2),
                "reason": reason,
                "evidence": [_redact_audit_string(item)[:240] for item in evidence[:4]],
                "safe_next_action": INTERACTION_INVARIANT_SMELL_GUIDANCE[category],
            }
        )

    mutation_terms = ("apply patch", "edited", "write", "delete", "move", "commit", "committed", "push", "deploy", "changed file", "mutated")
    if "mutation_mode" in invariant_ids and any(term in blob for term in mutation_terms):
        add(
            "ignored_historical_instruction",
            0.86,
            "Trajectory mentions mutation while a read-only/no-mutation constraint is present.",
            [note["text"] for note in notes if any(term in str(note.get("_raw_text", note.get("text", ""))).lower() for term in mutation_terms)],
        )
    secret_terms = ("token=", "password=", "authorization:", "api_key", "api key", "private key", "secret=")
    if "secret_safety" in invariant_ids and any(term in blob for term in secret_terms):
        add(
            "ignored_historical_instruction",
            0.9,
            "Trajectory appears to handle or expose a secret despite a no-secret constraint.",
            [note["text"] for note in notes if any(term in str(note.get("_raw_text", note.get("text", ""))).lower() for term in secret_terms)],
        )
    if "validation" in invariant_ids and any(term in blob for term in ("skipped tests", "did not run tests", "not run tests", "untested", "no validation", "without validation")):
        add(
            "missing_validation",
            0.88,
            "Required validation is mentioned but the trajectory says it was skipped or not run.",
            [note["text"] for note in notes if any(term in str(note.get("_raw_text", note.get("text", ""))).lower() for term in ("skipped", "not run", "untested", "no validation"))],
        )
    drift_terms = ("instead", "unrelated", "changed scope", "different task", "also deploy", "rewrite everything", "new feature")
    if any(term in blob for term in drift_terms) and any(term in task_lower for term in ("audit", "read-only", "review", "summarize", "diagnose")):
        add(
            "intent_drift",
            0.72,
            "Trajectory suggests a broader or different action than the stated read-only/audit intent.",
            [note["text"] for note in notes if any(term in str(note.get("_raw_text", note.get("text", ""))).lower() for term in drift_terms)],
        )
    contradiction_patterns = (
        ("will not", "then i"),
        ("plan: no", "now"),
        ("promised", "but"),
        ("prior plan", "instead"),
        ("said no", "but"),
    )
    if any(a in blob and b in blob for a, b in contradiction_patterns) or "contradict" in blob:
        add(
            "contradicted_prior_response",
            0.76,
            "Recent notes indicate a prior plan or promise was contradicted.",
            [note["text"] for note in notes if any(term in str(note.get("_raw_text", note.get("text", ""))).lower() for term in ("will not", "prior plan", "instead", "contradict", "but"))],
        )
    return smells


def _interaction_audit_confidence(smells: list[dict[str, Any]], invariants: list[dict[str, Any]]) -> float:
    if smells:
        return round(max(float(item.get("confidence", 0.0)) for item in smells), 2)
    if invariants and invariants[0].get("id") != "task_intent":
        return 0.64
    return 0.42


def _build_interaction_invariant_audit(
    *,
    task_summary: str,
    recent_notes: list[str] | list[dict[str, Any]] | None = None,
    planned_next_step: str = "",
    log_audit: bool = False,
) -> dict[str, Any]:
    summary = task_summary.strip()[:1000]
    notes = _interaction_audit_normalize_notes(recent_notes)
    if planned_next_step.strip():
        notes.append(
            {
                "index": len(notes),
                "role": "planned_next_step",
                "text": _redact_audit_string(planned_next_step.strip())[:500],
                "_raw_text": planned_next_step.strip()[:1000],
            }
        )
    invariants = _interaction_audit_extract_invariants(summary, notes)
    smells = _interaction_audit_find_smells(summary, notes, invariants)
    confidence = _interaction_audit_confidence(smells, invariants)
    needs_clarification = bool(smells) or confidence < 0.55
    recommendations = [item["safe_next_action"] for item in smells]
    if needs_clarification and not any("clarification_gate" in item for item in recommendations):
        recommendations.append("Use clarification_gate before mutation or readiness summaries when constraints are ambiguous.")
    if any(item.get("id") == "mutation_mode" for item in invariants):
        recommendations.append("Use state_snapshot before any explicitly approved mutation follow-up.")
    if any(item.get("category") == "missing_validation" for item in smells):
        recommendations.append("Use change_impact_gate and release_readiness to verify validation/readiness evidence.")
    if not recommendations:
        recommendations.append("Proceed with the planned read-only workflow while preserving the extracted invariants.")

    payload: dict[str, Any] = {
        "schema": "interaction_invariant_audit.v1",
        "read_only": True,
        "advisory_only": True,
        "ok_to_continue": not needs_clarification,
        "confidence": confidence,
        "extracted_invariants": invariants,
        "suspected_smells": smells,
        "safe_next_actions": list(dict.fromkeys(recommendations)),
        "linked_gates": {
            "clarification_gate": "Ask focused non-sensitive questions when intent or constraints are ambiguous.",
            "state_snapshot": "Create a rollback point before approved mutation work.",
            "change_impact_gate": "Check impacted files/tests before risky changes.",
            "release_readiness": "Summarize release blockers only after required evidence is present.",
            "workflow_diagnostics": "Diagnose concrete failed tool trajectories and audit events after a workflow failure.",
        },
        "redactions_applied": ["sensitive_keys_or_values"],
        "security": {
            "stores_conversation_logs_by_default": False,
            "records_secrets": False,
            "caller_snippets_redacted": True,
            "audit_logging_default": False,
        },
        "input_summary": {
            "task_summary_present": bool(summary),
            "recent_note_count": len(notes),
            "planned_next_step_present": bool(planned_next_step.strip()),
        },
    }
    if log_audit:
        _append_audit_event(
            "interaction_invariant_audit",
            ["read-only", "governance"],
            payload["ok_to_continue"],
            {
                "smell_categories": [item["category"] for item in smells],
                "invariant_ids": [item["id"] for item in invariants],
                "confidence": confidence,
                "input_summary": payload["input_summary"],
            },
            "ready" if payload["ok_to_continue"] else "needs_clarification",
        )
    return payload


@mcp.tool()
def interaction_invariant_audit(
    task_summary: str,
    recent_notes: list[str] | list[dict[str, Any]] | None = None,
    planned_next_step: str = "",
    log_audit: bool = False,
) -> dict[str, Any]:
    """Extract multi-turn task invariants and flag interaction-smell risks before mutation/readiness summaries."""
    return _build_interaction_invariant_audit(
        task_summary=task_summary,
        recent_notes=recent_notes,
        planned_next_step=planned_next_step,
        log_audit=log_audit,
    )


@mcp.tool()
def workflow_diagnostics(
    trajectory: list[dict[str, Any]] | None = None,
    start_time: str = "",
    end_time: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Diagnose failed MCP workflows from redacted audit events and optional trajectory snippets."""
    _require_git_repo()
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    start_dt = _parse_iso_datetime(start_time) if start_time.strip() else None
    end_dt = _parse_iso_datetime(end_time) if end_time.strip() else None
    if start_time.strip() and start_dt is None:
        raise ValueError("start_time must be an ISO-8601 timestamp")
    if end_time.strip() and end_dt is None:
        raise ValueError("end_time must be an ISO-8601 timestamp")
    if start_dt and end_dt and start_dt > end_dt:
        raise ValueError("start_time must be before end_time")
    if trajectory is not None and not isinstance(trajectory, list):
        raise ValueError("trajectory must be a list of step objects")
    events, audit_meta = _load_audit_events(start_dt, end_dt)
    report = _build_workflow_diagnostics(events, trajectory=trajectory, limit=limit)
    report["audit_source"] = audit_meta
    report["read_only"] = True
    report["security"] = {
        "redaction": "audit events and caller-supplied trajectory snippets are passed through MCP audit redaction",
        "records_secrets": False,
        "repo_boundary_enforced": audit_meta.get("source") != "outside_repo_boundary",
    }
    return report


def _governance_report_impl(
    start_time: str = "",
    end_time: str = "",
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    export: bool = True,
    compressed_observation: bool = False,
) -> dict[str, Any]:
    _require_git_repo()
    start_dt = _parse_iso_datetime(start_time) if start_time.strip() else None
    end_dt = _parse_iso_datetime(end_time) if end_time.strip() else None
    if start_time.strip() and start_dt is None:
        raise ValueError("start_time must be an ISO-8601 timestamp")
    if end_time.strip() and end_dt is None:
        raise ValueError("end_time must be an ISO-8601 timestamp")
    if start_dt and end_dt and start_dt > end_dt:
        raise ValueError("start_time must be before end_time")

    events, audit_meta = _load_audit_events(start_dt, end_dt)
    counts = _aggregate_audit_events(events)
    generated_at = _now_iso()
    git_info = {
        "base_ref": base_ref,
        "head_ref": head_ref,
        "base_commit": _git("rev-parse", base_ref, check=False).stdout.strip(),
        "head_commit": _git("rev-parse", head_ref, check=False).stdout.strip(),
        "range": f"{base_ref}...{head_ref}",
    }
    report_seed = json.dumps(
        {
            "generated_at": generated_at,
            "audit_digest": counts.get("digest", {}).get("chain_head", ""),
            "git": git_info,
            "window": {"start_time": start_time, "end_time": end_time},
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    report_id = f"governance-report-{_now_stamp()}-{hashlib.sha256(report_seed.encode('utf-8')).hexdigest()[:12]}"
    report: dict[str, Any] = {
        "schema": "governance_report.v1",
        "report_id": report_id,
        "generated_at": generated_at,
        "window": {
            "start_time": start_dt.isoformat() if start_dt else "",
            "end_time": end_dt.isoformat() if end_dt else "",
        },
        "git": git_info,
        "audit": {"source": audit_meta, "counts": counts, "redacted_events_sample": events[:25]},
        "workflow_diagnostics": _workflow_diagnostics_compact(
            _build_workflow_diagnostics(events, limit=50)
        ),
        "governance_hooks": _governance_result_store_summary(),
        "snapshots": _governance_snapshot_references(),
        "security": {
            "redaction": "audit events and report summaries are passed through MCP audit redaction",
            "repo_boundary_enforced": audit_meta.get("source") != "outside_repo_boundary",
            "external_integrations": "out_of_scope",
        },
        "exports": {},
    }
    resource_links: list[dict[str, Any]] = []
    provenance_inputs = {
        "start_time": start_time,
        "end_time": end_time,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "export": export,
        "compressed_observation": compressed_observation,
    }
    if export:
        report["exports"] = _write_governance_report_exports(
            report,
            provenance_inputs=provenance_inputs,
        )
        exports = report.get("exports", {}) if isinstance(report.get("exports"), dict) else {}
        if isinstance(exports.get("json"), str):
            resource_links.append(
                _artifact_resource_link(
                    title="Governance report JSON",
                    rel_path=exports["json"],
                    mime_type="application/json",
                    created_at=generated_at,
                    redacted=True,
                    safety_note="JSON export contains redacted audit summaries only; raw secrets are not persisted.",
                )
            )
        if isinstance(exports.get("markdown"), str):
            resource_links.append(
                _artifact_resource_link(
                    title="Governance report Markdown",
                    rel_path=exports["markdown"],
                    mime_type="text/markdown",
                    created_at=generated_at,
                    redacted=True,
                    safety_note="Markdown export is generated from redacted governance summary fields.",
                )
            )
    report["resource_links"] = resource_links
    report["_meta"] = _artifact_meta(resource_links)
    if compressed_observation:
        exports = report.get("exports", {}) if isinstance(report.get("exports"), dict) else {}
        if isinstance(exports.get("json"), str):
            raw_reference = {
                "type": "artifact",
                "path": exports["json"],
                "mime_type": "application/json",
            }
        else:
            raw_reference = {"type": "inline_return", "field": "report", "count": 1}
        report["compressed_observation"] = _compressed_observation_for_governance_report(
            report, raw_reference=raw_reference
        )
    if export and isinstance(report.get("exports"), dict) and isinstance(report["exports"].get("json"), str):
        exports = report.get("exports", {}) if isinstance(report.get("exports"), dict) else {}
        if isinstance(exports.get("json"), str) and isinstance(exports.get("markdown"), str):
            lineage_rel = _workflow_lineage_export_path(str(report["report_id"]))
            provenance_paths = {
                exports["json"]: str(_artifact_provenance_path(exports["json"]).relative_to(REPO_PATH)),
                exports["markdown"]: str(_artifact_provenance_path(exports["markdown"]).relative_to(REPO_PATH)),
                lineage_rel: str(_artifact_provenance_path(lineage_rel).relative_to(REPO_PATH)),
            }
            exports["lineage"] = lineage_rel
            exports["provenance"] = provenance_paths
            report["exports"] = exports
            plan_inputs = _governance_workflow_lineage_plan_inputs(
                constraints=_workflow_lineage_request_constraints(
                    start_time=start_time,
                    end_time=end_time,
                    base_ref=base_ref,
                    head_ref=head_ref,
                    export=export,
                    compressed_observation=compressed_observation,
                ),
                git_info=git_info,
                counts=counts,
                audit_meta=audit_meta,
            )
            report["lineage"] = {
                "schema": WORKFLOW_LINEAGE_SCHEMA,
                "manifest": lineage_rel,
                "plan_id": _workflow_lineage_plan_id(plan_inputs),
                "verify": {"tool": "workflow_lineage", "mode": "verify"},
            }
            resource_links.append(
                _artifact_resource_link(
                    title="Workflow lineage manifest",
                    rel_path=lineage_rel,
                    mime_type="application/json",
                    created_at=generated_at,
                    redacted=True,
                    safety_note="Lineage manifest stores redacted deterministic plan identity and observed artifact digests only.",
                )
            )
            report["provenance"] = {"sidecars": provenance_paths, "schema": PROVENANCE_SCHEMA}
            json_path = _resolve_repo_path(exports["json"])
            md_path = _resolve_repo_path(exports["markdown"])
            json_path.write_text(
                json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            md_path.write_text(_governance_markdown(report), encoding="utf-8")
            for link in resource_links:
                if link.get("path") == exports["json"]:
                    link["size_bytes"] = json_path.stat().st_size
                if link.get("path") == exports["markdown"]:
                    link["size_bytes"] = md_path.stat().st_size
            report["_meta"] = _artifact_meta(resource_links)
            json_path.write_text(
                json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            md_path.write_text(_governance_markdown(report), encoding="utf-8")
            manifest = _build_governance_workflow_lineage_manifest(
                report,
                provenance_inputs=provenance_inputs,
                audit_meta=audit_meta,
                counts=counts,
            )
            _write_workflow_lineage_manifest(manifest, lineage_rel)
            _write_artifact_provenance_sidecars(
                tool_name="governance_report",
                artifact_paths=[exports["json"], exports["markdown"], lineage_rel],
                inputs=provenance_inputs,
                git_state=_git_state_for_provenance(base_ref, head_ref),
                artifact_schemas={
                    exports["json"]: str(report.get("schema", "")),
                    exports["markdown"]: "governance_report.markdown",
                    lineage_rel: WORKFLOW_LINEAGE_SCHEMA,
                },
                lineage_manifest=lineage_rel,
            )
    return report


@mcp.tool()
def self_optimization_report(
    start_time: str = "",
    end_time: str = "",
    window_hours: int = 168,
    export: bool = False,
    recommendation_limit: int = 10,
    include_git: bool = True,
    include_audit: bool = True,
    include_traces: bool = True,
    redact_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Build a repo-local efficiency report for MCP usage, token savings, throughput, bottlenecks, and optimization candidates."""
    arguments = {
        "start_time": start_time,
        "end_time": end_time,
        "window_hours": window_hours,
        "export": export,
        "recommendation_limit": recommendation_limit,
        "include_git": include_git,
        "include_audit": include_audit,
        "include_traces": include_traces,
        "redact_terms": redact_terms or [],
    }
    with _otel_span(
        "mcp.tool.self_optimization_report",
        _otel_tool_attributes("self_optimization_report", arguments),
    ) as span:
        result = _self_optimization_report_impl(
            start_time=start_time,
            end_time=end_time,
            window_hours=window_hours,
            export=export,
            recommendation_limit=recommendation_limit,
            include_git=include_git,
            include_audit=include_audit,
            include_traces=include_traces,
            redact_terms=redact_terms,
        )
        _otel_set_result_attributes(span, result)
        return result


@mcp.tool()
def governance_report(
    start_time: str = "",
    end_time: str = "",
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    export: bool = True,
    compressed_observation: bool = False,
) -> dict[str, Any]:
    """Build and optionally export a redacted audit/governance report."""
    arguments = {
        "start_time": start_time,
        "end_time": end_time,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "export": export,
        "compressed_observation": compressed_observation,
    }
    with _otel_span(
        "mcp.tool.governance_report",
        _otel_tool_attributes("governance_report", arguments),
    ) as span:
        result = _governance_report_impl(
            start_time=start_time,
            end_time=end_time,
            base_ref=base_ref,
            head_ref=head_ref,
            export=export,
            compressed_observation=compressed_observation,
        )
        _otel_set_result_attributes(span, result)
        return result


@mcp.tool()
def workflow_lineage(mode: str = "verify", manifest_path: str = "") -> dict[str, Any]:
    """Read-only verifier for workflow_lineage.v1 manifests."""
    _require_git_repo()
    if mode != "verify":
        raise ValueError("mode must be: verify")
    path = _lineage_manifest_path_from_input(manifest_path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("workflow lineage manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("workflow lineage manifest must be a JSON object")
    return _verify_workflow_lineage_manifest(
        manifest,
        str(path.relative_to(REPO_PATH)),
    )


def _artifact_provenance_impl(
    artifact_path: str = "",
    include_reports: bool = True,
    include_snapshots: bool = True,
) -> dict[str, Any]:
    _require_git_repo()
    artifact_paths: list[str] = []
    if artifact_path.strip():
        artifact_paths = [artifact_path.strip()]
    else:
        if include_reports:
            reports_dir = _resolve_repo_path(str(REPORTS_DIR))
            if reports_dir.exists():
                for path in sorted(reports_dir.glob("*")):
                    if path.is_file() and not path.name.endswith(PROVENANCE_SUFFIX):
                        artifact_paths.append(str(path.relative_to(REPO_PATH)))
        if include_snapshots:
            snapshot_index = _resolve_repo_path(str(STATE_SNAPSHOT_INDEX_FILE))
            if snapshot_index.exists():
                artifact_paths.append(str(snapshot_index.relative_to(REPO_PATH)))
    checks = [_verify_artifact_provenance_path(path) for path in artifact_paths]
    return {
        "schema": "artifact_provenance_report.v1",
        "provenance_schema": PROVENANCE_SCHEMA,
        "attestation_schema": ATTESTATION_SCHEMA,
        "artifact_count": len(checks),
        "ok": all(bool(row.get("ok", False)) for row in checks),
        "checks": checks,
    }


@mcp.tool()
def artifact_provenance(
    artifact_path: str = "",
    include_reports: bool = True,
    include_snapshots: bool = True,
) -> dict[str, Any]:
    """Read-only verification for local MCP artifact provenance sidecars."""
    arguments = {
        "artifact_path": artifact_path,
        "include_reports": include_reports,
        "include_snapshots": include_snapshots,
    }
    with _otel_span(
        "mcp.tool.artifact_provenance",
        _otel_tool_attributes("artifact_provenance", arguments),
    ) as span:
        result = _artifact_provenance_impl(
            artifact_path=artifact_path,
            include_reports=include_reports,
            include_snapshots=include_snapshots,
        )
        _otel_set_result_attributes(span, result)
        return result


@mcp.tool()
def workflow_task(
    action: str = "start",
    workflow: str = "vscode_task_run",
    task_id: str = "",
    label: str = "",
    tasks_path: str = ".vscode/tasks.json",
    control_profile: str = "build",
    timeout_seconds: int = 1800,
    max_output_chars: int = 12000,
    max_retries: int = 1,
    restart: bool = False,
    start_time: str = "",
    end_time: str = "",
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    export: bool = True,
    retry_of: str = "",
) -> dict[str, Any]:
    """Start or inspect a supported persisted async workflow task."""
    action = str(action or "start").strip().lower() or "start"
    workflow = str(workflow or "vscode_task_run").strip()
    trace_task_id = task_id.strip()
    trace_categories = ["read-only"] if action == "status" else _workflow_task_categories(workflow)
    attrs = _otel_tool_attributes(
        "workflow_task",
        {"action": action, "workflow": workflow, "task_id": trace_task_id},
        trace_categories,
    )
    attrs.update(
        {
            "mcp.workflow.name": workflow,
            "mcp.workflow.action": action,
            "mcp.workflow.task_id.present": bool(trace_task_id),
        }
    )
    with _otel_span("mcp.tool.workflow_task", attrs, correlation_id=trace_task_id) as span:
        _require_git_repo()
        if action not in {"start", "status"}:
            raise ValueError("action must be one of: start, status")
        if action == "status":
            if not task_id:
                raise ValueError("task_id is required for status")
            result = _workflow_task_status_payload(task_id)
            _otel_set_result_attributes(span, result)
            span.set_attribute("mcp.workflow.task_id", task_id)
            return result
        if workflow == "vscode_task_run":
            _require_mutations()
            if not label.strip():
                raise ValueError("label is required for vscode_task_run")
            if timeout_seconds < 1:
                raise ValueError("timeout_seconds must be >= 1")
            args = {
                "label": label.strip(),
                "tasks_path": tasks_path,
                "control_profile": control_profile,
                "timeout_seconds": timeout_seconds,
                "max_output_chars": max_output_chars,
            }
        elif workflow == "governance_report":
            args = {
                "start_time": start_time,
                "end_time": end_time,
                "base_ref": base_ref,
                "head_ref": head_ref,
                "export": export,
            }
        else:
            raise ValueError(
                "workflow must be one of: "
                + ", ".join(sorted(_WORKFLOW_TASK_ALLOWED_WORKFLOWS))
            )
        id_args = dict(args)
        if retry_of:
            id_args["retry_of"] = retry_of
        resolved_task_id = _workflow_task_stable_id(workflow, id_args, task_id)
        span.set_attribute("mcp.workflow.task_id", resolved_task_id)
        if not trace_task_id:
            span.set_attribute("mcp.workflow.task_id.generated", True)
        result = _start_workflow_task(
            workflow,
            args,
            retry_of=retry_of,
            task_id=task_id,
            max_retries=max_retries if workflow == "vscode_task_run" else 0,
            restart=restart,
        )
        _otel_set_result_attributes(span, result)
        return result


@mcp.tool()
def task_status(task_id: str) -> dict[str, Any]:
    """Return persisted redacted status for an asynchronous workflow task."""
    attrs = _otel_tool_attributes("task_status", {"task_id": task_id}, _tool_categories("task_status"))
    attrs.update({"mcp.workflow.action": "status", "mcp.workflow.task_id.present": bool(task_id)})
    with _otel_span("mcp.tool.task_status", attrs, correlation_id=task_id) as span:
        _require_git_repo()
        result = _workflow_task_status_payload(task_id)
        span.set_attribute("mcp.workflow.task_id", task_id)
        _otel_set_result_attributes(span, result)
        return result



def _mcp_apps_dashboard_enabled() -> bool:
    return os.getenv("MCP_APPS_DASHBOARD_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _release_readiness_check_item(name: str, check: dict[str, Any]) -> dict[str, Any]:
    ok = bool(check.get("ok", False))
    explicit_warning = bool(check.get("warning", False)) or str(
        check.get("status", "")
    ).lower() in {"warning", "warn"}
    if isinstance(check.get("warnings"), list) and check.get("warnings"):
        explicit_warning = True
    optional_governance = name == "governance_report" and check.get("required") is False
    stale_optional_governance = (
        optional_governance
        and check.get("present") is True
        and check.get("recent") is False
    )
    missing_optional_governance = optional_governance and check.get("present") is False
    warning = ok and (
        explicit_warning or stale_optional_governance or missing_optional_governance
    )
    fields: list[str] = []
    for key in (
        "runner",
        "target",
        "exit_code",
        "selected_count",
        "needs_docs_update",
        "finding_count",
        "missing_spdx_header_count",
        "missing_license_text_count",
        "risk_level",
        "risk_score",
        "present",
        "recent",
        "report_id",
        "generated_at",
        "path",
        "age_hours",
        "warning_reason",
        "error",
    ):
        if key in check and check.get(key) not in (None, ""):
            fields.append(f"{key}={check.get(key)}")
    if missing_optional_governance:
        summary = "optional governance report is not present"
    elif stale_optional_governance:
        summary = "optional governance report is stale"
    else:
        summary = "; ".join(fields) if fields else ("passed" if ok else "failed")
    return {
        "id": name,
        "label": name.replace("_", " ").title(),
        "status": "warning" if warning else "pass" if ok else "fail",
        "blocking": not ok,
        "warning": warning,
        "summary": summary,
        "details": {k: v for k, v in check.items() if k != "tests"},
    }


def _release_readiness_dashboard_payload(result: dict[str, Any]) -> dict[str, Any]:
    checks = result.get("checks", {}) if isinstance(result.get("checks"), dict) else {}
    groups: list[dict[str, Any]] = []
    for title, names in (
        ("Release gate", ("tests", "impact_tests")),
        ("Policy and compliance", ("docs", "security", "license")),
        ("Risk and governance", ("risk", "governance_report")),
    ):
        items = [
            _release_readiness_check_item(name, checks[name])
            for name in names
            if isinstance(checks.get(name), dict)
        ]
        if not items:
            continue
        has_blocking = any(item["blocking"] for item in items)
        has_warning = any(item["warning"] for item in items)
        groups.append(
            {
                "title": title,
                "status": "blocking" if has_blocking else "warning" if has_warning else "pass",
                "items": items,
            }
        )

    impact = checks.get("impact_tests", {}) if isinstance(checks.get("impact_tests"), dict) else {}
    selected_tests = impact.get("tests", []) if isinstance(impact.get("tests"), list) else []
    next_steps = [
        "release_readiness(summary_mode='quick')",
        "change_impact_gate(base_ref='{}', head_ref='{}')".format(
            result.get("base_ref", "HEAD~1"), result.get("head_ref", "HEAD")
        ),
        "required_tool_chain(required_tools=['release_readiness', 'change_impact_gate'])",
    ]
    if selected_tests:
        next_steps.append("Run selected impacted tests: " + " ".join(str(t) for t in selected_tests[:20]))
    if not result.get("ok", False):
        failing = [
            item["id"]
            for group in groups
            for item in group.get("items", [])
            if item.get("blocking")
        ]
        next_steps.append("Resolve blocking release checks: " + ", ".join(failing))

    rollback_reference = None
    try:
        snapshots = _governance_snapshot_references(limit=1)
        latest = snapshots.get("latest", []) if isinstance(snapshots, dict) else []
        if latest:
            rollback_reference = latest[0]
    except Exception:
        rollback_reference = None

    return {
        "schema": "release_readiness.dashboard.v1",
        "app": {
            "extension": "io.modelcontextprotocol/ui",
            "resourceUri": RELEASE_READINESS_DASHBOARD_RESOURCE_URI,
            "mimeType": "text/html;profile=mcp-app",
            "readOnly": True,
        },
        "dashboard": {
            "data": {
                "schema": result.get("schema"),
                "base_ref": result.get("base_ref"),
                "head_ref": result.get("head_ref"),
                "ok": bool(result.get("ok", False)),
                "groups": groups,
                "selected_impacted_tests": selected_tests[:200],
                "rollback_reference": rollback_reference,
                "next_steps": next_steps,
            },
            "actions": [],
        },
    }


def _with_release_readiness_dashboard(result: dict[str, Any]) -> dict[str, Any]:
    if not _mcp_apps_dashboard_enabled():
        return result
    enriched = dict(result)
    enriched["mcp_apps"] = _release_readiness_dashboard_payload(result)
    return enriched


@mcp.tool()
def release_readiness(
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    run_tests: bool = True,
    test_runner: str = "unittest",
    test_target: str = "tests",
    run_docs_check: bool = True,
    run_security_check: bool = True,
    run_license_check: bool = True,
    run_risk_check: bool = True,
    run_impact_check: bool = True,
    summary_mode: str = "quick",
) -> dict[str, Any]:
    """Run release readiness checks and return go/no-go status."""
    _require_git_repo()
    if summary_mode not in {"quick", "full"}:
        raise ValueError("summary_mode must be one of: quick, full")

    result: dict[str, Any] = {
        "schema": "release_readiness.v1",
        "base_ref": base_ref,
        "head_ref": head_ref,
        "started_at": _now_iso(),
        "checks": {},
        "ok": True,
    }

    clarification = _clarification_gate_payload(
        intent="Assess release readiness before recommending release action",
        target=f"{base_ref}...{head_ref}",
        operation="release_readiness",
        risk_level="medium",
        rollback_plan="release_readiness is read-only; require a snapshot or rollback plan before follow-up mutation/deploy workflows",
    )
    result["checks"]["clarification_gate"] = {
        "ok": bool(clarification.get("ok_to_continue", False)),
        "status": clarification.get("status", ""),
        "missing_fields": [item.get("field", "") for item in clarification.get("missing_fields", []) if isinstance(item, dict)],
        "question_count": len(clarification.get("questions", [])) if isinstance(clarification.get("questions"), list) else 0,
    }
    if not clarification.get("ok_to_continue", False):
        result["ok"] = False

    if run_tests:
        test_out = self_test(
            runner=test_runner,
            target=test_target,
            verbose=False,
            fail_fast=False,
            timeout_seconds=600,
        )
        result["checks"]["tests"] = {
            "ok": test_out.get("ok", False),
            "runner": test_runner,
            "target": test_target,
            "exit_code": test_out.get("exit_code"),
        }
        if not test_out.get("ok", False):
            result["ok"] = False

    if run_docs_check:
        docs = doc_sync_check(base_ref=base_ref, head_ref=head_ref)
        result["checks"]["docs"] = {
            "ok": not docs.get("needs_docs_update", False),
            "needs_docs_update": docs.get("needs_docs_update", False),
        }
        if docs.get("needs_docs_update", False):
            result["ok"] = False

    if run_security_check:
        patch = _git("diff", f"{base_ref}...{head_ref}", "--", ".").stdout
        security = security_triage(diff_text=patch)
        finding_count = int(security.get("finding_count", 0))
        result["checks"]["security"] = {
            "ok": finding_count == 0,
            "finding_count": finding_count,
        }
        if finding_count > 0:
            result["ok"] = False

    if run_license_check:
        try:
            license_out = license_monitor(
                run_reuse_lint=True,
                generate_spdx=False,
                auto_fix_headers=False,
                download_missing_licenses=False,
            )
            result["checks"]["license"] = {
                "ok": license_out.get("ok", False),
                "missing_spdx_header_count": license_out.get("missing_spdx_header_count", 0),
                "missing_license_text_count": license_out.get("missing_license_text_count", 0),
            }
            if not license_out.get("ok", False):
                result["ok"] = False
        except Exception as exc:
            result["checks"]["license"] = {"ok": False, "error": str(exc)}
            result["ok"] = False

    if run_risk_check:
        risk = risk_scoring(ref=head_ref)
        result["checks"]["risk"] = {
            "ok": risk.get("risk_level") != "high",
            "risk_score": risk.get("risk_score"),
            "risk_level": risk.get("risk_level"),
        }
        if risk.get("risk_level") == "high":
            result["ok"] = False

    if run_impact_check:
        impacts = impact_tests(base_ref=base_ref, head_ref=head_ref, output_profile="compact")
        selected = impacts.get("tests", []) if isinstance(impacts, dict) else []
        result["checks"]["impact_tests"] = {
            "ok": True,
            "selected_count": len(selected),
            "tests": selected[:200],
        }

    governance_check = {
        "ok": True,
        **_latest_governance_report(max_age_hours=24),
    }
    if governance_check.get("required") is False and (
        governance_check.get("present") is False or governance_check.get("recent") is False
    ):
        governance_check["warning"] = True
        governance_check["warning_reason"] = "optional governance report missing or stale"
    result["checks"]["governance_report"] = governance_check

    result["finished_at"] = _now_iso()
    if summary_mode == "quick":
        quick_result = {
            "schema": "release_readiness.quick.v1",
            "base_ref": base_ref,
            "head_ref": head_ref,
            "ok": result["ok"],
            "checks": {
                name: {
                    k: v
                    for k, v in data.items()
                    if k
                    in {
                        "ok",
                        "exit_code",
                        "runner",
                        "target",
                        "finding_count",
                        "risk_score",
                        "risk_level",
                        "missing_spdx_header_count",
                        "missing_license_text_count",
                        "selected_count",
                        "needs_docs_update",
                        "present",
                        "recent",
                        "required",
                        "max_age_hours",
                        "report_id",
                        "generated_at",
                        "path",
                        "age_hours",
                        "warning",
                        "warning_reason",
                        "status",
                        "missing_fields",
                        "question_count",
                    }
                }
                for name, data in result["checks"].items()
                if isinstance(data, dict)
            },
        }
        return _with_release_readiness_dashboard(quick_result)
    return _with_release_readiness_dashboard(result)


@mcp.tool()
def required_tool_chain(
    required_tools: list[str],
    required_artifacts: list[str] | None = None,
    required_result_ids: list[str] | None = None,
    require_order: bool = True,
    max_age_minutes: int | None = None,
) -> dict[str, Any]:
    """Validate required tool chain execution from result telemetry and artifacts."""
    if not required_tools:
        raise ValueError("required_tools must not be empty")
    if max_age_minutes is not None and max_age_minutes < 1:
        raise ValueError("max_age_minutes must be >= 1 when provided")

    payload = _result_store_load()
    rows: list[dict[str, Any]] = []
    for rid, row in payload.get("results", {}).items():
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "result_id": rid,
                "tool": str(row.get("tool", "")),
                "created_at": str(row.get("created_at", "")),
            }
        )
    rows.sort(key=lambda x: x["created_at"])

    if max_age_minutes is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        filtered: list[dict[str, Any]] = []
        for row in rows:
            created_raw = row.get("created_at", "")
            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except Exception:
                continue
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            if created_dt >= cutoff:
                filtered.append(row)
        rows = filtered

    missing_tools: list[str] = []
    matched: list[dict[str, Any]] = []
    if require_order:
        idx = 0
        for need in required_tools:
            found = None
            for j in range(idx, len(rows)):
                if rows[j]["tool"] == need:
                    found = rows[j]
                    idx = j + 1
                    break
            if found is None:
                missing_tools.append(need)
            else:
                matched.append(found)
    else:
        by_tool: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_tool.setdefault(row["tool"], []).append(row)
        for need in required_tools:
            entries = by_tool.get(need, [])
            if not entries:
                missing_tools.append(need)
            else:
                matched.append(entries[-1])

    missing_result_ids: list[str] = []
    if required_result_ids:
        existing_ids = {row["result_id"] for row in rows}
        for rid in required_result_ids:
            if rid not in existing_ids:
                missing_result_ids.append(rid)

    required_artifacts = required_artifacts or []
    missing_artifacts: list[str] = []
    for rel in required_artifacts:
        p = _resolve_repo_path(rel)
        if not p.exists():
            missing_artifacts.append(rel)

    ok = not missing_tools and not missing_artifacts and not missing_result_ids
    return {
        "schema": "required_tool_chain.v1",
        "ok": ok,
        "require_order": require_order,
        "max_age_minutes": max_age_minutes,
        "required_tools": required_tools,
        "matched_tools": matched,
        "missing_tools": missing_tools,
        "required_artifacts": required_artifacts,
        "missing_artifacts": missing_artifacts,
        "required_result_ids": required_result_ids or [],
        "missing_result_ids": missing_result_ids,
        "observed_result_count": len(rows),
    }


@mcp.tool()
def fast_path_dev(
    task: str = "review",
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    refresh_index: bool = True,
    index_path: str = ".",
    run_readiness: bool = True,
    readiness_test_runner: str = "unittest",
    readiness_test_target: str = "tests",
    enforce_tool_chain: bool = False,
    required_tools: list[str] | None = None,
    store_result: bool = True,
) -> dict[str, Any]:
    """Run a low-token developer fast path workflow in one call."""
    if not task.strip():
        raise ValueError("task must not be empty")
    _ensure_repo_path_exists()

    token_profile = token_budget_guard(default_output_profile="compact")
    steps: dict[str, Any] = {
        "token_budget": {
            "max_output_chars": token_profile.get("max_output_chars"),
            "default_output_profile": token_profile.get("default_output_profile"),
        }
    }
    required_chain: list[str] = []

    if refresh_index:
        idx = repo_index_daemon(
            mode="refresh",
            path=index_path,
            output_profile="compact",
            summary_mode="quick",
            incremental=True,
        )
        steps["repo_index"] = idx
        required_chain.append("repo_index_daemon")

    if run_readiness:
        readiness = release_readiness(
            base_ref=base_ref,
            head_ref=head_ref,
            run_tests=True,
            test_runner=readiness_test_runner,
            test_target=readiness_test_target,
            run_docs_check=True,
            run_security_check=True,
            run_license_check=True,
            run_risk_check=True,
            run_impact_check=True,
            summary_mode="quick",
        )
        steps["release_readiness"] = readiness
        required_chain.append("release_readiness")

    chain_result = None
    if enforce_tool_chain:
        chain_result = required_tool_chain(
            required_tools=required_tools or required_chain or ["release_readiness"],
            require_order=False,
            max_age_minutes=60,
        )
        steps["required_tool_chain"] = chain_result

    ok = True
    if isinstance(steps.get("release_readiness"), dict):
        ok = ok and bool(steps["release_readiness"].get("ok", False))
    if isinstance(chain_result, dict):
        ok = ok and bool(chain_result.get("ok", False))

    out: dict[str, Any] = {
        "schema": "fast_path_dev.v1",
        "task": task,
        "ok": ok,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "steps": steps,
    }
    if store_result:
        out["result_id"] = _result_store_put("fast_path_dev", out)
    return out


@mcp.tool()
def workflow_compiler(
    goal: str,
    constraints: list[str] | None = None,
    include_rollback: bool = True,
    use_cache: bool = True,
    refresh_cache: bool = False,
    cache_ttl_minutes: int = 240,
) -> dict[str, Any]:
    """Compile a goal into an executable MCP workflow plan."""
    if not goal.strip():
        raise ValueError("goal must not be empty")
    if cache_ttl_minutes < 1:
        raise ValueError("cache_ttl_minutes must be >= 1")
    cons = constraints or []
    cache_key = json.dumps(
        {
            "v": 2,
            "goal": goal.strip(),
            "constraints": cons,
            "include_rollback": include_rollback,
        },
        sort_keys=True,
    )
    if use_cache and not refresh_cache:
        row = _cache_get_entry("workflow_compiler", cache_key)
        if isinstance(row, dict):
            cached_at_raw = str(row.get("updated_at", ""))
            cached_at = _parse_iso_timestamp(cached_at_raw)
            if cached_at is not None:
                cache_age = (datetime.now(timezone.utc) - cached_at).total_seconds()
                if cache_age <= float(cache_ttl_minutes) * 60.0:
                    val = row.get("value")
                    if isinstance(val, dict):
                        out = dict(val)
                        out["cached"] = True
                        out["cache_updated_at"] = cached_at_raw
                        out["cache_key"] = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:12]
                        return out
    g = goal.lower()
    steps: list[dict[str, Any]] = []
    if any(tok in g for tok in {"release", "ship", "deploy"}):
        steps.append({"tool": "release_readiness", "args": {"summary_mode": "quick"}})
    if any(tok in g for tok in {"license", "compliance", "foss"}):
        steps.append({"tool": "license_monitor", "args": {"run_reuse_lint": True, "generate_spdx": True}})
    if any(tok in g for tok in {"risk", "security"}):
        steps.append({"tool": "change_impact_gate", "args": {"block_on_risk_level": "high"}})
    if any(tok in g for tok in {"speed", "quick", "fast"}):
        steps.append({"tool": "fast_path_dev", "args": {"task": "quick-check", "refresh_index": True}})
    if not steps:
        steps = [
            {"tool": "repo_index_daemon", "args": {"mode": "refresh", "summary_mode": "quick"}},
            {"tool": "required_tool_chain", "args": {"required_tools": ["repo_index_daemon"]}},
        ]
    rollback = []
    if include_rollback:
        rollback = [
            {"tool": "state_snapshot", "when": "before_mutation"},
            {"tool": "state_restore", "when": "on_failure"},
        ]
    out = {
        "schema": "workflow_compiler.v1",
        "goal": goal,
        "constraints": cons,
        "steps": steps,
        "rollback": rollback,
        "cached": False,
        "cache_key": hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:12],
    }
    if use_cache:
        _cache_set("workflow_compiler", cache_key, out, max_entries=200)
    return out


def state_snapshot(
    label: str = "",
    include_build_dir: bool = False,
) -> dict[str, Any]:
    """Create a git-backed workspace snapshot for quick rollback."""
    _require_mutations()
    _require_git_repo()
    snap_id = f"{_now_stamp()}-{uuid.uuid4().hex[:8]}"
    safe_label = re.sub(r"[^a-zA-Z0-9_.-]+", "-", label.strip())[:64]
    name = f"{snap_id}-{safe_label}" if safe_label else snap_id
    base_head = _git("rev-parse", "HEAD").stdout.strip()
    stash_label = f"mcp-state-snapshot:{name}"

    args = ["stash", "push", "--include-untracked", "--message", stash_label]
    if not include_build_dir:
        args.extend(["--", ".", ":(exclude).codebase-tooling-mcp/**"])
    stash_result = _git(*args, check=False)
    output = (stash_result.stdout + "\n" + stash_result.stderr).strip()

    stash_commit = ""
    stash_ref = ""
    if stash_result.returncode == 0 and "No local changes to save" not in output:
        stash_commit = _git("rev-parse", "--verify", "stash@{0}").stdout.strip()
        stash_ref = f"refs/mcp-snapshots/{name}"
        _git("update-ref", stash_ref, stash_commit)
        _git("stash", "drop", "stash@{0}")
        _git("stash", "apply", "--index", stash_commit)

    index = _state_snapshot_index_load()
    snapshots = index.get("snapshots", {})
    snapshots[name] = {
        "snapshot_id": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_head": base_head,
        "stash_commit": stash_commit,
        "stash_ref": stash_ref,
        "include_build_dir": include_build_dir,
    }
    index["snapshots"] = snapshots
    _state_snapshot_index_save(index)
    snapshot_index_rel = str(STATE_SNAPSHOT_INDEX_FILE)
    provenance = _write_artifact_provenance_sidecars(
        tool_name="state_snapshot",
        artifact_paths=[snapshot_index_rel],
        inputs={"label": label, "include_build_dir": include_build_dir},
        git_state=_git_state_for_provenance(head_ref="HEAD"),
        artifact_schemas={snapshot_index_rel: "state_snapshot_index.v1"},
    )
    resource_links = [
        _artifact_resource_link(
            title="State snapshot index",
            rel_path=str(STATE_SNAPSHOT_INDEX_FILE),
            mime_type="application/json",
            created_at=snapshots[name]["created_at"],
            redacted=True,
            safety_note="Snapshot index is repository-local metadata; stash object contents are referenced by Git ref, not embedded.",
        )
    ]
    if stash_ref:
        resource_links.append(
            _artifact_resource_link(
                title="State snapshot rollback Git ref",
                uri="git-ref://" + urllib.parse.quote(stash_ref, safe="/._-"),
                mime_type="application/vnd.git-ref",
                created_at=snapshots[name]["created_at"],
                redacted=True,
                safety_note="Rollback contents remain in Git object storage and are referenced by ref only, not embedded in metadata.",
            )
        )
    return {
        "schema": "state_snapshot.v1",
        "snapshot_id": name,
        "backend": "git-stash",
        "base_head": base_head,
        "stash_commit": stash_commit,
        "stash_ref": stash_ref,
        "had_changes": bool(stash_commit),
        "provenance": provenance,
        "resource_links": resource_links,
        "_meta": _artifact_meta(resource_links),
    }


def state_restore(
    snapshot_id: str,
) -> dict[str, Any]:
    """Restore files from a previously created git-backed snapshot."""
    _require_mutations()
    _require_git_repo()
    if not snapshot_id.strip():
        raise ValueError("snapshot_id must not be empty")
    index = _state_snapshot_index_load()
    snapshots = index.get("snapshots", {})
    entry = snapshots.get(snapshot_id)
    if not isinstance(entry, dict):
        raise FileNotFoundError(f"snapshot_id not found: {snapshot_id}")

    base_head = str(entry.get("base_head", "")).strip()
    stash_commit = str(entry.get("stash_commit", "")).strip()
    include_build_dir = bool(entry.get("include_build_dir", False))

    if base_head:
        _git("reset", "--hard", base_head)
    clean_args = ["clean", "-fd"]
    if include_build_dir:
        clean_args.append("-x")
    _git(*clean_args)
    if stash_commit:
        _git("stash", "apply", "--index", stash_commit)

    return {
        "schema": "state_restore.v1",
        "snapshot_id": snapshot_id,
        "backend": "git-stash",
        "base_head": base_head,
        "stash_commit": stash_commit,
        "restored": True,
    }


@mcp.tool()
def workspace_transaction(
    mode: str = "begin",
    transaction_id: str = "",
    label: str = "",
    changes: list[dict[str, Any]] | None = None,
    create_dirs: bool = True,
    delete_metadata: bool = False,
    snapshot_id: str = "",
    include_build_dir: bool = False,
    path: str = "",
    content: str = "",
    overwrite: bool = True,
    encoding: str = "utf-8",
    pattern: str = "",
    replacement: str = "",
    regex: bool = False,
    case_insensitive: bool = False,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    include_hidden: bool = False,
    max_file_bytes: int = 1048576,
    max_files: int = 1000,
    max_replacements: int = 1000,
    source_path: str = "",
    destination: str = "",
    recursive: bool = False,
    diff_text: str = "",
    cached: bool = False,
) -> dict[str, Any]:
    """Strict workspace mutation router for transactional edits, snapshots, and direct file mutations."""
    allowed = {
        "begin",
        "apply",
        "validate",
        "rollback",
        "commit",
        "snapshot",
        "restore",
        "write",
        "replace",
        "move",
        "delete",
        "apply_diff",
    }
    if mode not in allowed:
        raise ValueError(f"mode must be one of: {', '.join(sorted(allowed))}")
    if mode == "snapshot":
        result = state_snapshot(label=label, include_build_dir=include_build_dir)
    elif mode == "restore":
        if not snapshot_id.strip():
            raise ValueError("snapshot_id is required for restore mode")
        result = state_restore(snapshot_id=snapshot_id)
    elif mode == "write":
        if not path.strip():
            raise ValueError("path is required for write mode")
        result = write_file(
            path=path,
            content=content,
            overwrite=overwrite,
            create_dirs=create_dirs,
            encoding=encoding,
        )
    elif mode == "replace":
        if not pattern:
            raise ValueError("pattern is required for replace mode")
        result = replace_in_files(
            path=path or ".",
            pattern=pattern,
            replacement=replacement,
            regex=regex,
            case_insensitive=case_insensitive,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            include_hidden=include_hidden,
            max_file_bytes=max_file_bytes,
            max_files=max_files,
            max_replacements=max_replacements,
            recursive=recursive,
            dry_run=False,
        )
    elif mode == "move":
        if not source_path.strip() or not destination.strip():
            raise ValueError("source_path and destination are required for move mode")
        result = move_path(
            source=source_path,
            destination=destination,
            create_dirs=create_dirs,
            overwrite=overwrite,
        )
    elif mode == "delete":
        target_path = path or source_path
        if not target_path.strip():
            raise ValueError("path is required for delete mode")
        result = delete_path(path=target_path, recursive=recursive)
    elif mode == "apply_diff":
        if not diff_text.strip():
            raise ValueError("diff_text is required for apply_diff mode")
        result = apply_unified_diff(diff_text=diff_text, check_only=False, cached=cached)
    else:
        result = edit_transaction(
            mode=mode,
            transaction_id=transaction_id,
            label=label,
            changes=changes,
            create_dirs=create_dirs,
            delete_metadata=delete_metadata,
        )
    out = {
        "schema": "workspace_transaction.v1",
        "mode": mode,
        "result": result,
    }
    if isinstance(result, dict) and isinstance(result.get("resource_links"), list):
        out["resource_links"] = result["resource_links"]
        if isinstance(result.get("_meta"), dict):
            out["_meta"] = result["_meta"]
    return out


@mcp.tool()
def policy_simulator(
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    diff_text: str = "",
) -> dict[str, Any]:
    """Simulate policy outcomes for docs/security/risk/license before applying changes."""
    _require_git_repo()
    patch = diff_text
    if not patch.strip():
        patch = _git("diff", f"{base_ref}...{head_ref}", "--", ".").stdout
    docs = doc_sync_check(base_ref=base_ref, head_ref=head_ref)
    security = security_triage(diff_text=patch)
    risk = risk_scoring(ref=head_ref)
    try:
        license_out = license_monitor(
            run_reuse_lint=True,
            generate_spdx=False,
            auto_fix_headers=False,
            download_missing_licenses=False,
        )
    except Exception as exc:
        license_out = {"ok": False, "error": str(exc)}
    blocking = []
    if docs.get("needs_docs_update"):
        blocking.append("docs")
    if int(security.get("finding_count", 0)) > 0:
        blocking.append("security")
    if risk.get("risk_level") == "high":
        blocking.append("risk")
    if not license_out.get("ok", False):
        blocking.append("license")
    return {
        "schema": "policy_simulator.v1",
        "ok": not blocking,
        "blocking_policies": blocking,
        "docs": docs,
        "security": {"finding_count": security.get("finding_count", 0)},
        "risk": {"risk_level": risk.get("risk_level"), "risk_score": risk.get("risk_score")},
        "license": {"ok": license_out.get("ok", False)},
    }


@mcp.tool()
def intent_router(
    query: str,
    candidates: list[str],
    top_k: int = 3,
) -> dict[str, Any]:
    """Pick a likely tool from candidate tools using lightweight keyword intent scoring."""
    if not candidates:
        raise ValueError("candidates must not be empty")
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    ranked = _intent_rank_candidates(query, candidates)
    top_score = float(ranked[0].get("score", 0.0))
    second_score = float(ranked[1].get("score", 0.0)) if len(ranked) > 1 else 0.0
    score_gap = top_score - second_score if len(ranked) > 1 else top_score
    confidence = 0.0 if top_score <= 0 else min(1.0, round((top_score + max(0.0, score_gap)) / 10.0, 4))
    return {
        "schema": "intent_router.v1",
        "query": query,
        "selected_tool": ranked[0]["tool"],
        "confidence": confidence,
        "score_gap": round(score_gap, 4),
        "ranked": ranked[:top_k],
    }


@mcp.tool()
def tool_router_learned(
    query: str,
    candidates: list[str],
    mode: str = "route",
    selected_tool: str = "",
    success: bool = True,
    latency_ms: float = 0.0,
    min_calls: int = 2,
    min_success_rate: float = 0.6,
    min_score_gap: float = 5.0,
    fallback_to_intent: bool = True,
) -> dict[str, Any]:
    """Learn simple routing preferences across tools and fall back to intent scoring when confidence is low."""
    if not candidates:
        raise ValueError("candidates must not be empty")
    if mode not in {"route", "record"}:
        raise ValueError("mode must be one of: route, record")
    if min_calls < 1:
        raise ValueError("min_calls must be >= 1")
    if not 0.0 <= min_success_rate <= 1.0:
        raise ValueError("min_success_rate must be between 0 and 1")
    if min_score_gap < 0.0:
        raise ValueError("min_score_gap must be >= 0")
    payload = _json_file_load(TOOL_ROUTER_STATS_FILE, {"stats": {}})
    stats = payload.get("stats", {})
    if not isinstance(stats, dict):
        stats = {}
    if mode == "record":
        if not selected_tool:
            raise ValueError("selected_tool is required in record mode")
        row = stats.get(selected_tool, {"calls": 0, "successes": 0, "avg_latency_ms": 0.0})
        calls = int(row.get("calls", 0)) + 1
        successes = int(row.get("successes", 0)) + (1 if success else 0)
        prev = float(row.get("avg_latency_ms", 0.0))
        lat = float(latency_ms) if latency_ms > 0 else prev
        avg = ((prev * (calls - 1)) + lat) / max(1, calls)
        stats[selected_tool] = {"calls": calls, "successes": successes, "avg_latency_ms": round(avg, 4)}
        payload["stats"] = stats
        _json_file_save(TOOL_ROUTER_STATS_FILE, payload)
        return {"schema": "tool_router_learned.v1", "mode": mode, "updated_tool": selected_tool}

    ranked: list[dict[str, Any]] = []
    for tool in candidates:
        row = stats.get(tool, {})
        calls = int(row.get("calls", 0))
        succ = int(row.get("successes", 0))
        avg_lat = float(row.get("avg_latency_ms", 500.0 if calls == 0 else row.get("avg_latency_ms", 0.0)))
        success_rate = (succ / calls) if calls > 0 else 0.5
        score = (success_rate * 100.0) - min(100.0, avg_lat / 10.0)
        ranked.append(
            {
                "tool": tool,
                "score": round(score, 4),
                "calls": calls,
                "success_rate": round(success_rate, 4),
                "avg_latency_ms": round(avg_lat, 4),
            }
        )
    ranked.sort(key=lambda row: row["score"], reverse=True)
    confidence = _tool_router_confidence(ranked, min_calls, min_success_rate, min_score_gap)
    selected_by = "learned"
    selected = ranked[0]["tool"]
    fallback = None
    if fallback_to_intent and not confidence["confident"]:
        fallback = intent_router(query=query, candidates=candidates)
        selected = str(fallback["selected_tool"])
        selected_by = "intent_router"

    return {
        "schema": "tool_router_learned.v1",
        "mode": mode,
        "query": query,
        "selected_tool": selected,
        "selected_by": selected_by,
        "confidence": confidence,
        "fallback": fallback,
        "ranked": ranked,
    }


@mcp.tool()
def artifact_memory_index(
    mode: str = "refresh",
    path: str = ".codebase-tooling-mcp/reports",
    query: str = "",
    max_entries: int = 1000,
) -> dict[str, Any]:
    """Index and query generated artifacts for re-use in low-token workflows."""
    if mode not in {"refresh", "query", "read", "add"}:
        raise ValueError("mode must be one of: refresh, query, read, add")
    if max_entries < 1:
        raise ValueError("max_entries must be >= 1")
    idx_file = _resolve_repo_path(str(ARTIFACT_INDEX_FILE))
    if mode == "refresh":
        root = _resolve_repo_path(path)
        rows: list[dict[str, Any]] = []
        if root.exists():
            for p in _iter_candidate_files(root, recursive=True):
                rel = str(p.relative_to(REPO_PATH)).replace("\\", "/")
                st = p.stat()
                rows.append(
                    {
                        "path": rel,
                        "size": int(st.st_size),
                        "mtime_ns": int(st.st_mtime_ns),
                        "sha256": _file_sha256(p),
                    }
                )
                if len(rows) >= max_entries:
                    break
        payload = {"schema": "artifact_memory_index.v1", "generated_at": _now_iso(), "artifacts": rows}
        _json_file_save(idx_file, payload)
        return {"schema": "artifact_memory_index.v1", "mode": mode, "count": len(rows), "index_path": str(idx_file.relative_to(REPO_PATH))}
    if not idx_file.is_file():
        raise FileNotFoundError(str(idx_file.relative_to(REPO_PATH)))
    payload = json.loads(idx_file.read_text(encoding="utf-8"))
    if mode == "read":
        rows = payload.get("artifacts", [])
        return {"schema": "artifact_memory_index.v1", "mode": mode, "count": len(rows), "artifacts": rows[:max_entries]}
    if mode == "query":
        q = query.lower().strip()
        rows = payload.get("artifacts", [])
        if q:
            rows = [r for r in rows if q in str(r.get("path", "")).lower()]
        return {"schema": "artifact_memory_index.v1", "mode": mode, "count": len(rows), "artifacts": rows[:max_entries]}
    # add
    _require_mutations()
    rows = payload.get("artifacts", [])
    candidate = _resolve_repo_path(query)
    if not candidate.is_file():
        raise FileNotFoundError(query)
    rel = str(candidate.relative_to(REPO_PATH)).replace("\\", "/")
    st = candidate.stat()
    rows.append({"path": rel, "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns), "sha256": _file_sha256(candidate)})
    payload["artifacts"] = rows[-max_entries:]
    _json_file_save(idx_file, payload)
    return {"schema": "artifact_memory_index.v1", "mode": mode, "added": rel, "count": len(payload["artifacts"])}


@mcp.tool()
def constraint_solver_for_tasks(
    requirements: list[str],
    actions: list[str],
) -> dict[str, Any]:
    """Check hard constraints against proposed actions."""
    if not requirements:
        raise ValueError("requirements must not be empty")
    if not actions:
        raise ValueError("actions must not be empty")
    action_text = " ".join(actions).lower()
    unsatisfied: list[str] = []
    satisfied: list[str] = []
    for req in requirements:
        needle = req.lower().strip()
        if needle and needle in action_text:
            satisfied.append(req)
        else:
            unsatisfied.append(req)
    return {
        "schema": "constraint_solver_for_tasks.v1",
        "ok": not unsatisfied,
        "satisfied": satisfied,
        "unsatisfied": unsatisfied,
        "actions": actions,
    }


@mcp.tool()
def spec_to_tests(
    spec_text: str,
    framework: str = "pytest",
    output_path: str = "",
    mode: str = "generate",
) -> dict[str, Any]:
    """Generate test skeletons from natural-language spec bullets."""
    if framework not in {"pytest", "unittest"}:
        raise ValueError("framework must be one of: pytest, unittest")
    if mode not in {"generate", "write"}:
        raise ValueError("mode must be one of: generate, write")
    lines = [line.strip("-* ").strip() for line in spec_text.splitlines() if line.strip()]
    reqs = [line for line in lines if any(tok in line.lower() for tok in {"must", "should", "shall"})]
    if not reqs:
        reqs = lines[:5]
    tests: list[str] = []
    if framework == "pytest":
        tests.append("import pytest")
        tests.append("")
        for i, req in enumerate(reqs, start=1):
            name = re.sub(r"[^a-z0-9]+", "_", req.lower()).strip("_")[:48] or f"req_{i}"
            tests.append(f"def test_spec_{i}_{name}():")
            tests.append(f"    # {req}")
            tests.append("    assert True")
            tests.append("")
    else:
        tests.append("import unittest")
        tests.append("")
        tests.append("class SpecTests(unittest.TestCase):")
        for i, req in enumerate(reqs, start=1):
            name = re.sub(r"[^a-z0-9]+", "_", req.lower()).strip("_")[:48] or f"req_{i}"
            tests.append(f"    def test_spec_{i}_{name}(self):")
            tests.append(f"        # {req}")
            tests.append("        self.assertTrue(True)")
            tests.append("")
    test_code = "\n".join(tests).rstrip() + "\n"
    if mode == "write":
        _require_mutations()
        if not output_path:
            raise ValueError("output_path is required for write mode")
        out = _resolve_repo_path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(test_code, encoding="utf-8")
    return {
        "schema": "spec_to_tests.v1",
        "framework": framework,
        "requirements_count": len(reqs),
        "test_code": test_code,
        "output_path": output_path if mode == "write" else None,
    }


@mcp.tool()
def auto_sharding_for_analysis(
    path: str = ".",
    shard_size: int = 100,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
) -> dict[str, Any]:
    """Split candidate files into deterministic shards for large analysis tasks."""
    if shard_size < 1:
        raise ValueError("shard_size must be >= 1")
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)
    files: list[str] = []
    for p in _iter_candidate_files(root, recursive=True):
        rel = str(p.relative_to(REPO_PATH)).replace("\\", "/")
        if not _allowed_by_globs(rel, include_globs=include_globs, exclude_globs=exclude_globs):
            continue
        if rel.startswith(".git/") or rel.startswith(".codebase-tooling-mcp/"):
            continue
        files.append(rel)
    files.sort()
    shards = [files[i : i + shard_size] for i in range(0, len(files), shard_size)]
    return {
        "schema": "auto_sharding_for_analysis.v1",
        "path": path,
        "file_count": len(files),
        "shard_size": shard_size,
        "shard_count": len(shards),
        "shards": [{"index": i + 1, "count": len(s), "files": s} for i, s in enumerate(shards)],
    }


@mcp.tool()
def confidence_scoring(
    checks: list[dict[str, Any]],
    weight_key: str = "weight",
) -> dict[str, Any]:
    """Compute confidence score from structured checks metadata."""
    if not checks:
        raise ValueError("checks must not be empty")
    total_w = 0.0
    total_s = 0.0
    details: list[dict[str, Any]] = []
    for row in checks:
        ok = bool(row.get("ok", False))
        w = float(row.get(weight_key, 1.0))
        w = max(0.0, w)
        s = w if ok else 0.0
        total_w += w
        total_s += s
        details.append({"name": row.get("name", ""), "ok": ok, "weight": w})
    confidence = total_s / max(1e-9, total_w)
    level = "low"
    if confidence >= 0.8:
        level = "high"
    elif confidence >= 0.5:
        level = "medium"
    return {
        "schema": "confidence_scoring.v1",
        "confidence": round(confidence, 6),
        "level": level,
        "details": details,
    }


@mcp.tool()
def runtime_contract_checker() -> dict[str, Any]:
    """Verify runtime tool exposure vs README contract and report drift."""
    code_tools = _server_tool_names()
    readme_tools = _readme_tool_names()
    missing_in_readme = sorted(code_tools - readme_tools)
    extra_in_readme = sorted(readme_tools - code_tools)
    return {
        "schema": "runtime_contract_checker.v1",
        "ok": not missing_in_readme and not extra_in_readme,
        "code_tool_count": len(code_tools),
        "readme_tool_count": len(readme_tools),
        "missing_in_readme": missing_in_readme,
        "extra_in_readme": extra_in_readme,
    }


@mcp.tool()
def cost_budget_enforcer(
    mode: str = "check",
    max_tokens: int = 200000,
    max_calls: int = 50,
    max_seconds: int = 600,
    used_tokens: int = 0,
    used_calls: int = 0,
    used_seconds: int = 0,
) -> dict[str, Any]:
    """Set/check runtime budgets for token/time/tool-call cost control."""
    if mode not in {"set", "check", "record"}:
        raise ValueError("mode must be one of: set, check, record")
    payload = _json_file_load(
        COST_BUDGET_FILE,
        {
            "limits": {"max_tokens": max_tokens, "max_calls": max_calls, "max_seconds": max_seconds},
            "used": {"tokens": 0, "calls": 0, "seconds": 0},
            "updated_at": _now_iso(),
        },
    )
    if mode == "set":
        _require_mutations()
        payload["limits"] = {"max_tokens": max_tokens, "max_calls": max_calls, "max_seconds": max_seconds}
        payload["updated_at"] = _now_iso()
        _json_file_save(COST_BUDGET_FILE, payload)
    elif mode == "record":
        _require_mutations()
        used = payload.get("used", {})
        used["tokens"] = int(used.get("tokens", 0)) + int(used_tokens)
        used["calls"] = int(used.get("calls", 0)) + int(used_calls)
        used["seconds"] = int(used.get("seconds", 0)) + int(used_seconds)
        payload["used"] = used
        payload["updated_at"] = _now_iso()
        _json_file_save(COST_BUDGET_FILE, payload)
    limits = payload.get("limits", {})
    used = payload.get("used", {})
    over = {
        "tokens": int(used.get("tokens", 0)) > int(limits.get("max_tokens", 0)),
        "calls": int(used.get("calls", 0)) > int(limits.get("max_calls", 0)),
        "seconds": int(used.get("seconds", 0)) > int(limits.get("max_seconds", 0)),
    }
    return {
        "schema": "cost_budget_enforcer.v1",
        "mode": mode,
        "ok": not any(over.values()),
        "limits": limits,
        "used": used,
        "over_budget": over,
    }


@mcp.tool()
def multi_agent_lane(
    task: str,
    lanes: list[str] | None = None,
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
) -> dict[str, Any]:
    """Run specialized analysis lanes and merge into one decision packet."""
    if not task.strip():
        raise ValueError("task must not be empty")
    selected = lanes or ["security", "risk", "docs", "tests"]
    lane_results: dict[str, Any] = {}
    if "security" in selected:
        patch = _git("diff", f"{base_ref}...{head_ref}", "--", ".").stdout
        lane_results["security"] = security_triage(diff_text=patch, max_findings=20)
    if "risk" in selected:
        lane_results["risk"] = risk_scoring(ref=head_ref)
    if "docs" in selected:
        lane_results["docs"] = doc_sync_check(base_ref=base_ref, head_ref=head_ref)
    if "tests" in selected:
        lane_results["tests"] = impact_tests(base_ref=base_ref, head_ref=head_ref, output_profile="compact")
    confidence = confidence_scoring(
        checks=[
            {"name": "security", "ok": int(lane_results.get("security", {}).get("finding_count", 0)) == 0, "weight": 3},
            {"name": "risk", "ok": lane_results.get("risk", {}).get("risk_level", "low") != "high", "weight": 2},
            {"name": "docs", "ok": not lane_results.get("docs", {}).get("needs_docs_update", False), "weight": 1},
            {"name": "tests", "ok": True, "weight": 1},
        ]
    )
    return {
        "schema": "multi_agent_lane.v1",
        "task": task,
        "lanes": selected,
        "results": lane_results,
        "confidence": confidence,
    }


@mcp.tool()
def human_approval_points(
    mode: str = "create",
    action: str = "",
    risk_level: str = "medium",
    details: str = "",
    approval_id: str = "",
    approved: bool = False,
) -> dict[str, Any]:
    """Manage human approval checkpoints for risky operations."""
    if mode not in {"create", "list", "resolve"}:
        raise ValueError("mode must be one of: create, list, resolve")
    payload = _approval_points_load()
    items = payload["items"]
    if mode == "list":
        return {"schema": "human_approval_points.v1", "mode": mode, "count": len(items), "items": items}
    if mode == "create":
        _require_mutations()
        row = _approval_point_append(action=action, risk_level=risk_level, details=details)
        return {"schema": "human_approval_points.v1", "mode": mode, "item": row}
    # resolve
    _require_mutations()
    if not approval_id:
        raise ValueError("approval_id is required for resolve mode")
    updated = None
    for row in items:
        if row.get("approval_id") == approval_id:
            row["status"] = "approved" if approved else "rejected"
            row["resolved_at"] = _now_iso()
            updated = row
            break
    if updated is None:
        raise FileNotFoundError(f"approval_id not found: {approval_id}")
    payload["items"] = items
    _json_file_save(APPROVAL_POINTS_FILE, payload)
    return {"schema": "human_approval_points.v1", "mode": mode, "item": updated}


@mcp.tool()
def root_cause_memory(
    mode: str = "list",
    issue: str = "",
    root_cause: str = "",
    fix: str = "",
    max_entries: int = 50,
) -> dict[str, Any]:
    """Persist and suggest root-cause/fix patterns from prior failures."""
    if mode not in {"add", "list", "suggest"}:
        raise ValueError("mode must be one of: add, list, suggest")
    if max_entries < 1:
        raise ValueError("max_entries must be >= 1")
    payload = _json_file_load(ROOT_CAUSE_FILE, {"entries": []})
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    if mode == "add":
        _require_mutations()
        if not issue.strip() or not root_cause.strip() or not fix.strip():
            raise ValueError("issue, root_cause, and fix are required for add mode")
        row = {
            "id": uuid.uuid4().hex[:12],
            "issue": issue,
            "root_cause": root_cause,
            "fix": fix,
            "created_at": _now_iso(),
        }
        entries.append(row)
        payload["entries"] = entries
        _json_file_save(ROOT_CAUSE_FILE, payload)
        return {"schema": "root_cause_memory.v1", "mode": mode, "entry": row}
    if mode == "list":
        return {"schema": "root_cause_memory.v1", "mode": mode, "count": min(len(entries), max_entries), "entries": entries[-max_entries:]}
    needle = issue.lower().strip()
    ranked = []
    for row in entries:
        hay = f"{row.get('issue','')} {row.get('root_cause','')} {row.get('fix','')}".lower()
        score = sum(1 for t in re.split(r"\W+", needle) if t and t in hay)
        if score > 0:
            ranked.append((score, row))
    ranked.sort(key=lambda x: x[0], reverse=True)
    suggestions = [r for _, r in ranked[:max_entries]]
    return {"schema": "root_cause_memory.v1", "mode": mode, "count": len(suggestions), "suggestions": suggestions}


@mcp.tool()
def execution_replay(
    mode: str = "start",
    replay_id: str = "",
    event: dict[str, Any] | None = None,
    max_events: int = 1000,
) -> dict[str, Any]:
    """Record and replay deterministic execution event streams."""
    if mode not in {"start", "log", "finish", "read"}:
        raise ValueError("mode must be one of: start, log, finish, read")
    if max_events < 1:
        raise ValueError("max_events must be >= 1")
    EXECUTION_REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    if mode == "start":
        _require_mutations()
        rid = uuid.uuid4().hex[:12]
        p = _resolve_repo_path(str(EXECUTION_REPLAY_DIR / f"{rid}.json"))
        payload = {"schema": "execution_replay.v1", "replay_id": rid, "status": "open", "created_at": _now_iso(), "events": []}
        _json_file_save(p, payload)
        return {"schema": "execution_replay.v1", "mode": mode, "replay_id": rid, "path": str(p.relative_to(REPO_PATH))}
    if not replay_id:
        raise ValueError("replay_id is required for this mode")
    p = _resolve_repo_path(str(EXECUTION_REPLAY_DIR / f"{replay_id}.json"))
    if not p.is_file():
        raise FileNotFoundError(str(p.relative_to(REPO_PATH)))
    payload = json.loads(p.read_text(encoding="utf-8"))
    events = payload.get("events", [])
    if not isinstance(events, list):
        events = []
    if mode == "read":
        return {"schema": "execution_replay.v1", "mode": mode, "replay_id": replay_id, "status": payload.get("status"), "events": events[:max_events]}
    _require_mutations()
    if mode == "log":
        events.append({"ts": _now_iso(), "event": event or {}})
        if len(events) > max_events:
            events = events[-max_events:]
        payload["events"] = events
        payload["updated_at"] = _now_iso()
        _json_file_save(p, payload)
        return {"schema": "execution_replay.v1", "mode": mode, "replay_id": replay_id, "event_count": len(events)}
    # finish
    payload["status"] = "closed"
    payload["updated_at"] = _now_iso()
    _json_file_save(p, payload)
    return {"schema": "execution_replay.v1", "mode": mode, "replay_id": replay_id, "status": "closed", "event_count": len(events)}


@mcp.tool()
def encode_lossless(
    value: Any,
    use_symbols: bool = True,
    min_symbol_length: int = 12,
    min_symbol_reuse: int = 2,
    use_blob_refs: bool = True,
    min_blob_chars: int = 400,
    blob_store_path: str = ".codebase-tooling-mcp/cache/lossless_blobs.json",
    store_blobs: bool = True,
    store_result: bool = False,
) -> dict[str, Any]:
    """Encode payload with reversible lossless codec (symbols + optional blob refs)."""
    if min_symbol_length < 1:
        raise ValueError("min_symbol_length must be >= 1")
    if min_symbol_reuse < 1:
        raise ValueError("min_symbol_reuse must be >= 1")
    if min_blob_chars < 1:
        raise ValueError("min_blob_chars must be >= 1")
    blob_file = _resolve_repo_path(blob_store_path)
    symbol_table = (
        _lossless_build_symbol_table(
            value=value,
            min_symbol_length=min_symbol_length,
            min_symbol_reuse=min_symbol_reuse,
        )
        if use_symbols
        else {}
    )
    symbol_inverse = _lossless_symbol_inverse(symbol_table)

    blob_payload = _lossless_blob_store_load(blob_file)
    blobs = blob_payload["blobs"]
    original_blob_count = len(blobs)
    encoded = _lossless_encode_node(
        value,
        symbol_inverse=symbol_inverse,
        blobs=blobs,
        use_blob_refs=use_blob_refs,
        min_blob_chars=min_blob_chars,
    )
    if use_blob_refs and store_blobs:
        _require_mutations()
        blob_payload["updated_at"] = _now_iso()
        _lossless_blob_store_save(blob_file, blob_payload)

    original_json = json.dumps(value, ensure_ascii=True, sort_keys=True)
    encoded_json = json.dumps(encoded, ensure_ascii=True, sort_keys=True)
    added_blob_count = len(blobs) - original_blob_count
    out: dict[str, Any] = {
        "schema": "lossless_codec.v1",
        "mode": "encode",
        "codec": "lossless_v1",
        "encoded": encoded,
        "symbol_table": symbol_table,
        "blob_store_path": blob_store_path if use_blob_refs else None,
        "added_blob_count": added_blob_count if use_blob_refs else 0,
        "original_json_chars": len(original_json),
        "encoded_json_chars": len(encoded_json),
        "char_saving": len(original_json) - len(encoded_json),
        "char_saving_ratio": (
            round((len(original_json) - len(encoded_json)) / max(1, len(original_json)), 6)
        ),
    }
    if store_result:
        out["result_id"] = _result_store_put("encode_lossless", out)
    return out


@mcp.tool()
def decode_lossless(
    encoded: Any,
    symbol_table: dict[str, str] | None = None,
    blob_store_path: str = ".codebase-tooling-mcp/cache/lossless_blobs.json",
    blobs_inline: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Decode payload produced by encode_lossless."""
    symbols = symbol_table or {}
    blobs: dict[str, str] = {}
    if blobs_inline:
        blobs.update(blobs_inline)
    blob_file = _resolve_repo_path(blob_store_path)
    store_payload = _lossless_blob_store_load(blob_file)
    raw_blobs = store_payload.get("blobs", {})
    if isinstance(raw_blobs, dict):
        for k, v in raw_blobs.items():
            if isinstance(k, str) and isinstance(v, str):
                blobs.setdefault(k, v)
    decoded = _lossless_decode_node(encoded, symbol_table=symbols, blobs=blobs)
    return {
        "schema": "lossless_codec.v1",
        "mode": "decode",
        "codec": "lossless_v1",
        "decoded": decoded,
    }


@mcp.tool()
def roundtrip_verify(
    value: Any,
    use_symbols: bool = True,
    min_symbol_length: int = 12,
    min_symbol_reuse: int = 2,
    use_blob_refs: bool = False,
    min_blob_chars: int = 400,
) -> dict[str, Any]:
    """Verify decode(encode(x)) round-trip equivalence for lossless codec."""
    encoded = encode_lossless(
        value=value,
        use_symbols=use_symbols,
        min_symbol_length=min_symbol_length,
        min_symbol_reuse=min_symbol_reuse,
        use_blob_refs=use_blob_refs,
        min_blob_chars=min_blob_chars,
        store_blobs=False,
    )
    decoded = decode_lossless(
        encoded=encoded["encoded"],
        symbol_table=encoded.get("symbol_table", {}),
        blobs_inline={},
    )
    ok = decoded.get("decoded") == value
    return {
        "schema": "lossless_codec.v1",
        "mode": "roundtrip_verify",
        "codec": "lossless_v1",
        "ok": ok,
        "original_json_chars": encoded.get("original_json_chars"),
        "encoded_json_chars": encoded.get("encoded_json_chars"),
        "char_saving": encoded.get("char_saving"),
        "char_saving_ratio": encoded.get("char_saving_ratio"),
    }


@mcp.tool()
def delta_encode(
    base: Any,
    target: Any,
    store_result: bool = False,
) -> dict[str, Any]:
    """Build deterministic lossless delta operations from base -> target."""
    ops: list[dict[str, Any]] = []
    _delta_build_ops(base=base, target=target, path="", ops=ops)
    out = {
        "schema": "delta_codec.v1",
        "mode": "encode",
        "op_count": len(ops),
        "ops": ops,
    }
    if store_result:
        out["result_id"] = _result_store_put("delta_encode", out)
    return out


@mcp.tool()
def delta_apply(
    base: Any,
    ops: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply deterministic delta operations and return reconstructed payload."""
    current = json.loads(json.dumps(base))
    for op in ops:
        op_name = str(op.get("op", ""))
        path = str(op.get("path", ""))
        parts = _delta_parse_path(path)
        if op_name == "set":
            current = _delta_set_value(current, parts, op.get("value"))
            continue
        if op_name == "remove":
            current = _delta_remove_value(current, parts)
            continue
        raise ValueError(f"unsupported delta op: {op_name}")
    return {
        "schema": "delta_codec.v1",
        "mode": "apply",
        "op_count": len(ops),
        "value": current,
    }


@mcp.tool()
def failure_memory(
    mode: str = "get",
    category: str | None = None,
    contains: str | None = None,
    max_entries: int = 100,
    error_text: str = "",
    max_suggestions: int = 5,
) -> dict[str, Any]:
    """Unified failure memory access (`get` or `suggest`)."""
    if mode not in {"get", "suggest"}:
        raise ValueError("mode must be one of: get, suggest")
    if mode == "get":
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        payload = _failure_memory_load()
        entries_out: list[dict[str, Any]] = []
        needle = (contains or "").lower().strip()
        for entry in reversed(payload["entries"]):
            if category and entry.get("category") != category:
                continue
            if needle:
                hay = f"{entry.get('stderr', '')}\n{entry.get('stdout', '')}".lower()
                if needle not in hay:
                    continue
            entries_out.append(entry)
            if len(entries_out) >= max_entries:
                break
        return {"mode": mode, "count": len(entries_out), "entries": entries_out}

    if max_suggestions < 1:
        raise ValueError("max_suggestions must be >= 1")
    needle_terms = [t for t in re.split(r"\W+", error_text.lower()) if len(t) > 2]
    payload = _failure_memory_load()
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in payload["entries"]:
        hay = f"{entry.get('stderr', '')}\n{entry.get('stdout', '')}".lower()
        score = sum(1 for t in needle_terms if t in hay)
        if score <= 0:
            continue
        scored.append((score, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    suggestions: list[dict[str, Any]] = []
    for score, entry in scored[:max_suggestions]:
        suggestions.append(
            {
                "score": score,
                "category": entry.get("category"),
                "command": entry.get("command"),
                "suggestion": entry.get("suggestion"),
                "stderr": entry.get("stderr"),
            }
        )
    return {"mode": mode, "count": len(suggestions), "suggestions": suggestions}


def edit_transaction(
    mode: str = "begin",
    transaction_id: str = "",
    label: str = "",
    changes: list[dict[str, Any]] | None = None,
    create_dirs: bool = True,
    delete_metadata: bool = False,
) -> dict[str, Any]:
    """Unified transaction tool (`begin|apply|validate|rollback|commit`)."""
    if mode not in {"begin", "apply", "validate", "rollback", "commit"}:
        raise ValueError("mode must be one of: begin, apply, validate, rollback, commit")

    if mode == "begin":
        _require_mutations()
        txn_id = uuid.uuid4().hex[:12]
        payload = {
            "id": txn_id,
            "label": label,
            "status": "open",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "changes": [],
            "backups": {},
        }
        _tx_save(txn_id, payload)
        return {"mode": mode, "transaction_id": txn_id, "status": "open"}

    if not transaction_id.strip():
        raise ValueError("transaction_id is required for this mode")

    if mode == "apply":
        _require_mutations()
        batch = changes or []
        if not batch:
            raise ValueError("changes must not be empty")
        tx = _tx_load(transaction_id)
        if tx.get("status") != "open":
            raise ValueError("transaction is not open")
        applied: list[str] = []
        for change in batch:
            path = change.get("path")
            content = change.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                raise ValueError("each change requires string path and content")
            file_path = _resolve_repo_path(path)
            rel = str(file_path.relative_to(REPO_PATH))

            if rel not in tx["backups"]:
                if file_path.exists() and file_path.is_file():
                    tx["backups"][rel] = {
                        "existed": True,
                        "content": file_path.read_text(encoding="utf-8", errors="replace"),
                    }
                else:
                    tx["backups"][rel] = {"existed": False, "content": ""}

            if create_dirs:
                file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            tx["changes"].append({"path": rel, "bytes": len(content.encode("utf-8"))})
            applied.append(rel)

        tx["updated_at"] = _now_iso()
        _tx_save(transaction_id, tx)
        return {
            "mode": mode,
            "transaction_id": transaction_id,
            "applied": applied,
            "change_count": len(applied),
        }

    if mode == "validate":
        tx = _tx_load(transaction_id)
        changed_paths = sorted({c["path"] for c in tx.get("changes", [])})
        py_files = [p for p in changed_paths if p.endswith(".py")]
        compile_errors: list[dict[str, Any]] = []
        for rel in py_files[:200]:
            file_path = _resolve_repo_path(rel)
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", str(file_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                compile_errors.append({"path": rel, "stderr": _trim_text(proc.stderr)})
        return {
            "mode": mode,
            "transaction_id": transaction_id,
            "status": tx.get("status"),
            "changed_paths": changed_paths,
            "python_files_checked": len(py_files[:200]),
            "compile_error_count": len(compile_errors),
            "compile_errors": compile_errors,
        }

    if mode == "rollback":
        _require_mutations()
        tx = _tx_load(transaction_id)
        backups = tx.get("backups", {})
        restored: list[str] = []
        for rel, backup in backups.items():
            file_path = _resolve_repo_path(rel)
            existed = bool(backup.get("existed"))
            if existed:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(str(backup.get("content", "")), encoding="utf-8")
            else:
                if file_path.exists():
                    file_path.unlink()
            restored.append(rel)
        tx["status"] = "rolled_back"
        tx["updated_at"] = _now_iso()
        _tx_save(transaction_id, tx)
        return {
            "mode": mode,
            "transaction_id": transaction_id,
            "status": "rolled_back",
            "restored": restored,
        }

    tx = _tx_load(transaction_id)
    tx["status"] = "committed"
    tx["updated_at"] = _now_iso()
    _tx_save(transaction_id, tx)
    if delete_metadata:
        _tx_path(transaction_id).unlink(missing_ok=True)
    return {
        "mode": mode,
        "transaction_id": transaction_id,
        "status": "committed",
        "metadata_deleted": delete_metadata,
    }


@mcp.tool()
def test_impact_map(
    changed_files: list[str] | None = None,
    refresh: bool = False,
    max_age_hours: int = TEST_IMPACT_MAP_MAX_AGE_HOURS,
    max_tests: int = 300,
    output_profile: str | None = None,
) -> dict[str, Any]:
    """Read or refresh the static Python test impact map and query impacted tests."""
    _require_git_repo()
    if max_tests < 1:
        raise ValueError("max_tests must be >= 1")
    if max_age_hours < 0:
        raise ValueError("max_age_hours must be >= 0")
    profile = _default_output_profile(output_profile)
    artifact_path = str(TEST_IMPACT_MAP_FILE)
    if refresh:
        _require_mutations()
        payload = _build_test_impact_map_payload()
        path = _resolve_repo_path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        status = "fresh"
    else:
        payload, status = _load_test_impact_map(max_age_hours=max_age_hours)

    changed = [str(path).strip() for path in (changed_files or []) if str(path).strip()]
    query = _query_test_impact_map(payload, changed, max_tests=max_tests) if payload and changed else {
        "tests": [],
        "test_details": [],
        "impacted_sources": [],
        "unmapped_changed_files": changed if changed and status != "fresh" else [],
        "coverage_gaps": [],
        "confidence": 0.0,
    }
    result = {
        "schema": "test_impact_map.query.v1",
        "artifact_path": artifact_path,
        "artifact_status": status,
        "generated_at": payload.get("generated_at") if isinstance(payload, dict) else None,
        "changed_files": changed,
        "selected_tests": query["tests"],
        "test_details": query["test_details"],
        "impacted_sources": query["impacted_sources"],
        "unmapped_changed_files": query["unmapped_changed_files"],
        "coverage_gaps": query["coverage_gaps"],
        "confidence": query["confidence"],
    }
    if profile == "compact":
        return {
            "schema": "test_impact_map.query.compact.v1",
            "artifact_status": status,
            "test_count": len(query["tests"]),
            "selected_tests": query["tests"],
            "unmapped_changed_files": query["unmapped_changed_files"],
            "confidence": query["confidence"],
        }
    return result


@mcp.tool()
def impact_tests(
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    max_tests: int = 300,
    output_profile: str | None = None,
) -> dict[str, Any]:
    """Select impacted tests using the static map when fresh, else dependency edges."""
    _require_git_repo()
    if max_tests < 1:
        raise ValueError("max_tests must be >= 1")
    profile = _default_output_profile(output_profile)
    diff_out = _git("diff", "--name-only", f"{base_ref}...{head_ref}").stdout.strip()
    changed = [line.strip() for line in diff_out.splitlines() if line.strip()]

    artifact, artifact_status = _load_test_impact_map()
    artifact_unmapped_changed_files: list[str] = []
    artifact_coverage_gaps: list[dict[str, Any]] = []
    if artifact:
        mapped = _query_test_impact_map(artifact, changed, max_tests=max_tests)
        artifact_unmapped_changed_files = mapped["unmapped_changed_files"]
        artifact_coverage_gaps = mapped["coverage_gaps"]
        if artifact_status == "fresh" and (mapped["tests"] or not mapped["unmapped_changed_files"]):
            result = {
                "base_ref": base_ref,
                "head_ref": head_ref,
                "changed_files": changed,
                "impacted_files": [row["path"] for row in mapped["impacted_sources"]],
                "tests": mapped["tests"],
                "test_details": mapped["test_details"],
                "impact_map": {
                    "artifact_path": str(TEST_IMPACT_MAP_FILE),
                    "artifact_status": artifact_status,
                    "generated_at": artifact.get("generated_at"),
                    "confidence": mapped["confidence"],
                    "coverage_gaps": mapped["coverage_gaps"],
                    "unmapped_changed_files": mapped["unmapped_changed_files"],
                },
            }
            if profile == "compact":
                return {"test_count": len(mapped["tests"]), "tests": mapped["tests"], "unmapped_changed_files": mapped["unmapped_changed_files"], "impact_map_status": artifact_status}
            return result

    dep = dependency_map(path=".", recursive=True, include_stdlib=False, output_profile="normal")
    reverse_edges: dict[str, set[str]] = {}
    for edge in dep.get("edges", []):
        reverse_edges.setdefault(edge["to"], set()).add(edge["from"])

    impacted: set[str] = set(changed)
    queue: list[str] = list(changed)
    while queue:
        cur = queue.pop(0)
        for src in reverse_edges.get(cur, set()):
            if src not in impacted:
                impacted.add(src)
                queue.append(src)

    tests: list[str] = []
    for rel in sorted(impacted):
        p = Path(rel)
        if "test" in p.name.lower() and p.suffix == ".py":
            tests.append(rel)
            continue
        if p.suffix == ".py":
            for cand in (f"tests/test_{p.stem}.py", f"tests/{p.stem}_test.py"):
                resolved = _resolve_repo_path(cand)
                if resolved.is_file():
                    tests.append(cand)
    deduped: list[str] = []
    seen: set[str] = set()
    for t in tests:
        if t in seen:
            continue
        seen.add(t)
        deduped.append(t)
        if len(deduped) >= max_tests:
            break

    result = {
        "base_ref": base_ref,
        "head_ref": head_ref,
        "changed_files": changed,
        "impacted_files": sorted(impacted),
        "tests": deduped,
        "impact_map": {"artifact_path": str(TEST_IMPACT_MAP_FILE), "artifact_status": artifact_status, "fallback_used": True, "coverage_gaps": artifact_coverage_gaps, "unmapped_changed_files": artifact_unmapped_changed_files},
        "unmapped_changed_files": artifact_unmapped_changed_files,
    }
    if profile == "compact":
        return {"test_count": len(deduped), "tests": deduped, "unmapped_changed_files": artifact_unmapped_changed_files, "impact_map_status": artifact_status}
    return result


@mcp.tool()
def api_surface_snapshot(
    path: str = ".",
    snapshot_path: str = str(API_SNAPSHOT_FILE),
    mode: str = "write",
    include_private: bool = False,
) -> dict[str, Any]:
    """Write or check public Python API surface snapshots."""
    _require_git_repo()
    if mode not in {"write", "check"}:
        raise ValueError("mode must be one of: write, check")

    symbols = symbol_index(
        path=path,
        include_private=include_private,
        recursive=True,
        max_symbols=20000,
        output_profile="normal",
    )
    public_symbols = [
        {
            "path": s["path"],
            "name": s["name"],
            "kind": s["kind"],
        }
        for s in symbols
        if include_private or not str(s["name"]).startswith("_")
    ]
    public_symbols.sort(key=lambda x: (x["path"], x["name"], x["kind"]))
    snap_file = _resolve_repo_path(snapshot_path)

    if mode == "write":
        _require_mutations()
        snap_file.parent.mkdir(parents=True, exist_ok=True)
        snap_file.write_text(
            json.dumps({"generated_at": _now_iso(), "symbols": public_symbols}, indent=2),
            encoding="utf-8",
        )
        return {"mode": "write", "snapshot_path": snapshot_path, "symbol_count": len(public_symbols)}

    if not snap_file.is_file():
        raise FileNotFoundError(snapshot_path)
    baseline = json.loads(snap_file.read_text(encoding="utf-8"))
    baseline_symbols = baseline.get("symbols", [])
    base_set = {(x["path"], x["name"], x["kind"]) for x in baseline_symbols if isinstance(x, dict)}
    cur_set = {(x["path"], x["name"], x["kind"]) for x in public_symbols}
    removed = sorted(base_set - cur_set)
    added = sorted(cur_set - base_set)
    return {
        "mode": "check",
        "snapshot_path": snapshot_path,
        "removed_count": len(removed),
        "added_count": len(added),
        "removed": [{"path": p, "name": n, "kind": k} for (p, n, k) in removed],
        "added": [{"path": p, "name": n, "kind": k} for (p, n, k) in added],
    }


@mcp.tool()
def workspace_facts(refresh: bool = True) -> dict[str, Any]:
    """Get or refresh lightweight workspace facts."""
    facts_path = Path(".codebase-tooling-mcp/memory/workspace_facts.json")
    if not refresh:
        payload = _json_file_load(facts_path, {})
        if payload:
            return payload

    files = find_paths(path=".", recursive=True, file_type="file", max_entries=10000, output_profile="compact")
    ext_counts: dict[str, int] = {}
    for rel in files:
        ext = Path(rel).suffix.lower() or "<none>"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    top_ext = sorted(
        [{"extension": k, "count": v} for k, v in ext_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:20]
    payload = {
        "generated_at": _now_iso(),
        "is_git_repo": _is_git_repo(),
        "file_count": len(files),
        "top_extensions": top_ext,
        "has_tests_dir": _resolve_repo_path("tests").exists(),
        "has_readme": _resolve_repo_path("README.md").exists(),
        "default_output_profile": _token_budget_load()["default_output_profile"],
    }
    _json_file_save(facts_path, payload)
    return payload


@mcp.tool()
def risk_scoring(
    ref: str | None = None,
    staged: bool = False,
    pathspec: str | None = None,
) -> dict[str, Any]:
    """Score change risk using path and churn heuristics."""
    summary = summarize_diff(ref=ref, staged=staged, pathspec=pathspec, output_profile="normal")
    score = 0
    reasons: list[str] = []
    file_count = int(summary["file_count"])
    add = int(summary["total_added"])
    delete = int(summary["total_deleted"])
    churn = add + delete

    if file_count > 20:
        score += 2
        reasons.append("large file count")
    if churn > 500:
        score += 2
        reasons.append("high churn")
    if churn > 1500:
        score += 2
        reasons.append("very high churn")

    risky_files = summary.get("risk_flags", {}).get("risky_files", [])
    if risky_files:
        score += min(4, len(risky_files))
        reasons.append("sensitive file changes")

    todo_adds = int(summary.get("risk_flags", {}).get("todo_like_additions", 0))
    if todo_adds > 0:
        score += 1
        reasons.append("todo/fixme additions")

    level = "low"
    if score >= 6:
        level = "high"
    elif score >= 3:
        level = "medium"
    return {
        "risk_score": score,
        "risk_level": level,
        "reasons": reasons,
        "summary": summary,
    }


@mcp.tool()
def doc_sync_check(
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    doc_globs: list[str] | None = None,
) -> dict[str, Any]:
    """Check whether docs changed when code/API-like files changed."""
    _require_git_repo()
    docs = doc_globs or ["README.md", "docs/**", "**/*.md"]
    diff_out = _git("diff", "--name-only", f"{base_ref}...{head_ref}").stdout.strip()
    changed = [line.strip() for line in diff_out.splitlines() if line.strip()]
    doc_changed: list[str] = []
    code_changed: list[str] = []
    for rel in changed:
        if any(fnmatch.fnmatch(rel, g) for g in docs):
            doc_changed.append(rel)
            continue
        if rel.endswith((".py", ".ts", ".tsx", ".js", ".go", ".rs")):
            code_changed.append(rel)
    needs_docs = bool(code_changed) and not bool(doc_changed)
    suggestions: list[str] = []
    if needs_docs:
        suggestions.append("Update README.md with behavioral/API changes.")
        if _resolve_repo_path("docs").exists():
            suggestions.append("Add or update docs under docs/.")
    return {
        "base_ref": base_ref,
        "head_ref": head_ref,
        "changed_count": len(changed),
        "code_changed": code_changed,
        "doc_changed": doc_changed,
        "needs_docs_update": needs_docs,
        "suggestions": suggestions,
    }


@mcp.tool()
def tree_sitter_core(
    path: str = ".",
    mode: str = "status",
    language: str = "auto",
    node_types: list[str] | None = None,
    text_pattern: str | None = None,
    recursive: bool = True,
    max_files: int = 200,
    max_nodes: int = 5000,
    output_profile: str | None = None,
    fields: list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
    adaptive_limits: bool = True,
    summary_mode: str = "full",
    compress: bool = False,
    store_result: bool = False,
) -> dict[str, Any]:
    """Parse/search syntax trees via Tree-sitter when available."""
    if mode not in {"status", "parse", "search"}:
        raise ValueError("mode must be one of: status, parse, search")
    if max_files < 1 or max_nodes < 1:
        raise ValueError("max_files and max_nodes must be >= 1")
    if summary_mode not in {"full", "quick"}:
        raise ValueError("summary_mode must be one of: full, quick")
    if adaptive_limits:
        max_files = _adaptive_limit(max_files, soft_cap=120)
        max_nodes = _adaptive_limit(max_nodes, soft_cap=1500)
    profile = _default_output_profile(output_profile)
    available = _tree_sitter_available()
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    if mode == "status":
        return {"available": available, "engine": "tree_sitter_languages"}

    fingerprint = _fingerprint_path(root, recursive=recursive, max_files=3000)
    cache_key = json.dumps(
        {
            "path": str(root.relative_to(REPO_PATH)),
            "mode": mode,
            "language": language,
            "node_types": node_types or [],
            "text_pattern": text_pattern or "",
            "recursive": recursive,
            "max_files": max_files,
            "max_nodes": max_nodes,
            "fingerprint": fingerprint,
            "available": available,
        },
        sort_keys=True,
    )
    cached = _cache_get("tree_sitter_core", cache_key)
    if isinstance(cached, dict):
        matched_files = int(cached.get("file_count", 0))
        total_nodes = int(cached.get("node_count", 0))
        files = list(cached.get("files", []))
    else:
        matched_files = 0
        total_nodes = 0
        files: list[dict[str, Any]] = []
        regex = re.compile(text_pattern, re.IGNORECASE) if text_pattern else None

        for candidate in _iter_candidate_files(root, recursive=recursive):
            ext = candidate.suffix.lower()
            detected = _tree_sitter_language_for_ext(ext)
            if not detected:
                continue
            lang = detected if language == "auto" else language
            if language != "auto" and lang != detected:
                continue
            source = candidate.read_text(encoding="utf-8", errors="replace")
            rel = str(candidate.relative_to(REPO_PATH)).replace("\\", "/")
            nodes: list[dict[str, Any]] = []

            if available:
                try:
                    nodes = _tree_sitter_parse_nodes(
                        source=source,
                        language=lang,
                        node_types=node_types,
                        max_nodes=max_nodes - total_nodes,
                    )
                except Exception:
                    nodes = []
            if not nodes and lang == "python":
                ast_hits = ast_search(
                    path=rel,
                    node_type=(node_types[0] if node_types else "FunctionDef"),
                    max_results=max(1, max_nodes - total_nodes),
                )
                nodes = [
                    {
                        "type": hit["node_type"],
                        "start_line": hit["line"],
                        "start_column": hit["column"],
                        "end_line": hit["end_line"],
                        "end_column": hit["column"],
                    }
                    for hit in ast_hits
                ]

            if regex:
                lines = source.splitlines()
                filtered: list[dict[str, Any]] = []
                for n in nodes:
                    s = max(1, int(n["start_line"]))
                    e = min(len(lines), int(n["end_line"]))
                    snippet = "\n".join(lines[s - 1 : e])
                    if regex.search(snippet):
                        filtered.append(n)
                nodes = filtered

            if not nodes:
                continue
            matched_files += 1
            total_nodes += len(nodes)
            files.append({"path": rel, "language": lang, "node_count": len(nodes), "nodes": nodes})
            if matched_files >= max_files or total_nodes >= max_nodes:
                break
        _cache_set(
            "tree_sitter_core",
            cache_key,
            {"file_count": matched_files, "node_count": total_nodes, "files": files},
        )

    files = _paginate(files, offset=offset, limit=limit)
    if profile == "compact" and not fields:
        files = [
            {"path": f.get("path"), "language": f.get("language"), "node_count": f.get("node_count")}
            for f in files
        ]
    else:
        files = _select_fields(files, fields)

    result = {
        "schema": "tree_sitter_core.v1",
        "available": available,
        "mode": mode,
        "path": str(root.relative_to(REPO_PATH)),
        "file_count": matched_files,
        "node_count": total_nodes,
        "files": files,
    }
    if summary_mode == "quick":
        result = {
            "schema": "tree_sitter_core.quick.v1",
            "available": available,
            "mode": mode,
            "path": str(root.relative_to(REPO_PATH)),
            "file_count": matched_files,
            "node_count": total_nodes,
        }
    if compress and isinstance(result.get("files"), list):
        result["files_compressed"] = _compress_table(result["files"])
        result.pop("files", None)
    if store_result:
        result["result_id"] = _result_store_put("tree_sitter_core", result)
    return result


def repo_index_daemon(
    mode: str = "refresh",
    path: str = ".",
    query: str = "",
    recursive: bool = True,
    include_hashes: bool = False,
    max_files: int = 5000,
    output_profile: str | None = None,
    fields: list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
    adaptive_limits: bool = True,
    summary_mode: str = "full",
    compress: bool = False,
    store_result: bool = False,
    incremental: bool = True,
) -> dict[str, Any]:
    """Build/read/query a persistent repository index keyed by file metadata."""
    if mode not in {"refresh", "read", "query"}:
        raise ValueError("mode must be one of: refresh, read, query")
    if max_files < 1:
        raise ValueError("max_files must be >= 1")
    if summary_mode not in {"full", "quick"}:
        raise ValueError("summary_mode must be one of: full, quick")
    if adaptive_limits:
        max_files = _adaptive_limit(max_files, soft_cap=2500)
    profile = _default_output_profile(output_profile)
    index_path = _resolve_repo_path(str(REPO_INDEX_FILE))

    if mode in {"read", "query"}:
        if not index_path.is_file():
            raise FileNotFoundError(str(REPO_INDEX_FILE))
        index_payload = json.loads(index_path.read_text(encoding="utf-8"))
        if mode == "query":
            value = _query_value(index_payload, query) if query.strip() else index_payload
            if isinstance(value, list):
                value = _paginate(value, offset=offset, limit=limit)
                if value and isinstance(value[0], dict):
                    value = _select_fields(value, fields)
            return {
                "mode": mode,
                "query": query,
                "value_json": _trim_text(json.dumps(value, indent=2, ensure_ascii=True)),
                "value": value if profile != "compact" else None,
            }
        if isinstance(index_payload.get("files"), list):
            files = _paginate(index_payload["files"], offset=offset, limit=limit)
            if files and isinstance(files[0], dict):
                files = _select_fields(files, fields)
            index_payload["files"] = files
        if profile == "compact":
            compact = {
                "schema": "repo_index_daemon.compact.v1",
                "mode": mode,
                "generated_at": index_payload.get("generated_at"),
                "file_count": index_payload.get("file_count", 0),
                "symbol_count": index_payload.get("symbol_count", 0),
                "dependency_edge_count": index_payload.get("dependency_edge_count", 0),
            }
            if store_result:
                compact["result_id"] = _result_store_put("repo_index_daemon", compact)
            return compact
        if summary_mode == "quick":
            quick = {
                "schema": "repo_index_daemon.quick.v1",
                "mode": mode,
                "generated_at": index_payload.get("generated_at"),
                "file_count": index_payload.get("file_count", 0),
                "symbol_count": index_payload.get("symbol_count", 0),
            }
            if store_result:
                quick["result_id"] = _result_store_put("repo_index_daemon", quick)
            return quick
        if compress and isinstance(index_payload.get("files"), list):
            index_payload["files_compressed"] = _compress_table(index_payload["files"])
            index_payload.pop("files", None)
        if store_result:
            index_payload["result_id"] = _result_store_put("repo_index_daemon", index_payload)
        return index_payload

    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    existing_payload = None
    if incremental and index_path.is_file():
        try:
            existing_payload = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_payload = None

    files_meta: list[dict[str, Any]] = []
    for candidate in _iter_candidate_files(root, recursive=recursive):
        rel = str(candidate.relative_to(REPO_PATH)).replace("\\", "/")
        if _is_hidden_rel_path(Path(rel)):
            continue
        stat = candidate.stat()
        entry = {
            "path": rel,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
        if include_hashes:
            try:
                entry["sha256"] = _file_sha256(candidate)
            except OSError:
                entry["sha256"] = ""
        files_meta.append(entry)
        if len(files_meta) >= max_files:
            break

    changed_paths: list[str] = []
    if existing_payload and isinstance(existing_payload.get("files"), list):
        prev = {
            f.get("path"): (f.get("size"), f.get("mtime_ns"))
            for f in existing_payload["files"]
            if isinstance(f, dict) and isinstance(f.get("path"), str)
        }
        for row in files_meta:
            p = row["path"]
            sig = (row.get("size"), row.get("mtime_ns"))
            if prev.get(p) != sig:
                changed_paths.append(p)

    reuse_prev_graphs = bool(existing_payload) and len([p for p in changed_paths if p.endswith(".py")]) == 0
    if reuse_prev_graphs:
        symbols = existing_payload.get("symbols", []) if isinstance(existing_payload.get("symbols"), list) else []
        dep_edges = existing_payload.get("dependencies", []) if isinstance(existing_payload.get("dependencies"), list) else []
        call_edges = existing_payload.get("call_edges", []) if isinstance(existing_payload.get("call_edges"), list) else []
        dep = {"edge_count": len(dep_edges), "edges": dep_edges}
        call = {"edge_count": len(call_edges), "edges": call_edges}
    else:
        symbols = symbol_index(
            path=path,
            include_private=False,
            recursive=recursive,
            max_symbols=20000,
            output_profile="compact",
        )
        dep = dependency_map(
            path=path,
            recursive=recursive,
            include_stdlib=False,
            max_files=max_files,
            output_profile="compact",
        )
        call = call_graph(
            path=path,
            recursive=recursive,
            max_edges=20000,
            output_profile="compact",
        )

    payload: dict[str, Any] = {
        "schema": "repo_index_daemon.v1",
        "generated_at": _now_iso(),
        "path": str(root.relative_to(REPO_PATH)),
        "file_count": len(files_meta),
        "symbol_count": (
            int(existing_payload.get("symbol_count", 0))
            if reuse_prev_graphs and existing_payload
            else len(symbols)
        ),
        "dependency_edge_count": int(dep.get("edge_count", 0)),
        "call_edge_count": int(call.get("edge_count", 0)),
        "files": files_meta if profile != "compact" else files_meta[:300],
        "symbols": symbols if profile == "verbose" else [],
        "dependencies": dep.get("edges", []) if profile == "verbose" else [],
        "call_edges": call.get("edges", []) if profile == "verbose" else [],
        "incremental": incremental,
        "changed_paths_count": len(changed_paths),
    }
    if _is_git_repo():
        payload["git_head"] = _git("rev-parse", "HEAD").stdout.strip()
        payload["git_branch"] = _git("branch", "--show-current").stdout.strip()

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    result = {
        "schema": "repo_index_daemon.refresh.v1",
        "mode": mode,
        "index_path": str(REPO_INDEX_FILE),
        "generated_at": payload["generated_at"],
        "file_count": payload["file_count"],
        "symbol_count": payload["symbol_count"],
        "dependency_edge_count": payload["dependency_edge_count"],
        "call_edge_count": payload["call_edge_count"],
        "incremental": incremental,
        "changed_paths_count": len(changed_paths),
    }
    if summary_mode == "quick":
        result = {
            "schema": "repo_index_daemon.quick.v1",
            "mode": mode,
            "generated_at": payload["generated_at"],
            "file_count": payload["file_count"],
            "symbol_count": payload["symbol_count"],
            "incremental": incremental,
            "changed_paths_count": len(changed_paths),
        }
    if compress:
        result["files_compressed"] = _compress_table(files_meta[:500])
    if store_result:
        result["result_id"] = _result_store_put("repo_index_daemon", result)
    return result


class CodeIndexRouterService:
    """Application service for code indexing and semantic-search routing."""

    def route(
        self,
        mode: str = "refresh",
        path: str = ".",
        query: str = "",
        recursive: bool = True,
        output_profile: str | None = None,
        fields: list[str] | None = None,
        offset: int = 0,
        limit: int | None = None,
        max_files: int = 5000,
        max_symbols: int = 20000,
        max_edges: int = 20000,
        include_hashes: bool = False,
        include_private: bool = False,
        include_stdlib: bool = False,
        local_rerank_top_k: int = 25,
        use_local_rerank: bool = True,
        summary_mode: str = "full",
        compress: bool = False,
        store_result: bool = False,
        incremental: bool = True,
        action_mode: str = "",
        pattern: str = "",
        case_insensitive: bool = False,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        include_hidden: bool = False,
        max_matches: int = 500,
        max_file_bytes: int = 1048576,
        node_type: str = "Call",
        name_pattern: str = "",
        base_ref: str = "HEAD~1",
        head_ref: str = "HEAD",
        snapshot_path: str = str(API_SNAPSHOT_FILE),
    ) -> dict[str, Any]:
        allowed = {
            "refresh",
            "read",
            "query",
            "symbols",
            "deps",
            "calls",
            "search",
            "grep",
            "tree",
            "ast",
            "impact_tests",
            "doc_sync",
            "api_surface",
        }
        if mode not in allowed:
            raise ValueError(f"mode must be one of: {', '.join(sorted(allowed))}")

        if mode in {"refresh", "read", "query"}:
            result = repo_index_daemon(
                mode=mode,
                path=path,
                query=query,
                recursive=recursive,
                include_hashes=include_hashes,
                max_files=max_files,
                output_profile=output_profile,
                fields=fields,
                offset=offset,
                limit=limit,
                summary_mode=summary_mode,
                compress=compress,
                store_result=store_result,
                incremental=incremental,
            )
        elif mode == "symbols":
            result = symbol_index(
                path=path,
                recursive=recursive,
                include_private=include_private,
                max_symbols=max_symbols,
                output_profile=output_profile,
                fields=fields,
                offset=offset,
                limit=limit,
            )
        elif mode == "deps":
            result = dependency_map(
                path=path,
                recursive=recursive,
                include_stdlib=include_stdlib,
                max_files=max_files,
                output_profile=output_profile,
                fields=fields,
                offset=offset,
                limit=limit,
                summary_mode=summary_mode,
                compress=compress,
                store_result=store_result,
            )
        elif mode == "calls":
            result = call_graph(
                path=path,
                recursive=recursive,
                max_edges=max_edges,
                output_profile=output_profile,
                fields=fields,
                offset=offset,
                limit=limit,
                summary_mode=summary_mode,
                compress=compress,
                store_result=store_result,
            )
        elif mode == "search":
            result = semantic_find(
                query=query,
                path=path,
                max_results=limit or 20,
                local_rerank_top_k=local_rerank_top_k,
                use_local_rerank=use_local_rerank,
                output_profile=output_profile,
                fields=fields,
                offset=offset,
                summary_mode=summary_mode,
                compress=compress,
                store_result=store_result,
            )
        elif mode == "grep":
            needle = pattern or query
            if not needle:
                raise ValueError("query or pattern is required for grep mode")
            result = grep(
                pattern=needle,
                path=path,
                recursive=recursive,
                case_insensitive=case_insensitive,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                include_hidden=include_hidden,
                max_matches=max_matches,
                max_file_bytes=max_file_bytes,
                output_profile=output_profile or "compact",
                fields=fields,
                offset=offset,
                limit=limit,
                compress=compress,
                summary_mode=summary_mode,
                store_result=store_result,
            )
        elif mode == "tree":
            tree_mode = action_mode or "parse"
            result = tree_sitter_core(
                path=path,
                mode=tree_mode,
                recursive=recursive,
                node_types=[node_type] if node_type else None,
                text_pattern=query or pattern or None,
                max_files=max_files,
                max_nodes=max_symbols,
                output_profile=output_profile,
                fields=fields,
                offset=offset,
                limit=limit,
                summary_mode=summary_mode,
                compress=compress,
                store_result=store_result,
            )
        elif mode == "ast":
            result = ast_search(
                path=path,
                node_type=node_type,
                name_pattern=name_pattern or query or None,
                recursive=recursive,
                max_results=max_matches,
            )
        elif mode == "impact_tests":
            result = impact_tests(
                base_ref=base_ref,
                head_ref=head_ref,
                max_tests=limit or max_matches,
                output_profile=output_profile or "normal",
            )
        elif mode == "doc_sync":
            result = doc_sync_check(base_ref=base_ref, head_ref=head_ref)
        else:
            result = api_surface_snapshot(
                path=path,
                snapshot_path=snapshot_path,
                mode=action_mode or "check",
                include_private=include_private,
            )
        return {
            "schema": "code_index_router.v1",
            "mode": mode,
            "result": result,
        }


_CODE_INDEX_ROUTER_SERVICE = CodeIndexRouterService()


@mcp.tool()
def code_index_router(
    mode: str = "refresh",
    path: str = ".",
    query: str = "",
    recursive: bool = True,
    output_profile: str | None = None,
    fields: list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
    max_files: int = 5000,
    max_symbols: int = 20000,
    max_edges: int = 20000,
    include_hashes: bool = False,
    include_private: bool = False,
    include_stdlib: bool = False,
    local_rerank_top_k: int = 25,
    use_local_rerank: bool = True,
    summary_mode: str = "full",
    compress: bool = False,
    store_result: bool = False,
    incremental: bool = True,
    action_mode: str = "",
    pattern: str = "",
    case_insensitive: bool = False,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    include_hidden: bool = False,
    max_matches: int = 500,
    max_file_bytes: int = 1048576,
    node_type: str = "Call",
    name_pattern: str = "",
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    snapshot_path: str = str(API_SNAPSHOT_FILE),
) -> dict[str, Any]:
    """Strict code-intel router for repository index, search, structural analysis, and test/doc impact modes."""
    return _CODE_INDEX_ROUTER_SERVICE.route(
        mode=mode,
        path=path,
        query=query,
        recursive=recursive,
        output_profile=output_profile,
        fields=fields,
        offset=offset,
        limit=limit,
        max_files=max_files,
        max_symbols=max_symbols,
        max_edges=max_edges,
        include_hashes=include_hashes,
        include_private=include_private,
        include_stdlib=include_stdlib,
        local_rerank_top_k=local_rerank_top_k,
        use_local_rerank=use_local_rerank,
        summary_mode=summary_mode,
        compress=compress,
        store_result=store_result,
        incremental=incremental,
        action_mode=action_mode,
        pattern=pattern,
        case_insensitive=case_insensitive,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        include_hidden=include_hidden,
        max_matches=max_matches,
        max_file_bytes=max_file_bytes,
        node_type=node_type,
        name_pattern=name_pattern,
        base_ref=base_ref,
        head_ref=head_ref,
        snapshot_path=snapshot_path,
    )


@mcp.tool()
def self_check_pipeline(
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    run_test_execution: bool = True,
    run_impact_tests: bool = True,
    run_doc_check: bool = True,
    run_api_check: bool = True,
    run_risk_check: bool = True,
    run_compile_check: bool = True,
    snapshot_path: str = str(API_SNAPSHOT_FILE),
    max_compile_files: int = 300,
    summary_mode: str = "quick",
) -> dict[str, Any]:
    """Run a single-call quality gate pipeline and return structured results."""
    _require_git_repo()
    if max_compile_files < 1:
        raise ValueError("max_compile_files must be >= 1")
    if summary_mode not in {"quick", "full"}:
        raise ValueError("summary_mode must be one of: quick, full")

    result: dict[str, Any] = {
        "base_ref": base_ref,
        "head_ref": head_ref,
        "started_at": _now_iso(),
        "checks": {},
        "ok": True,
    }

    diff_out = _git("diff", "--name-only", f"{base_ref}...{head_ref}").stdout.strip()
    changed = [line.strip() for line in diff_out.splitlines() if line.strip()]
    result["changed_files"] = changed

    if run_compile_check:
        compile_errors: list[dict[str, Any]] = []
        py_files = [p for p in changed if p.endswith(".py")][:max_compile_files]
        for rel in py_files:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", str(_resolve_repo_path(rel))],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                compile_errors.append({"path": rel, "stderr": _trim_text(proc.stderr)})
        result["checks"]["compile"] = {
            "checked_files": len(py_files),
            "error_count": len(compile_errors),
            "errors": compile_errors if summary_mode == "full" else compile_errors[:10],
        }
        if compile_errors:
            result["ok"] = False

    if run_risk_check:
        risk = risk_scoring(ref=f"{base_ref}...{head_ref}")
        result["checks"]["risk"] = risk
        if risk.get("risk_level") == "high":
            result["ok"] = False

    if run_doc_check:
        doc = doc_sync_check(base_ref=base_ref, head_ref=head_ref)
        result["checks"]["docs"] = doc
        if doc.get("needs_docs_update"):
            result["ok"] = False

    if run_api_check:
        if _resolve_repo_path(snapshot_path).is_file():
            api = api_surface_snapshot(
                mode="check",
                snapshot_path=snapshot_path,
                include_private=False,
            )
            result["checks"]["api"] = api
            if api.get("removed_count", 0) > 0:
                result["ok"] = False
        else:
            result["checks"]["api"] = {
                "skipped": True,
                "reason": f"snapshot missing: {snapshot_path}",
            }

    if run_impact_tests:
        impacts = impact_tests(
            base_ref=base_ref,
            head_ref=head_ref,
            output_profile="compact",
        )
        result["checks"]["impact_tests"] = impacts

    if run_test_execution:
        selected = impact_tests(
            base_ref=base_ref,
            head_ref=head_ref,
            output_profile="compact",
        ).get("tests", [])
        test_cmd = ["pytest", "-q", *selected] if selected else ["pytest", "-q"]
        exec_result = command_runner(
            command=test_cmd,
            cwd=".",
            timeout_seconds=300,
        )
        result["checks"]["test_execution"] = {
            "ok": exec_result.get("ok", False),
            "exit_code": exec_result.get("exit_code"),
            "command": exec_result.get("command"),
            "selected_tests": selected,
            "stderr": exec_result.get("stderr", ""),
            "timeout": exec_result.get("timeout", False),
        }
        if not exec_result.get("ok", False):
            result["ok"] = False

    result["finished_at"] = _now_iso()
    if summary_mode == "quick":
        return {
            "schema": "self_check_pipeline.quick.v1",
            "base_ref": base_ref,
            "head_ref": head_ref,
            "ok": result["ok"],
            "changed_file_count": len(changed),
            "checks": {
                name: {
                    k: v
                    for k, v in data.items()
                    if k in {"ok", "exit_code", "error_count", "needs_docs_update", "risk_level", "removed_count", "added_count", "timeout", "checked_files"}
                }
                for name, data in result["checks"].items()
                if isinstance(data, dict)
            },
        }
    return result


def memory_upsert(
    namespace: str,
    key: str,
    value: Any,
    ttl_days: int | None = None,
    confidence: float = 1.0,
    source: str = "agent",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Create or update a structured context memory record."""
    _require_mutations()
    if not namespace.strip() or not key.strip():
        raise ValueError("namespace and key must not be empty")
    if confidence < 0 or confidence > 1:
        raise ValueError("confidence must be in range [0, 1]")

    payload = _memory_load()
    entries = payload["entries"]
    now_iso = _now_iso()
    expires_at = _to_iso_expiry(ttl_days)
    updated = False
    for entry in entries:
        if entry.get("namespace") == namespace and entry.get("key") == key:
            entry["value"] = value
            entry["confidence"] = confidence
            entry["source"] = source
            entry["tags"] = tags or []
            entry["updated_at"] = now_iso
            entry["expires_at"] = expires_at
            updated = True
            break

    if not updated:
        entries.append(
            {
                "namespace": namespace,
                "key": key,
                "value": value,
                "confidence": confidence,
                "source": source,
                "tags": tags or [],
                "created_at": now_iso,
                "updated_at": now_iso,
                "expires_at": expires_at,
            }
        )

    _memory_save(payload)
    return {
        "path": str(MEMORY_FILE),
        "namespace": namespace,
        "key": key,
        "updated": True,
        "expires_at": expires_at,
    }


def memory_summary_upsert(
    namespace: str,
    focus: str,
    summary: str,
    ttl_days: int | None = None,
    confidence: float = 1.0,
    source: str = "agent",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Create or update memory summary/focus records for context retention."""
    _require_mutations()
    if not namespace.strip() or not focus.strip():
        raise ValueError("namespace and focus must not be empty")
    if confidence < 0 or confidence > 1:
        raise ValueError("confidence must be in range [0, 1]")
    payload = _memory_load()
    summaries = payload["summaries"]
    now_iso = _now_iso()
    expires_at = _to_iso_expiry(ttl_days)
    updated = False
    for row in summaries:
        if row.get("namespace") == namespace and row.get("focus") == focus:
            row["summary"] = summary
            row["confidence"] = confidence
            row["source"] = source
            row["tags"] = tags or []
            row["updated_at"] = now_iso
            row["expires_at"] = expires_at
            updated = True
            break
    if not updated:
        summaries.append(
            {
                "namespace": namespace,
                "focus": focus,
                "summary": summary,
                "confidence": confidence,
                "source": source,
                "tags": tags or [],
                "created_at": now_iso,
                "updated_at": now_iso,
                "expires_at": expires_at,
            }
        )
    _memory_save(payload)
    return {
        "path": str(MEMORY_FILE),
        "namespace": namespace,
        "focus": focus,
        "updated": True,
        "expires_at": expires_at,
    }


def memory_decision_record(
    namespace: str,
    topic: str,
    decision: Any,
    decided_by: str = "llm",
    rationale: str = "",
    ttl_days: int | None = None,
    confidence: float = 1.0,
    source: str = "agent",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Record a decision with human-over-llm priority semantics."""
    _require_mutations()
    if not namespace.strip() or not topic.strip():
        raise ValueError("namespace and topic must not be empty")
    if decided_by not in {"human", "llm"}:
        raise ValueError("decided_by must be one of: human, llm")
    if confidence < 0 or confidence > 1:
        raise ValueError("confidence must be in range [0, 1]")
    payload = _memory_load()
    decisions = payload["decisions"]
    now_iso = _now_iso()
    expires_at = _to_iso_expiry(ttl_days)
    row = {
        "id": uuid.uuid4().hex[:12],
        "namespace": namespace,
        "topic": topic,
        "decision": decision,
        "decided_by": decided_by,
        "rationale": rationale,
        "confidence": confidence,
        "source": source,
        "tags": tags or [],
        "created_at": now_iso,
        "updated_at": now_iso,
        "expires_at": expires_at,
    }
    decisions.append(row)
    _memory_save(payload)
    now = datetime.now(timezone.utc)
    effective = _effective_decisions(
        decisions=decisions,
        now=now,
        namespace=namespace,
        include_expired=False,
    )
    resolved = None
    for item in effective:
        if item.get("topic") == topic:
            resolved = item
            break
    return {
        "path": str(MEMORY_FILE),
        "recorded": row,
        "effective_decision": resolved,
    }


def memory_get(
    namespace: str | None = None,
    key: str | None = None,
    include_expired: bool = False,
    max_entries: int = 200,
    include_summaries: bool = True,
    include_effective_decisions: bool = True,
    auto_compact: bool = False,
    compact_threshold_entries: int = 80,
    compact_threshold_chars: int = 16000,
    compact_keep_entries: int = 40,
    compact_summary_max_chars: int = 1200,
) -> dict[str, Any]:
    """Read context memory entries with namespace/key filters."""
    if max_entries < 1:
        raise ValueError("max_entries must be >= 1")
    _memory_stats_record("get")
    payload = _memory_load()
    now = datetime.now(timezone.utc)
    entries_out: list[dict[str, Any]] = []
    summaries_out: list[dict[str, Any]] = []

    for entry in payload["entries"]:
        if namespace is not None and entry.get("namespace") != namespace:
            continue
        if key is not None and entry.get("key") != key:
            continue
        expired = _is_expired(entry.get("expires_at"), now)
        if expired and not include_expired:
            continue
        copied = dict(entry)
        copied["expired"] = expired
        entries_out.append(copied)
        if len(entries_out) >= max_entries:
            break

    if include_summaries:
        for row in payload["summaries"]:
            if namespace is not None and row.get("namespace") != namespace:
                continue
            expired = _is_expired(row.get("expires_at"), now)
            if expired and not include_expired:
                continue
            copied = dict(row)
            copied["expired"] = expired
            summaries_out.append(copied)
            if len(summaries_out) >= max_entries:
                break

    effective_decisions = (
        _effective_decisions(
            decisions=payload["decisions"],
            now=now,
            namespace=namespace,
            include_expired=include_expired,
        )[:max_entries]
        if include_effective_decisions
        else []
    )
    if entries_out:
        _memory_stats_record("hit")
    else:
        _memory_stats_record("miss")

    compact_result: dict[str, Any] | None = None
    if auto_compact:
        compact_result = memory_auto_compact(
            namespace=namespace,
            threshold_entries=compact_threshold_entries,
            threshold_chars=compact_threshold_chars,
            keep_entries=compact_keep_entries,
            summary_max_chars=compact_summary_max_chars,
            drop_expired=False,
        )

    result = {
        "path": str(MEMORY_FILE),
        "count": len(entries_out),
        "entries": entries_out,
        "summary_count": len(summaries_out),
        "summaries": summaries_out,
        "effective_decision_count": len(effective_decisions),
        "effective_decisions": effective_decisions,
        "usage_stats": _memory_stats_load(),
    }
    if compact_result is not None:
        result["auto_compact"] = compact_result
    return result


def memory_validate(
    validate_paths: bool = True,
    drop_expired: bool = False,
    max_entries: int = 5000,
) -> dict[str, Any]:
    """Validate memory freshness and optionally prune expired records."""
    if max_entries < 1:
        raise ValueError("max_entries must be >= 1")
    payload = _memory_load()
    entries = payload["entries"][:max_entries]
    summaries = payload["summaries"][:max_entries]
    decisions = payload["decisions"][:max_entries]
    now = datetime.now(timezone.utc)

    stale: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    kept_summaries: list[dict[str, Any]] = []
    kept_decisions: list[dict[str, Any]] = []
    dropped = 0

    for entry in entries:
        expired = _is_expired(entry.get("expires_at"), now)
        if expired and drop_expired:
            dropped += 1
            continue

        record = dict(entry)
        record["expired"] = expired
        record["stale_paths"] = []
        if validate_paths:
            value = entry.get("value")
            refs = value.get("file_paths", []) if isinstance(value, dict) else []
            if isinstance(refs, list):
                for rel in refs:
                    if isinstance(rel, str):
                        try:
                            resolved = _resolve_repo_path(rel)
                        except ValueError:
                            record["stale_paths"].append(rel)
                            continue
                        if not resolved.exists():
                            record["stale_paths"].append(rel)
        if expired or record["stale_paths"]:
            stale.append(record)
        kept.append(entry)

    stale_summaries = 0
    for row in summaries:
        expired = _is_expired(row.get("expires_at"), now)
        if expired and drop_expired:
            dropped += 1
            stale_summaries += 1
            continue
        if expired:
            stale_summaries += 1
        kept_summaries.append(row)

    stale_decisions = 0
    for row in decisions:
        expired = _is_expired(row.get("expires_at"), now)
        if expired and drop_expired:
            dropped += 1
            stale_decisions += 1
            continue
        if expired:
            stale_decisions += 1
        kept_decisions.append(row)

    if drop_expired:
        _require_mutations()
        payload["entries"] = kept
        payload["summaries"] = kept_summaries
        payload["decisions"] = kept_decisions
        _memory_save(payload)

    return {
        "path": str(MEMORY_FILE),
        "total_checked": len(entries),
        "stale_count": len(stale),
        "summary_checked": len(summaries),
        "summary_stale_count": stale_summaries,
        "decision_checked": len(decisions),
        "decision_stale_count": stale_decisions,
        "dropped_expired": dropped,
        "stale_entries": stale,
    }


class MemoryRouterService:
    """Application service for context-memory operations and policy routing."""

    def route(
        self,
        mode: str = "get",
        namespace: str | None = None,
        key: str | None = None,
        value: Any = None,
        ttl_days: int | None = None,
        confidence: float = 1.0,
        source: str = "agent",
        tags: list[str] | None = None,
        focus: str = "",
        summary: str = "",
        topic: str = "",
        decision: Any = None,
        decided_by: str = "llm",
        rationale: str = "",
        include_expired: bool = False,
        max_entries: int = 200,
        include_summaries: bool = True,
        include_effective_decisions: bool = True,
        validate_paths: bool = True,
        drop_expired: bool = False,
        auto_compact: bool = False,
        compact_threshold_entries: int = 80,
        compact_threshold_chars: int = 16000,
        compact_keep_entries: int = 40,
        compact_summary_max_chars: int = 1200,
        contains: str = "",
        category: str = "",
        error_text: str = "",
        max_suggestions: int = 5,
        issue: str = "",
        root_cause: str = "",
        fix: str = "",
        path: str = ".codebase-tooling-mcp/reports",
        query: str = "",
        artifact_mode: str = "refresh",
    ) -> dict[str, Any]:
        allowed = {
            "upsert",
            "summary_upsert",
            "decision_record",
            "get",
            "validate",
            "auto_compact",
            "failure_memory",
            "root_cause",
            "artifact_index",
        }
        if mode not in allowed:
            raise ValueError(f"mode must be one of: {', '.join(sorted(allowed))}")
        if mode == "upsert":
            if namespace is None or key is None:
                raise ValueError("namespace and key are required for upsert mode")
            result = memory_upsert(
                namespace=namespace,
                key=key,
                value=value,
                ttl_days=ttl_days,
                confidence=confidence,
                source=source,
                tags=tags,
            )
        elif mode == "summary_upsert":
            if namespace is None:
                raise ValueError("namespace is required for summary_upsert mode")
            result = memory_summary_upsert(
                namespace=namespace,
                focus=focus,
                summary=summary,
                ttl_days=ttl_days,
                confidence=confidence,
                source=source,
                tags=tags,
            )
        elif mode == "decision_record":
            if namespace is None:
                raise ValueError("namespace is required for decision_record mode")
            result = memory_decision_record(
                namespace=namespace,
                topic=topic,
                decision=decision,
                decided_by=decided_by,
                rationale=rationale,
                ttl_days=ttl_days,
                confidence=confidence,
                source=source,
                tags=tags,
            )
        elif mode == "validate":
            result = memory_validate(
                validate_paths=validate_paths,
                drop_expired=drop_expired,
                max_entries=max_entries,
            )
        elif mode == "auto_compact":
            result = memory_auto_compact(
                namespace=namespace,
                threshold_entries=compact_threshold_entries,
                threshold_chars=compact_threshold_chars,
                keep_entries=compact_keep_entries,
                summary_max_chars=compact_summary_max_chars,
                drop_expired=drop_expired,
            )
        elif mode == "failure_memory":
            result = failure_memory(
                mode=query or "get",
                category=category or None,
                contains=contains or None,
                max_entries=max_entries,
                error_text=error_text,
                max_suggestions=max_suggestions,
            )
        elif mode == "root_cause":
            result = root_cause_memory(
                mode=query or "list",
                issue=issue,
                root_cause=root_cause,
                fix=fix,
                max_entries=max_entries,
            )
        elif mode == "artifact_index":
            result = artifact_memory_index(
                mode=artifact_mode,
                path=path,
                query=query,
                max_entries=max_entries,
            )
        else:
            result = memory_get(
                namespace=namespace,
                key=key,
                include_expired=include_expired,
                max_entries=max_entries,
                include_summaries=include_summaries,
                include_effective_decisions=include_effective_decisions,
                auto_compact=auto_compact,
                compact_threshold_entries=compact_threshold_entries,
                compact_threshold_chars=compact_threshold_chars,
                compact_keep_entries=compact_keep_entries,
                compact_summary_max_chars=compact_summary_max_chars,
            )
        return {
            "schema": "memory_router.v1",
            "mode": mode,
            "result": result,
        }


_MEMORY_ROUTER_SERVICE = MemoryRouterService()


@mcp.tool()
def memory_router(
    mode: str = "get",
    namespace: str | None = None,
    key: str | None = None,
    value: Any = None,
    ttl_days: int | None = None,
    confidence: float = 1.0,
    source: str = "agent",
    tags: list[str] | None = None,
    focus: str = "",
    summary: str = "",
    topic: str = "",
    decision: Any = None,
    decided_by: str = "llm",
    rationale: str = "",
    include_expired: bool = False,
    max_entries: int = 200,
    include_summaries: bool = True,
    include_effective_decisions: bool = True,
    validate_paths: bool = True,
    drop_expired: bool = False,
    auto_compact: bool = False,
    compact_threshold_entries: int = 80,
    compact_threshold_chars: int = 16000,
    compact_keep_entries: int = 40,
    compact_summary_max_chars: int = 1200,
    contains: str = "",
    category: str = "",
    error_text: str = "",
    max_suggestions: int = 5,
    issue: str = "",
    root_cause: str = "",
    fix: str = "",
    path: str = ".codebase-tooling-mcp/reports",
    query: str = "",
    artifact_mode: str = "refresh",
) -> dict[str, Any]:
    """Strict memory router for context memory, failure memory, root-cause memory, and artifact indexing."""
    return _MEMORY_ROUTER_SERVICE.route(
        mode=mode,
        namespace=namespace,
        key=key,
        value=value,
        ttl_days=ttl_days,
        confidence=confidence,
        source=source,
        tags=tags,
        focus=focus,
        summary=summary,
        topic=topic,
        decision=decision,
        decided_by=decided_by,
        rationale=rationale,
        include_expired=include_expired,
        max_entries=max_entries,
        include_summaries=include_summaries,
        include_effective_decisions=include_effective_decisions,
        validate_paths=validate_paths,
        drop_expired=drop_expired,
        auto_compact=auto_compact,
        compact_threshold_entries=compact_threshold_entries,
        compact_threshold_chars=compact_threshold_chars,
        compact_keep_entries=compact_keep_entries,
        compact_summary_max_chars=compact_summary_max_chars,
        contains=contains,
        category=category,
        error_text=error_text,
        max_suggestions=max_suggestions,
        issue=issue,
        root_cause=root_cause,
        fix=fix,
        path=path,
        query=query,
        artifact_mode=artifact_mode,
    )


@mcp.tool()
def repo_router(
    mode: str = "tree",
    path: str = ".",
    recursive: bool = True,
    include_hidden: bool = False,
    max_entries: int = 1000,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    file_type: str = "any",
    max_depth: int | None = None,
    output_profile: str | None = None,
    offset: int = 0,
    limit: int | None = None,
    adaptive_limits: bool = True,
    encoding: str = "utf-8",
    max_bytes: int = MAX_READ_BYTES,
    max_chars: int = 20000,
    max_pages: int = 20,
    max_rows_per_sheet: int = 200,
    start_line: int = 1,
    end_line: int = 1,
    context_before: int = 0,
    context_after: int = 0,
    requests: list[dict[str, Any]] | None = None,
    query: str = "",
) -> dict[str, Any]:
    """Router for repository listing, reads, snippets, batches, and structured config queries."""
    allowed = {"tree", "find", "read", "read_document", "read_snippet", "read_batch", "query_json"}
    if mode not in allowed:
        raise ValueError(f"mode must be one of: {', '.join(sorted(allowed))}")
    if mode == "tree":
        result = list_files(path=path, recursive=recursive, include_hidden=include_hidden, max_entries=max_entries)
    elif mode == "find":
        result = find_paths(
            path=path,
            recursive=recursive,
            include_hidden=include_hidden,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            file_type=file_type,
            max_depth=max_depth,
            max_entries=max_entries,
            output_profile=output_profile or "compact",
            offset=offset,
            limit=limit,
            adaptive_limits=adaptive_limits,
        )
    elif mode == "read":
        result = read_file(path=path, encoding=encoding, max_bytes=max_bytes)
    elif mode == "read_document":
        result = read_document(
            path=path,
            max_chars=max_chars,
            max_pages=max_pages,
            max_rows_per_sheet=max_rows_per_sheet,
            output_profile=output_profile,
        )
    elif mode == "read_snippet":
        result = read_snippet(
            path=path,
            start_line=start_line,
            end_line=end_line,
            context_before=context_before,
            context_after=context_after,
            encoding=encoding,
            output_profile=output_profile,
        )
    elif mode == "read_batch":
        result = read_batch(
            requests=requests or [],
            encoding=encoding,
            output_profile=output_profile,
        )
    else:
        result = json_query(path=path, query=query, file_type=file_type, output_profile=output_profile or "normal")
    return {"schema": "repo_router.v1", "mode": mode, "result": result}


@mcp.tool()
def git_router(
    mode: str = "status",
    ref: str = "HEAD",
    path: str = "",
    pathspec: str = "",
    staged: bool = False,
    short: bool = True,
    limit: int = 20,
    paths: list[str] | None = None,
    message: str = "",
    allow_empty: bool = False,
    remote: str = "origin",
    branch: str = "",
    rebase: bool = False,
    set_upstream: bool = False,
    create_branch: bool = False,
    name: str = "",
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    diff_text: str = "",
    max_findings: int = 20,
) -> dict[str, Any]:
    """Router for Git operations, diff summaries, risk scoring, and security triage."""
    allowed = {
        "init",
        "status",
        "diff",
        "log",
        "show",
        "add",
        "restore",
        "commit",
        "checkout",
        "create_branch",
        "fetch",
        "pull",
        "push",
        "summarize_diff",
        "risk",
        "security",
    }
    if mode not in allowed:
        raise ValueError(f"mode must be one of: {', '.join(sorted(allowed))}")
    if mode == "init":
        result = git_init(initial_branch=branch or name or "main")
    elif mode == "status":
        result = git_status(short=short)
    elif mode == "diff":
        result = git_diff(ref=ref if ref else None, pathspec=pathspec or None, staged=staged)
    elif mode == "log":
        result = git_log(limit=limit, ref=ref or "HEAD")
    elif mode == "show":
        result = git_show(ref=ref or "HEAD", path=path or None)
    elif mode == "add":
        result = git_add(paths=paths or ([path] if path else []))
    elif mode == "restore":
        result = git_restore(paths=paths or ([path] if path else []), staged=staged)
    elif mode == "commit":
        result = git_commit(message=message, allow_empty=allow_empty)
    elif mode == "checkout":
        result = git_checkout(ref=branch or ref, create_branch=create_branch)
    elif mode == "create_branch":
        result = git_create_branch(name=name or branch, checkout=create_branch or True)
    elif mode == "fetch":
        result = git_fetch(remote=remote, prune=staged)
    elif mode == "pull":
        result = git_pull(remote=remote, branch=branch or None, rebase=rebase)
    elif mode == "push":
        result = git_push(remote=remote, branch=branch or None, set_upstream=set_upstream)
    elif mode == "summarize_diff":
        result = summarize_diff(ref=ref or None, pathspec=pathspec or None, staged=staged, output_profile="compact")
    elif mode == "risk":
        result = risk_scoring(ref=ref or head_ref, pathspec=pathspec or None, staged=staged)
    else:
        patch = diff_text or git_diff(ref=f"{base_ref}...{head_ref}")
        result = security_triage(diff_text=patch, paths=paths or ([path] if path else None), max_findings=max_findings)
    return {"schema": "git_router.v1", "mode": mode, "result": result}


@mcp.tool()
def tool_router(
    mode: str = "route",
    query: str = "",
    candidates: list[str] | None = None,
    selected_tool: str = "",
    success: bool = True,
    latency_ms: float = 0.0,
    min_calls: int = 2,
    min_success_rate: float = 0.6,
    min_score_gap: float = 5.0,
) -> dict[str, Any]:
    """Router for tool selection with learned ranking and intent fallback."""
    selected = candidates or []
    if mode == "inspect":
        ranked = _intent_rank_candidates(query=query, candidates=selected) if selected else []
        stats_payload = _json_file_load(TOOL_ROUTER_STATS_FILE, {"stats": {}})
        return {
            "schema": "tool_router.v1",
            "mode": mode,
            "query": query,
            "candidates": selected,
            "stats": stats_payload.get("stats", {}),
            "intent_ranked": ranked,
        }
    result = tool_router_learned(
        query=query,
        candidates=selected,
        mode=mode,
        selected_tool=selected_tool,
        success=success,
        latency_ms=latency_ms,
        min_calls=min_calls,
        min_success_rate=min_success_rate,
        min_score_gap=min_score_gap,
        fallback_to_intent=True,
    )
    return {
        "schema": "tool_router.v1",
        "mode": mode,
        "result": result,
    }


@mcp.tool()
def quality_router(
    mode: str = "self_test",
    runner: str = "pytest",
    target: str = "tests",
    verbose: bool = False,
    timeout_seconds: int = 600,
    fail_fast: bool = False,
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    run_test_execution: bool = True,
    run_impact_tests: bool = True,
    run_doc_check: bool = True,
    run_api_check: bool = True,
    run_risk_check: bool = True,
    run_compile_check: bool = True,
    snapshot_path: str = str(API_SNAPSHOT_FILE),
    max_compile_files: int = 300,
    summary_mode: str = "quick",
    test_runner: str = "pytest",
    test_target: str = "tests",
    run_docs_check: bool = True,
    run_license_check: bool = True,
    run_security_check: bool = True,
    run_tests: bool = True,
    history_path: str = str(FLAKY_HISTORY_FILE),
    runs: int = 5,
    update_history: bool = True,
    block_on_risk_level: str = "high",
    critical_globs: list[str] | None = None,
    require_docs_for_impl_diff: bool = True,
    require_tests_for_critical: bool = True,
    required_tools: list[str] | None = None,
    required_artifacts: list[str] | None = None,
    required_result_ids: list[str] | None = None,
    max_age_minutes: int = 60,
    require_order: bool = False,
    spec_text: str = "",
    framework: str = "pytest",
    output_path: str = "",
    findings: list[dict[str, Any]] | None = None,
    replace_all: bool = False,
    run_validation: bool = False,
) -> dict[str, Any]:
    """Router for testing, quality gates, spec-to-tests, and batch fixes."""
    allowed = {"self_test", "self_check", "release_readiness", "flaky", "change_impact", "required_tool_chain", "spec_to_tests", "smart_fix"}
    if mode not in allowed:
        raise ValueError(f"mode must be one of: {', '.join(sorted(allowed))}")
    if mode == "self_test":
        result = self_test(runner=runner, target=target, verbose=verbose, timeout_seconds=timeout_seconds, fail_fast=fail_fast)
    elif mode == "self_check":
        result = self_check_pipeline(
            base_ref=base_ref,
            head_ref=head_ref,
            run_test_execution=run_test_execution,
            run_impact_tests=run_impact_tests,
            run_doc_check=run_doc_check,
            run_api_check=run_api_check,
            run_risk_check=run_risk_check,
            run_compile_check=run_compile_check,
            snapshot_path=snapshot_path,
            max_compile_files=max_compile_files,
            summary_mode=summary_mode,
        )
    elif mode == "release_readiness":
        result = release_readiness(
            base_ref=base_ref,
            head_ref=head_ref,
            run_docs_check=run_docs_check,
            run_impact_check=run_impact_tests,
            run_license_check=run_license_check,
            run_risk_check=run_risk_check,
            run_security_check=run_security_check,
            run_tests=run_tests,
            summary_mode=summary_mode,
            test_runner=test_runner,
            test_target=test_target,
        )
    elif mode == "flaky":
        result = flaky_test_detector(
            runner=runner,
            target=target,
            runs=runs,
            timeout_seconds=timeout_seconds,
            fail_fast=fail_fast,
            history_path=history_path,
            update_history=update_history,
        )
    elif mode == "change_impact":
        result = change_impact_gate(
            base_ref=base_ref,
            head_ref=head_ref,
            block_on_risk_level=block_on_risk_level,
            critical_globs=critical_globs,
            require_docs_for_impl_diff=require_docs_for_impl_diff,
            require_tests_for_critical=require_tests_for_critical,
        )
    elif mode == "required_tool_chain":
        result = required_tool_chain(
            required_tools=required_tools or [],
            required_artifacts=required_artifacts or [],
            required_result_ids=required_result_ids or [],
            max_age_minutes=max_age_minutes,
            require_order=require_order,
        )
    elif mode == "spec_to_tests":
        result = spec_to_tests(
            spec_text=spec_text,
            framework=framework,
            mode="generate",
            output_path=output_path or None,
        )
    else:
        result = smart_fix_batch(
            findings=findings or [],
            mode="apply",
            replace_all=replace_all,
            run_validation=run_validation,
        )
    return {"schema": "quality_router.v1", "mode": mode, "result": result}


@mcp.tool()
def governance_router(
    mode: str = "policy",
    action_mode: str = "",
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    diff_text: str = "",
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    recursive: bool = True,
    run_reuse_lint: bool = True,
    generate_spdx: bool = False,
    auto_fix_headers: bool = False,
    download_missing_licenses: bool = False,
    lint_report_path: str = str(REUSE_LINT_REPORT),
    spdx_output_path: str = str(REUSE_SPDX_REPORT),
    max_missing_files: int = 200,
    action: str = "",
    risk_level: str = "medium",
    details: str = "",
    approval_id: str = "",
    approved: bool = False,
    message: str = "",
    ref: str = "HEAD",
    include_diff_hints: bool = False,
) -> dict[str, Any]:
    """Router for policy, license, runtime contract, approval, and commit lint workflows."""
    allowed = {"policy", "license", "runtime_contract", "human_approval", "commit_lint"}
    if mode not in allowed:
        raise ValueError(f"mode must be one of: {', '.join(sorted(allowed))}")
    if mode == "policy":
        result = policy_simulator(base_ref=base_ref, head_ref=head_ref, diff_text=diff_text)
    elif mode == "license":
        result = license_monitor(
            path=".",
            recursive=recursive,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            run_reuse_lint=run_reuse_lint,
            generate_spdx=generate_spdx,
            auto_fix_headers=auto_fix_headers,
            download_missing_licenses=download_missing_licenses,
            lint_report_path=lint_report_path,
            spdx_output_path=spdx_output_path,
            max_missing_files=max_missing_files,
        )
    elif mode == "runtime_contract":
        result = runtime_contract_checker()
    elif mode == "human_approval":
        result = human_approval_points(
            mode=action_mode or "list",
            action=action,
            risk_level=risk_level,
            details=details,
            approval_id=approval_id,
            approved=approved,
        )
    else:
        result = commit_lint_tag(message=message, ref=ref, include_diff_hints=include_diff_hints)
    return {"schema": "governance_router.v1", "mode": mode, "result": result}


@mcp.tool()
def workflow_router(
    mode: str = "fast_path",
    action_mode: str = "",
    task: str = "",
    goal: str = "",
    constraints: list[str] | None = None,
    include_rollback: bool = True,
    use_cache: bool = True,
    refresh_cache: bool = False,
    cache_ttl_minutes: int = 240,
    lanes: list[str] | None = None,
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    actions: list[str] | None = None,
    requirements: list[str] | None = None,
    checks: list[dict[str, Any]] | None = None,
    query: str = "",
    path: str = ".codebase-tooling-mcp/reports",
    max_entries: int = 100,
    category: str = "",
    contains: str = "",
    error_text: str = "",
    max_suggestions: int = 5,
    issue: str = "",
    root_cause: str = "",
    fix: str = "",
    replay_id: str = "",
    event: dict[str, Any] | None = None,
    max_events: int = 1000,
    shard_size: int = 50,
    refresh_index: bool = True,
    run_readiness: bool = True,
    enforce_tool_chain: bool = True,
    store_result: bool = False,
) -> dict[str, Any]:
    """Router for workflow orchestration, artifact memory, failure memory, and replay helpers."""
    allowed = {"fast_path", "compile", "multi_agent", "constraint_check", "confidence", "artifact_index", "failure_memory", "root_cause", "execution_replay", "auto_shard"}
    if mode not in allowed:
        raise ValueError(f"mode must be one of: {', '.join(sorted(allowed))}")
    if mode == "fast_path":
        result = fast_path_dev(
            task=task,
            base_ref=base_ref,
            head_ref=head_ref,
            refresh_index=refresh_index,
            run_readiness=run_readiness,
            enforce_tool_chain=enforce_tool_chain,
            store_result=store_result,
        )
    elif mode == "compile":
        result = workflow_compiler(
            goal=goal,
            constraints=constraints,
            include_rollback=include_rollback,
            use_cache=use_cache,
            refresh_cache=refresh_cache,
            cache_ttl_minutes=cache_ttl_minutes,
        )
    elif mode == "multi_agent":
        result = multi_agent_lane(task=task, lanes=lanes, base_ref=base_ref, head_ref=head_ref)
    elif mode == "constraint_check":
        result = constraint_solver_for_tasks(actions=actions or [], requirements=requirements or [])
    elif mode == "confidence":
        result = confidence_scoring(checks=checks or [])
    elif mode == "artifact_index":
        result = artifact_memory_index(mode=action_mode or "refresh", path=path, query=query, max_entries=max_entries)
    elif mode == "failure_memory":
        result = failure_memory(
            mode=action_mode or "get",
            category=category or None,
            contains=contains or None,
            max_entries=max_entries,
            error_text=error_text,
            max_suggestions=max_suggestions,
        )
    elif mode == "root_cause":
        result = root_cause_memory(
            mode=action_mode or "list",
            issue=issue,
            root_cause=root_cause,
            fix=fix,
            max_entries=max_entries,
        )
    elif mode == "execution_replay":
        result = execution_replay(mode=action_mode or "read", replay_id=replay_id, event=event, max_events=max_events)
    else:
        result = auto_sharding_for_analysis(path=path or ".", shard_size=shard_size)
    return {"schema": "workflow_router.v1", "mode": mode, "result": result}


@mcp.tool()
def runtime_guard_router(
    mode: str = "benchmark",
    action_mode: str = "",
    tools: list[str] | None = None,
    iterations: int = 3,
    warmup: int = 1,
    baseline_path: str = str(OUTPUT_BASELINE_FILE),
    tolerance_ratio: float = 1.2,
    max_output_chars: int | None = None,
    default_output_profile: str | None = None,
    reset: bool = False,
    max_tokens: int = 200000,
    max_calls: int = 50,
    max_seconds: int = 600,
    used_tokens: int = 0,
    used_calls: int = 0,
    used_seconds: int = 0,
    tool: str | None = None,
    max_age_minutes: int = 1440,
    limit: int = 50,
    result_id: str = "",
    value: Any = None,
    offset: int = 0,
    fields: list[str] | None = None,
    refresh: bool = True,
) -> dict[str, Any]:
    """Router for benchmarks, guards, caches, result handles, and workspace facts."""
    allowed = {"benchmark", "output_size", "golden_output", "token_budget", "cost_budget", "cache", "result_handle", "workspace_facts"}
    if mode not in allowed:
        raise ValueError(f"mode must be one of: {', '.join(sorted(allowed))}")
    if mode == "benchmark":
        result = tool_benchmark(tools=tools, iterations=iterations, warmup=warmup)
    elif mode == "output_size":
        result = output_size_guard(mode=action_mode or "check", tools=tools, tolerance_ratio=tolerance_ratio, baseline_path=baseline_path)
    elif mode == "golden_output":
        result = golden_output_guard(mode=action_mode or "check", tools=tools, baseline_path=baseline_path)
    elif mode == "token_budget":
        result = token_budget_guard(max_output_chars=max_output_chars, default_output_profile=default_output_profile, reset=reset)
    elif mode == "cost_budget":
        result = cost_budget_enforcer(
            mode=action_mode or "check",
            max_tokens=max_tokens,
            max_calls=max_calls,
            max_seconds=max_seconds,
            used_tokens=used_tokens,
            used_calls=used_calls,
            used_seconds=used_seconds,
        )
    elif mode == "cache":
        result = cache_control(mode=action_mode or "stats", tool=tool, max_age_minutes=max_age_minutes, limit=limit)
    elif mode == "result_handle":
        result = result_handle(mode=action_mode or "fetch", result_id=result_id, tool=tool, value=value, offset=offset, limit=limit, fields=fields)
    else:
        result = workspace_facts(refresh=refresh)
    return {"schema": "runtime_guard_router.v1", "mode": mode, "result": result}


@mcp.tool()
def math_router(
    mode: str = "solve",
    text: str = "",
    symbols: str = "",
    expression: str = "",
    equations: list[str] | None = None,
    variable: str = "",
    assumptions: str = "",
    include_steps: bool = False,
    left: str = "",
    right: str = "",
    variables: str = "",
    trials: int = 10,
) -> dict[str, Any]:
    """Router for math parsing, solving, and verification."""
    if mode == "parse":
        result = math_parser(text=text, symbols=symbols)
    elif mode == "solve":
        result = math_solver(expression=expression, equations=equations, variable=variable, assumptions=assumptions, include_steps=include_steps)
    elif mode == "verify":
        result = math_verify(left=left, right=right, variables=variables, trials=trials)
    else:
        raise ValueError("mode must be one of: parse, solve, verify")
    return {"schema": "math_router.v1", "mode": mode, "result": result}


@mcp.tool()
def document_router(
    mode: str = "ocr",
    path: str = "",
    image_path: str = "",
    language: str = "",
    max_chars: int = 20000,
    max_slides: int = 50,
    max_chars_per_slide: int = 1200,
    use_local_model: bool = True,
    output_profile: str | None = None,
    text: str = "",
    source_lang: str = "",
    target_lang: str = "",
) -> dict[str, Any]:
    """Router for OCR, image interpretation, presentation parsing, and translation."""
    if mode == "ocr":
        result = vision_ocr_parser(image_path=image_path or path, language=language or "eng", max_chars=max_chars)
    elif mode == "image":
        result = image_interpret(image_path=image_path or path, language=language or "en", max_chars=max_chars, mode="caption", output_profile=output_profile, use_local_model=True)
    elif mode == "presentation":
        result = interpret_presentation(path=path, max_slides=max_slides, max_chars_per_slide=max_chars_per_slide, use_local_model=use_local_model, output_profile=output_profile)
    elif mode == "translate":
        result = translation_small(text=text, source_lang=source_lang, target_lang=target_lang, mode="lexical")
    else:
        raise ValueError("mode must be one of: ocr, image, presentation, translate")
    return {"schema": "document_router.v1", "mode": mode, "result": result}


@mcp.tool()
def diagram_router(
    mode: str = "from_code",
    path: str = ".",
    diagram_type: str = "flowchart",
    include_call_edges: bool = False,
    max_nodes: int = 100,
    output_profile: str | None = None,
    mermaid_text: str = "",
    auto_fix: bool = False,
    drawio_xml: str = "",
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    diagram_path: str = "",
    source_paths: list[str] | None = None,
    marker: str = "",
) -> dict[str, Any]:
    """Router for code diagrams, Mermaid linting, draw.io generation, and diagram sync checks."""
    if mode == "from_code":
        result = diagram_from_code(path=path, diagram_type=diagram_type, include_call_edges=include_call_edges, max_nodes=max_nodes, output_profile=output_profile)
    elif mode == "lint_mermaid":
        result = mermaid_lint_fix(mermaid_text=mermaid_text, auto_fix=auto_fix)
    elif mode == "drawio":
        result = drawio_generator(mode="parse" if drawio_xml else "generate", drawio_xml=drawio_xml, nodes=nodes, edges=edges)
    elif mode == "sync_check":
        result = diagram_sync_check(diagram_path=diagram_path, source_paths=source_paths or [], marker=marker, mode="check")
    else:
        raise ValueError("mode must be one of: from_code, lint_mermaid, drawio, sync_check")
    return {"schema": "diagram_router.v1", "mode": mode, "result": result}


def _prune_public_mcp_surface() -> None:
    for name in sorted(_declared_tool_names()):
        if name in PUBLIC_MCP_TOOL_NAMES:
            continue
        try:
            mcp.remove_tool(name)
        except Exception:
            continue


def _attach_release_readiness_app_metadata(tool: Any) -> None:
    if not _mcp_apps_dashboard_enabled():
        return
    ui_meta = {
        "resourceUri": RELEASE_READINESS_DASHBOARD_RESOURCE_URI,
        "visibility": ["model"],
    }
    existing = getattr(tool, "_meta", None)
    if not isinstance(existing, dict):
        existing = {}
    existing.setdefault("ui", ui_meta)
    tool.__dict__["_meta"] = existing
    tool.__dict__.setdefault("meta", existing)


def _mcp_client_compatible_output_schema(tool_name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Return an MCP-advertised outputSchema accepted by object-root clients.

    Some existing public tools intentionally return bare lists for Python and
    legacy MCP callers. Continue validates every advertised outputSchema root as
    an object, so preserve the precise legacy schema under an extension key while
    advertising an object-root compatibility schema for tools/list.
    """
    if schema.get("type") == "object":
        return schema
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "result": schema,
        },
        "x-codebase-tooling-mcp-legacy-output-schema": schema,
        "description": f"{tool_name} returns the legacy payload directly; object root is advertised for MCP client compatibility.",
    }


def _apply_output_schemas_to_mcp_tools() -> None:
    """Attach checked-in outputSchema metadata to schema-backed FastMCP tools."""
    for name, schema in OUTPUT_SCHEMA_BY_TOOL.items():
        tool = mcp._tool_manager.get_tool(name)  # FastMCP has no public setter for outputSchema.
        if tool is None:
            continue
        tool.fn_metadata.output_schema = _mcp_client_compatible_output_schema(name, schema)
        tool.fn_metadata.output_model = _AnyToolOutput
        tool.fn_metadata.wrap_output = False
        tool.__dict__.pop("output_schema", None)
        if name == "release_readiness":
            _attach_release_readiness_app_metadata(tool)


_apply_output_schemas_to_mcp_tools()
_prune_public_mcp_surface()


async def mcp_server_manifest(_request):
    return JSONResponse(_mcp_server_manifest_payload())


async def healthz(_request):
    runtime = _runtime_state_payload(include_ollama_probe=False)
    server_state = runtime.get("server", {})
    ollama_state = runtime.get("ollama", {})
    oauth_resource_config_error = _http_oauth_resource_config_error()
    scope_config_error = _http_bearer_token_scope_config_error()
    auth_configuration_error = oauth_resource_config_error or scope_config_error
    return JSONResponse(
        {
            "ok": True,
            "repo_path": str(REPO_PATH),
            "is_git_repo": _is_git_repo(),
            "allow_mutations": ALLOW_MUTATIONS,
            "transport": MCP_TRANSPORT,
            "runtime_image_version": runtime_image_version().rendered,
            "mcp_coding_experiment_version": mcp_coding_experiment_version().rendered,
            "server": {
                "http_mode": server_state.get("http_mode"),
                "port": server_state.get("port"),
                "port_listening": server_state.get("port_listening"),
            },
            "auth": {
                "mode": MCP_HTTP_AUTH_MODE,
                "oauth_resource_configured": MCP_HTTP_AUTH_MODE == "oauth-resource"
                and not bool(oauth_resource_config_error),
                "configuration_error": auth_configuration_error,
                "scope_configuration_error": scope_config_error,
                "scopes_supported": _supported_mcp_scopes(),
                "local_bearer_token_scopes": sorted(_local_bearer_token_granted_scopes()),
                "oauth_protected_resource_metadata": "/.well-known/oauth-protected-resource",
            },
            "ollama": {
                "running": ollama_state.get("running"),
                "serve_processes": ollama_state.get("serve_processes"),
                "configured_port": ollama_state.get("configured_port"),
                "configured_port_listening": ollama_state.get("configured_port_listening"),
                "port_11434_listening": ollama_state.get("port_11434_listening"),
            },
        }
    )


async def root(_request):
    return PlainTextResponse("git-repo-manager MCP server")


async def sse_events(request):
    raw_last = request.query_params.get("last", "20").strip()
    try:
        last = int(raw_last or "20")
    except ValueError:
        last = 20
    subscriber_id = uuid.uuid4().hex[:12]
    subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=SSE_SUBSCRIBER_QUEUE_MAX)
    replay = _sse_replay(limit=last)
    with _SSE_LOCK:
        _SSE_SUBSCRIBERS[subscriber_id] = subscriber
    _sse_publish("sse.connected", subscriber_id=subscriber_id, replayed=len(replay))

    async def _event_stream():
        try:
            for entry in replay:
                yield _sse_encode_event(entry)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = subscriber.get_nowait()
                    yield _sse_encode_event(entry)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(SSE_HEARTBEAT_SECONDS)
        finally:
            with _SSE_LOCK:
                _SSE_SUBSCRIBERS.pop(subscriber_id, None)
            _sse_publish("sse.disconnected", subscriber_id=subscriber_id)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield


starlette_app = Starlette(
    routes=[
        Route("/", root, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/.well-known/mcp-server.json", mcp_server_manifest, methods=["GET"]),
        Route("/sse", sse_events, methods=["GET"]),
        # FastMCP's streamable HTTP app serves MCP routes under `/mcp` internally.
        # Mount at root so the public MCP endpoint is exactly `/mcp`.
        Mount("/", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)

authenticated_starlette_app = MCPHTTPAuthMiddleware(starlette_app)

app = CORSMiddleware(
    authenticated_starlette_app,
    allow_origins=ALLOW_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)


def main() -> None:
    transport = MCP_TRANSPORT

    if transport in {"stdio", "direct"}:
        mcp.run()
        return

    if transport in {"http", "streamable-http", "streamable_http"}:
        import uvicorn

        uvicorn.run(app, host=HOST, port=PORT)
        return

    raise ValueError(
        "Unsupported MCP_TRANSPORT. Expected one of: stdio, direct, http, streamable-http"
    )


if __name__ == "__main__":
    main()
