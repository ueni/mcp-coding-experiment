# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

import contextlib
import ast
import json
import os
import shutil
import subprocess
import sys
import re
import fnmatch
import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

try:
    from tree_sitter_languages import get_parser as _ts_get_parser
except ModuleNotFoundError:  # pragma: no cover
    _ts_get_parser = None

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
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
LABS_DIR = Path("toolchain/dev/labs")
REPORTS_DIR = Path(".build/reports")
MEMORY_FILE = Path(".build/memory/context_memory.json")
FAILURE_MEMORY_FILE = Path(".build/memory/failure_memory.json")
TOKEN_BUDGET_FILE = Path(".build/memory/token_budget.json")
EDIT_TXN_DIR = Path(".build/transactions")
API_SNAPSHOT_FILE = Path(".build/reports/API_SURFACE.json")
REPO_INDEX_FILE = Path(".build/index/repo_index.json")
SAFE_COMMANDS = {"rg", "find", "sed", "awk", "jq", "git"}
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

mcp = FastMCP(
    "git-repo-manager",
    instructions=(
        "Manage exactly one mounted Git repository and its files. "
        "All paths are relative to the repository root."
    ),
)


def _trim_text(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n\n[truncated: output exceeded {max_chars} characters; original length={len(text)}]"
    )


def _ensure_repo_path_exists() -> None:
    REPO_PATH.mkdir(parents=True, exist_ok=True)


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


def _iter_candidate_files(root: Path, recursive: bool) -> Any:
    if root.is_file():
        yield root
        return
    if recursive:
        for p in root.rglob("*"):
            if p.is_file():
                yield p
        return
    for p in root.glob("*"):
        if p.is_file():
            yield p


def _read_lines(path: Path, encoding: str = "utf-8") -> list[str]:
    return path.read_text(encoding=encoding, errors="replace").splitlines()


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
        return {"entries": []}
    try:
        payload = json.loads(memory_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"entries": []}
    if not isinstance(payload, dict):
        return {"entries": []}
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    return {"entries": entries}


def _memory_save(payload: dict[str, Any]) -> None:
    memory_path = _resolve_repo_path(str(MEMORY_FILE))
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
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


def _token_budget_load() -> dict[str, Any]:
    payload = _json_file_load(
        TOKEN_BUDGET_FILE,
        {
            "max_output_chars": MAX_OUTPUT_CHARS,
            "default_output_profile": "normal",
        },
    )
    if not isinstance(payload, dict):
        return {"max_output_chars": MAX_OUTPUT_CHARS, "default_output_profile": "normal"}
    max_chars = payload.get("max_output_chars", MAX_OUTPUT_CHARS)
    profile = payload.get("default_output_profile", "normal")
    if not isinstance(max_chars, int) or max_chars < 1:
        max_chars = MAX_OUTPUT_CHARS
    if profile not in OUTPUT_PROFILES:
        profile = "normal"
    return {"max_output_chars": max_chars, "default_output_profile": profile}


def _token_budget_apply_max(max_chars: int | None) -> int:
    if isinstance(max_chars, int) and max_chars > 0:
        return max_chars
    return int(_token_budget_load()["max_output_chars"])


def _default_output_profile(output_profile: str | None) -> str:
    if output_profile and output_profile.strip():
        return _validate_output_profile(output_profile)
    return _validate_output_profile(_token_budget_load()["default_output_profile"])


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


def _language_parse_file(path: Path, language: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    symbols: list[dict[str, Any]] = []
    imports: list[str] = []
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        if language in {"javascript", "typescript"}:
            m = re.search(r"\b(function|class)\s+([A-Za-z_]\w*)", line)
            if m:
                symbols.append({"name": m.group(2), "kind": m.group(1), "line": idx})
            m2 = re.search(r"\b(const|let|var)\s+([A-Za-z_]\w*)\s*=\s*\(", line)
            if m2:
                symbols.append({"name": m2.group(2), "kind": "callable_var", "line": idx})
            imp = re.search(r"^\s*import\s+.*?from\s+[\"']([^\"']+)[\"']", line)
            if imp:
                imports.append(imp.group(1))
        elif language == "go":
            m = re.search(r"^\s*func\s+([A-Za-z_]\w*)", line)
            if m:
                symbols.append({"name": m.group(1), "kind": "func", "line": idx})
            imp = re.search(r'^\s*import\s+"([^"]+)"', line)
            if imp:
                imports.append(imp.group(1))
        elif language == "rust":
            m = re.search(r"^\s*(pub\s+)?(fn|struct|enum|trait)\s+([A-Za-z_]\w*)", line)
            if m:
                symbols.append({"name": m.group(3), "kind": m.group(2), "line": idx})
            imp = re.search(r"^\s*use\s+([^;]+);", line)
            if imp:
                imports.append(imp.group(1).strip())
    return {"symbols": symbols, "imports": imports}


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
        check=False,
        capture_output=True,
        text=True,
        cwd=str(REPO_PATH),
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
        msg = (
            stderr or stdout or f"{script_name} failed with exit code {proc.returncode}"
        )
        raise RuntimeError(msg)

    return result


@mcp.tool()
def repo_info() -> dict[str, Any]:
    """Return repository state and server settings."""
    _ensure_repo_path_exists()

    info: dict[str, Any] = {
        "repo_path": str(REPO_PATH),
        "repo_exists": REPO_PATH.exists(),
        "is_git_repo": _is_git_repo(),
        "allow_mutations": ALLOW_MUTATIONS,
        "transport": MCP_TRANSPORT,
        "max_read_bytes": MAX_READ_BYTES,
        "max_output_chars": MAX_OUTPUT_CHARS,
    }

    if info["is_git_repo"]:
        info["current_branch"] = _git("branch", "--show-current").stdout.strip()
        info["head"] = _git("rev-parse", "HEAD").stdout.strip()
        status = _git("status", "--porcelain").stdout.strip()
        info["dirty"] = bool(status)

    return info


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
def git_status(short: bool = True) -> str:
    """Return git status."""
    _require_git_repo()
    args = ["status"]
    if short:
        args.append("--short")
    return _trim_text(_git(*args).stdout)


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
    config_path: str = ".config/dev/labs/release_rehearsal.json",
    allow_dirty: bool = False,
    keep_branch: bool = False,
) -> dict[str, Any]:
    """Run release rehearsal lab and write report(s) under .build/reports."""
    _resolve_repo_path(config_path)
    args = ["--config", config_path]
    if allow_dirty:
        args.append("--allow-dirty")
    if keep_branch:
        args.append("--keep-branch")
    return _run_lab_script("release_rehearsal.py", args)


@mcp.tool()
def lab_refactor_tournament(
    config_path: str = ".config/dev/labs/refactor_tournament.json",
    allow_dirty: bool = False,
    keep_branches: bool = False,
) -> dict[str, Any]:
    """Run refactor tournament lab and write report(s) under .build/reports."""
    _resolve_repo_path(config_path)
    args = ["--config", config_path]
    if allow_dirty:
        args.append("--allow-dirty")
    if keep_branches:
        args.append("--keep-branches")
    return _run_lab_script("refactor_tournament.py", args)


@mcp.tool()
def lab_policy_gatekeeper(
    config_path: str = ".config/dev/labs/policy_gatekeeper.json",
    changed_ref: str = "HEAD",
    report_path: str = ".build/reports/POLICY_GATEKEEPER.md",
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
    config_path: str = ".config/dev/labs/branch_swarm_lab.json",
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
    output_path: str = ".build/reports/PR_PACKET.md",
) -> dict[str, Any]:
    """Generate a narrated PR packet for a commit range."""
    _resolve_repo_path(output_path)
    args = ["--base", base, "--head", head, "--output", output_path]
    return _run_lab_script("narrated_pr_generator.py", args)


@mcp.tool()
def lab_repo_digital_twin(
    json_path: str = ".build/reports/REPO_DIGITAL_TWIN.json",
    markdown_path: str = ".build/reports/REPO_DIGITAL_TWIN.md",
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
def find_paths(
    path: str = ".",
    recursive: bool = True,
    include_hidden: bool = False,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    file_type: str = "any",
    max_depth: int | None = None,
    max_entries: int = 1000,
    output_profile: str = "normal",
) -> list[str]:
    """Find files and/or directories under a repository-relative path."""
    if max_entries < 1:
        raise ValueError("max_entries must be >= 1")
    if max_depth is not None and max_depth < 0:
        raise ValueError("max_depth must be >= 0")
    if file_type not in {"any", "file", "dir"}:
        raise ValueError("file_type must be one of: any, file, dir")
    profile = _validate_output_profile(output_profile)

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
    output_profile: str = "normal",
) -> list[dict[str, Any]]:
    """Search repository files for a regex pattern and return matches.

    Returns a list of objects: { path, line, column, match, lineText }.
    Paths are repository-relative; line/column are 1-based.
    """
    if max_matches < 1:
        raise ValueError("max_matches must be >= 1")
    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be >= 1")
    profile = _validate_output_profile(output_profile)
    if profile == "compact":
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
        if recursive:
            for p in root.rglob("*"):
                if p.is_file():
                    yield p
            return
        for p in root.glob("*"):
            if p.is_file():
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
    output_profile: str = "normal",
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
    output_profile: str = "normal",
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


@mcp.tool()
def semantic_find(
    query: str,
    path: str = ".",
    max_results: int = 30,
    output_profile: str | None = None,
    include_private_symbols: bool = False,
) -> dict[str, Any]:
    """Ranked search over file paths, symbols, and text matches."""
    if not query.strip():
        raise ValueError("query must not be empty")
    if max_results < 1:
        raise ValueError("max_results must be >= 1")
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

    ranked = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)[
        :max_results
    ]
    if profile == "compact":
        ranked = [
            {
                "kind": r["kind"],
                "path": r["path"],
                "score": r["score"],
            }
            for r in ranked
        ]
    return {
        "query": query,
        "path": str(root.relative_to(REPO_PATH)),
        "count": len(ranked),
        "results": ranked,
    }


@mcp.tool()
def symbol_index(
    path: str = ".",
    include_private: bool = False,
    recursive: bool = True,
    max_symbols: int = 5000,
    encoding: str = "utf-8",
    output_profile: str = "normal",
) -> list[dict[str, Any]]:
    """Index Python symbols (classes/functions) for focused navigation."""
    if max_symbols < 1:
        raise ValueError("max_symbols must be >= 1")
    profile = _validate_output_profile(output_profile)
    if profile == "compact":
        max_symbols = min(max_symbols, 2000)

    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    symbols: list[dict[str, Any]] = []
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
        for symbol in extracted:
            if len(symbols) >= max_symbols:
                return symbols
            symbols.append(_symbol_to_profile(symbol, profile))
    return symbols


@mcp.tool()
def read_symbol(
    name: str,
    path: str = ".",
    occurrence: int = 1,
    include_private: bool = True,
    recursive: bool = True,
    encoding: str = "utf-8",
    output_profile: str = "normal",
) -> dict[str, Any]:
    """Read source for a named Python symbol."""
    profile = _validate_output_profile(output_profile)
    if occurrence < 1:
        raise ValueError("occurrence must be >= 1")

    matches: list[dict[str, Any]] = []
    for symbol in symbol_index(
        path=path,
        include_private=include_private,
        recursive=recursive,
        max_symbols=20000,
        encoding=encoding,
        output_profile="normal",
    ):
        if symbol["name"] == name:
            matches.append(symbol)

    if not matches:
        raise FileNotFoundError(f"symbol not found: {name}")
    if occurrence > len(matches):
        raise ValueError(
            f"occurrence out of range: requested {occurrence}, found {len(matches)}"
        )

    target = matches[occurrence - 1]
    file_path = _resolve_repo_path(target["path"])
    lines = _read_lines(file_path, encoding=encoding)
    start = max(1, int(target["line_start"]))
    end = min(len(lines), int(target["line_end"]))
    content = "\n".join(lines[start - 1 : end])

    result = {
        "path": target["path"],
        "name": target["name"],
        "kind": target["kind"],
        "occurrence": occurrence,
        "line_start": start,
        "line_end": end,
        "content": content,
    }
    if profile == "compact":
        return {
            "path": result["path"],
            "name": result["name"],
            "line_start": result["line_start"],
            "line_end": result["line_end"],
            "content": result["content"],
        }
    return result


@mcp.tool()
def dependency_map(
    path: str = ".",
    recursive: bool = True,
    include_stdlib: bool = False,
    max_files: int = 3000,
    output_profile: str = "normal",
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Build a Python import dependency map for repo-local modules."""
    if max_files < 1:
        raise ValueError("max_files must be >= 1")
    profile = _validate_output_profile(output_profile)
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

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

    result: dict[str, Any] = {
        "root": str(root.relative_to(REPO_PATH)),
        "python_file_count": len(python_files),
        "edge_count": len(edges),
        "edges": edges,
    }
    if profile == "compact":
        return {
            "python_file_count": result["python_file_count"],
            "edge_count": result["edge_count"],
            "edges": edges[:500],
        }
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
    return result


@mcp.tool()
def call_graph(
    path: str = ".",
    recursive: bool = True,
    max_edges: int = 5000,
    output_profile: str | None = None,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Build a simple Python function-level call graph."""
    if max_edges < 1:
        raise ValueError("max_edges must be >= 1")
    profile = _default_output_profile(output_profile)
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

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

    if profile == "compact":
        return {"edge_count": len(edges), "edges": edges[:500]}
    inbound: dict[str, int] = {}
    for edge in edges:
        inbound[edge["callee"]] = inbound.get(edge["callee"], 0) + 1
    result: dict[str, Any] = {"edge_count": len(edges), "edges": edges}
    if profile == "verbose":
        result["most_called"] = sorted(
            [{"symbol": k, "count": v} for k, v in inbound.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:25]
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


@mcp.tool()
def command_runner(
    command: list[str],
    cwd: str = ".",
    timeout_seconds: int = 30,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    """Run a whitelisted command without shell interpolation."""
    if not command:
        raise ValueError("command must not be empty")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    out_cap = _token_budget_apply_max(max_output_chars)

    binary = command[0]
    if binary not in SAFE_COMMANDS:
        raise ValueError(f"command not allowed: {binary}")

    if binary == "git":
        if len(command) < 2:
            raise ValueError("git command must include a subcommand")
        if command[1] not in SAFE_GIT_SUBCOMMANDS:
            raise ValueError(f"git subcommand not allowed: {command[1]}")
    if binary == "sed" and any(arg == "-i" or arg.startswith("-i") for arg in command[1:]):
        raise ValueError("sed in-place edits are not allowed")
    if binary == "find" and any(arg in {"-delete", "-exec", "-ok"} for arg in command[1:]):
        raise ValueError("find destructive/exec flags are not allowed")
    if binary == "awk":
        script = command[1] if len(command) > 1 else ""
        if "system(" in script:
            raise ValueError("awk system() is not allowed")

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
        _failure_record(
            command=command,
            stderr="command timed out",
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            category="command_runner",
            suggestion="Increase timeout_seconds or narrow command scope.",
        )
        return {
            "ok": False,
            "exit_code": None,
            "command": command,
            "cwd": str(workdir.relative_to(REPO_PATH)),
            "stdout": _trim_text((exc.stdout or "") if isinstance(exc.stdout, str) else "", max_chars=out_cap),
            "stderr": _trim_text((exc.stderr or "") if isinstance(exc.stderr, str) else "", max_chars=out_cap),
            "timeout": True,
        }
    except FileNotFoundError as exc:
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
            "cwd": str(workdir.relative_to(REPO_PATH)),
            "stdout": "",
            "stderr": str(exc),
            "timeout": False,
        }
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
    }


@mcp.tool()
def test_targeted(
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    runner: list[str] | None = None,
    max_tests: int = 200,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run targeted tests inferred from changed paths in a git range."""
    _require_git_repo()
    if max_tests < 1:
        raise ValueError("max_tests must be >= 1")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    out_cap = _token_budget_apply_max(None)

    diff_out = _git("diff", "--name-only", f"{base_ref}...{head_ref}").stdout.strip()
    changed = [line.strip() for line in diff_out.splitlines() if line.strip()]

    candidates: list[str] = []
    for path in changed:
        p = Path(path)
        name = p.name
        stem = p.stem
        if "test" in name.lower() and p.suffix == ".py":
            candidates.append(path)
            continue
        if p.suffix == ".py":
            candidates.extend(
                [
                    f"tests/test_{stem}.py",
                    f"tests/{stem}_test.py",
                ]
            )

    seen: set[str] = set()
    tests: list[str] = []
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        resolved = _resolve_repo_path(item)
        if resolved.is_file():
            tests.append(item)
        if len(tests) >= max_tests:
            break

    run_cmd = runner or ["pytest", "-q"]
    if tests:
        full_cmd = [*run_cmd, *tests]
    else:
        full_cmd = run_cmd

    try:
        proc = subprocess.run(
            full_cmd,
            cwd=str(REPO_PATH),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        _failure_record(
            command=full_cmd,
            stderr="test run timed out",
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            category="test_targeted",
            suggestion="Increase timeout_seconds or reduce selected tests.",
        )
        return {
            "base_ref": base_ref,
            "head_ref": head_ref,
            "changed_files": changed,
            "targeted_tests": tests,
            "command": full_cmd,
            "ok": False,
            "exit_code": None,
            "timeout": True,
            "stdout": _trim_text((exc.stdout or "") if isinstance(exc.stdout, str) else "", max_chars=out_cap),
            "stderr": _trim_text((exc.stderr or "") if isinstance(exc.stderr, str) else "", max_chars=out_cap),
        }
    except FileNotFoundError as exc:
        _failure_record(
            command=full_cmd,
            stderr=str(exc),
            category="test_targeted",
            suggestion="Install the configured test runner or pass a valid runner list.",
        )
        return {
            "base_ref": base_ref,
            "head_ref": head_ref,
            "changed_files": changed,
            "targeted_tests": tests,
            "command": full_cmd,
            "ok": False,
            "exit_code": None,
            "timeout": False,
            "stdout": "",
            "stderr": str(exc),
        }
    if proc.returncode != 0:
        _failure_record(
            command=full_cmd,
            stderr=proc.stderr,
            stdout=proc.stdout,
            category="test_targeted",
            suggestion="Review failing tests and rerun with targeted scope.",
        )

    return {
        "base_ref": base_ref,
        "head_ref": head_ref,
        "changed_files": changed,
        "targeted_tests": tests,
        "command": full_cmd,
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": _trim_text(proc.stdout, max_chars=out_cap),
        "stderr": _trim_text(proc.stderr, max_chars=out_cap),
    }


@mcp.tool()
def summarize_diff(
    ref: str | None = None,
    staged: bool = False,
    pathspec: str | None = None,
    output_profile: str = "normal",
) -> dict[str, Any]:
    """Return compact structured diff summary with risk hints."""
    _require_git_repo()
    profile = _validate_output_profile(output_profile)
    args = ["diff"]
    if staged:
        args.append("--staged")
    if ref:
        args.append(ref)
    if pathspec:
        _resolve_repo_path(pathspec)
        args.extend(["--", pathspec])

    numstat = _git(*args, "--numstat").stdout
    patch = _git(*args, "--unified=0").stdout

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
    return result


@mcp.tool()
def json_query(
    path: str,
    query: str = "",
    file_type: str | None = None,
    output_profile: str = "normal",
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
def token_budget_guard(
    max_output_chars: int | None = None,
    default_output_profile: str | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    """Set or read global output budget/profile defaults."""
    if reset:
        payload = {
            "max_output_chars": MAX_OUTPUT_CHARS,
            "default_output_profile": "normal",
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
def failure_memory_get(
    category: str | None = None,
    contains: str | None = None,
    max_entries: int = 100,
) -> dict[str, Any]:
    """Read recent failure records captured from tool runs."""
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
    return {"count": len(entries_out), "entries": entries_out}


@mcp.tool()
def failure_memory_suggest(
    error_text: str,
    max_suggestions: int = 5,
) -> dict[str, Any]:
    """Suggest remediation hints from similar historical failures."""
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
    return {"count": len(suggestions), "suggestions": suggestions}


@mcp.tool()
def edit_transaction_begin(label: str = "") -> dict[str, Any]:
    """Start a multi-step edit transaction."""
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
    return {"transaction_id": txn_id, "status": "open"}


@mcp.tool()
def edit_transaction_apply(
    transaction_id: str,
    changes: list[dict[str, Any]],
    create_dirs: bool = True,
) -> dict[str, Any]:
    """Apply one or more file-content changes into an open transaction."""
    _require_mutations()
    if not changes:
        raise ValueError("changes must not be empty")
    tx = _tx_load(transaction_id)
    if tx.get("status") != "open":
        raise ValueError("transaction is not open")

    applied: list[str] = []
    for change in changes:
        path = change.get("path")
        content = change.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("each change requires string path and content")
        file_path = _resolve_repo_path(path)
        rel = str(file_path.relative_to(REPO_PATH))

        if rel not in tx["backups"]:
            if file_path.exists() and file_path.is_file():
                tx["backups"][rel] = {"existed": True, "content": file_path.read_text(encoding="utf-8", errors="replace")}
            else:
                tx["backups"][rel] = {"existed": False, "content": ""}

        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        tx["changes"].append({"path": rel, "bytes": len(content.encode("utf-8"))})
        applied.append(rel)

    tx["updated_at"] = _now_iso()
    _tx_save(transaction_id, tx)
    return {"transaction_id": transaction_id, "applied": applied, "change_count": len(applied)}


@mcp.tool()
def edit_transaction_validate(transaction_id: str) -> dict[str, Any]:
    """Validate open transaction with lightweight checks."""
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
        "transaction_id": transaction_id,
        "status": tx.get("status"),
        "changed_paths": changed_paths,
        "python_files_checked": len(py_files[:200]),
        "compile_error_count": len(compile_errors),
        "compile_errors": compile_errors,
    }


@mcp.tool()
def edit_transaction_rollback(transaction_id: str) -> dict[str, Any]:
    """Rollback all files touched by a transaction."""
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
    return {"transaction_id": transaction_id, "status": "rolled_back", "restored": restored}


@mcp.tool()
def edit_transaction_commit(transaction_id: str, delete_metadata: bool = False) -> dict[str, Any]:
    """Commit transaction metadata (does not create a git commit)."""
    tx = _tx_load(transaction_id)
    tx["status"] = "committed"
    tx["updated_at"] = _now_iso()
    _tx_save(transaction_id, tx)
    if delete_metadata:
        _tx_path(transaction_id).unlink(missing_ok=True)
    return {"transaction_id": transaction_id, "status": "committed", "metadata_deleted": delete_metadata}


@mcp.tool()
def patch_minimize(
    ref: str | None = None,
    staged: bool = False,
    pathspec: str | None = None,
) -> dict[str, Any]:
    """Generate a minimal-context patch (`-U0`) for current repo diff."""
    _require_git_repo()
    args = ["diff", "--unified=0"]
    if staged:
        args.append("--staged")
    if ref:
        args.append(ref)
    if pathspec:
        _resolve_repo_path(pathspec)
        args.extend(["--", pathspec])
    patch = _git(*args).stdout
    return {
        "bytes": len(patch.encode("utf-8")),
        "line_count": len(patch.splitlines()),
        "patch": _trim_text(patch),
    }


@mcp.tool()
def impact_tests(
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    max_tests: int = 300,
    output_profile: str | None = None,
) -> dict[str, Any]:
    """Select impacted tests using changed files and dependency edges."""
    _require_git_repo()
    if max_tests < 1:
        raise ValueError("max_tests must be >= 1")
    profile = _default_output_profile(output_profile)
    diff_out = _git("diff", "--name-only", f"{base_ref}...{head_ref}").stdout.strip()
    changed = [line.strip() for line in diff_out.splitlines() if line.strip()]

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
    }
    if profile == "compact":
        return {"test_count": len(deduped), "tests": deduped}
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
    facts_path = Path(".build/memory/workspace_facts.json")
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
def language_parsers(
    path: str = ".",
    language: str = "auto",
    recursive: bool = True,
    max_files: int = 1000,
    output_profile: str | None = None,
) -> dict[str, Any]:
    """Extract lightweight symbols/imports for JS/TS/Go/Rust files."""
    if max_files < 1:
        raise ValueError("max_files must be >= 1")
    profile = _default_output_profile(output_profile)
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    lang_map = {
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
    }
    allowed = {"auto", "javascript", "typescript", "go", "rust"}
    if language not in allowed:
        raise ValueError("language must be one of: auto, javascript, typescript, go, rust")

    files: list[dict[str, Any]] = []
    for candidate in _iter_candidate_files(root, recursive=recursive):
        suffix = candidate.suffix.lower()
        detected = lang_map.get(suffix)
        if not detected:
            continue
        if language != "auto" and detected != language:
            continue
        rel = str(candidate.relative_to(REPO_PATH)).replace("\\", "/")
        try:
            parsed = _language_parse_file(candidate, detected)
        except OSError:
            continue
        files.append(
            {
                "path": rel,
                "language": detected,
                "symbol_count": len(parsed["symbols"]),
                "import_count": len(parsed["imports"]),
                "symbols": parsed["symbols"],
                "imports": parsed["imports"],
            }
        )
        if len(files) >= max_files:
            break

    if profile == "compact":
        return {
            "file_count": len(files),
            "files": [
                {
                    "path": f["path"],
                    "language": f["language"],
                    "symbol_count": f["symbol_count"],
                    "import_count": f["import_count"],
                }
                for f in files
            ],
        }
    return {"file_count": len(files), "files": files}


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
) -> dict[str, Any]:
    """Parse/search syntax trees via Tree-sitter when available."""
    if mode not in {"status", "parse", "search"}:
        raise ValueError("mode must be one of: status, parse, search")
    if max_files < 1 or max_nodes < 1:
        raise ValueError("max_files and max_nodes must be >= 1")
    profile = _default_output_profile(output_profile)
    available = _tree_sitter_available()
    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    if mode == "status":
        return {"available": available, "engine": "tree_sitter_languages"}

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
            # Fallback to Python AST if tree-sitter runtime is unavailable.
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
        file_result: dict[str, Any] = {"path": rel, "language": lang, "node_count": len(nodes)}
        if profile != "compact":
            file_result["nodes"] = nodes
        files.append(file_result)
        if matched_files >= max_files or total_nodes >= max_nodes:
            break

    return {
        "available": available,
        "mode": mode,
        "path": str(root.relative_to(REPO_PATH)),
        "file_count": matched_files,
        "node_count": total_nodes,
        "files": files,
    }


@mcp.tool()
def repo_index_daemon(
    mode: str = "refresh",
    path: str = ".",
    query: str = "",
    recursive: bool = True,
    include_hashes: bool = False,
    max_files: int = 5000,
    output_profile: str | None = None,
) -> dict[str, Any]:
    """Build/read/query a persistent repository index keyed by file metadata."""
    if mode not in {"refresh", "read", "query"}:
        raise ValueError("mode must be one of: refresh, read, query")
    if max_files < 1:
        raise ValueError("max_files must be >= 1")
    profile = _default_output_profile(output_profile)
    index_path = _resolve_repo_path(str(REPO_INDEX_FILE))

    if mode in {"read", "query"}:
        if not index_path.is_file():
            raise FileNotFoundError(str(REPO_INDEX_FILE))
        index_payload = json.loads(index_path.read_text(encoding="utf-8"))
        if mode == "query":
            value = _query_value(index_payload, query) if query.strip() else index_payload
            return {
                "mode": mode,
                "query": query,
                "value_json": _trim_text(json.dumps(value, indent=2, ensure_ascii=True)),
                "value": value if profile != "compact" else None,
            }
        if profile == "compact":
            return {
                "mode": mode,
                "generated_at": index_payload.get("generated_at"),
                "file_count": index_payload.get("file_count", 0),
                "symbol_count": index_payload.get("symbol_count", 0),
                "dependency_edge_count": index_payload.get("dependency_edge_count", 0),
            }
        return index_payload

    root = _resolve_repo_path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    files_meta: list[dict[str, Any]] = []
    for candidate in _iter_candidate_files(root, recursive=recursive):
        rel = str(candidate.relative_to(REPO_PATH)).replace("\\", "/")
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
        "generated_at": _now_iso(),
        "path": str(root.relative_to(REPO_PATH)),
        "file_count": len(files_meta),
        "symbol_count": len(symbols),
        "dependency_edge_count": int(dep.get("edge_count", 0)),
        "call_edge_count": int(call.get("edge_count", 0)),
        "files": files_meta if profile != "compact" else files_meta[:300],
        "symbols": symbols if profile == "verbose" else [],
        "dependencies": dep.get("edges", []) if profile == "verbose" else [],
        "call_edges": call.get("edges", []) if profile == "verbose" else [],
    }
    if _is_git_repo():
        payload["git_head"] = _git("rev-parse", "HEAD").stdout.strip()
        payload["git_branch"] = _git("branch", "--show-current").stdout.strip()

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "mode": mode,
        "index_path": str(REPO_INDEX_FILE),
        "generated_at": payload["generated_at"],
        "file_count": payload["file_count"],
        "symbol_count": payload["symbol_count"],
        "dependency_edge_count": payload["dependency_edge_count"],
        "call_edge_count": payload["call_edge_count"],
    }


@mcp.tool()
def self_check_pipeline(
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    run_targeted_tests: bool = True,
    run_impact_tests: bool = True,
    run_doc_check: bool = True,
    run_api_check: bool = True,
    run_risk_check: bool = True,
    run_compile_check: bool = True,
    snapshot_path: str = str(API_SNAPSHOT_FILE),
    max_compile_files: int = 300,
) -> dict[str, Any]:
    """Run a single-call quality gate pipeline and return structured results."""
    _require_git_repo()
    if max_compile_files < 1:
        raise ValueError("max_compile_files must be >= 1")

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
            "errors": compile_errors,
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
        impacts = impact_tests(base_ref=base_ref, head_ref=head_ref, output_profile="compact")
        result["checks"]["impact_tests"] = impacts

    if run_targeted_tests:
        tests = test_targeted(base_ref=base_ref, head_ref=head_ref)
        result["checks"]["targeted_tests"] = {
            "ok": tests.get("ok", False),
            "exit_code": tests.get("exit_code"),
            "targeted_tests": tests.get("targeted_tests", []),
            "timeout": tests.get("timeout", False),
            "stderr": tests.get("stderr", ""),
        }
        if not tests.get("ok", False):
            result["ok"] = False

    result["finished_at"] = _now_iso()
    return result


@mcp.tool()
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


@mcp.tool()
def memory_get(
    namespace: str | None = None,
    key: str | None = None,
    include_expired: bool = False,
    max_entries: int = 200,
) -> dict[str, Any]:
    """Read context memory entries with namespace/key filters."""
    if max_entries < 1:
        raise ValueError("max_entries must be >= 1")
    payload = _memory_load()
    now = datetime.now(timezone.utc)
    entries_out: list[dict[str, Any]] = []

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

    return {
        "path": str(MEMORY_FILE),
        "count": len(entries_out),
        "entries": entries_out,
    }


@mcp.tool()
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
    now = datetime.now(timezone.utc)

    stale: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
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

    if drop_expired:
        _require_mutations()
        payload["entries"] = kept
        _memory_save(payload)

    return {
        "path": str(MEMORY_FILE),
        "total_checked": len(entries),
        "stale_count": len(stale),
        "dropped_expired": dropped,
        "stale_entries": stale,
    }


async def healthz(_request):
    return JSONResponse(
        {
            "ok": True,
            "repo_path": str(REPO_PATH),
            "is_git_repo": _is_git_repo(),
            "allow_mutations": ALLOW_MUTATIONS,
            "transport": MCP_TRANSPORT,
        }
    )


async def root(_request):
    return PlainTextResponse("git-repo-manager MCP server")


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield


starlette_app = Starlette(
    routes=[
        Route("/", root, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        Mount("/", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)

app = CORSMiddleware(
    starlette_app,
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
