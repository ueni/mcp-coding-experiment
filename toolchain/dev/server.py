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
) -> list[str]:
    """Find files and/or directories under a repository-relative path."""
    if max_entries < 1:
        raise ValueError("max_entries must be >= 1")
    if max_depth is not None and max_depth < 0:
        raise ValueError("max_depth must be >= 0")
    if file_type not in {"any", "file", "dir"}:
        raise ValueError("file_type must be one of: any, file, dir")

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
) -> list[dict[str, Any]]:
    """Search repository files for a regex pattern and return matches.

    Returns a list of objects: { path, line, column, match, lineText }.
    Paths are repository-relative; line/column are 1-based.
    """
    if max_matches < 1:
        raise ValueError("max_matches must be >= 1")
    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be >= 1")

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
                        results.append(res)
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
) -> dict[str, Any]:
    """Read a focused line range with optional surrounding context."""
    if start_line < 1 or end_line < 1:
        raise ValueError("start_line and end_line must be >= 1")
    if end_line < start_line:
        raise ValueError("end_line must be >= start_line")
    if context_before < 0 or context_after < 0:
        raise ValueError("context_before/context_after must be >= 0")

    file_path = _resolve_repo_path(path)
    if not file_path.is_file():
        raise FileNotFoundError(path)

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


@mcp.tool()
def symbol_index(
    path: str = ".",
    include_private: bool = False,
    recursive: bool = True,
    max_symbols: int = 5000,
    encoding: str = "utf-8",
) -> list[dict[str, Any]]:
    """Index Python symbols (classes/functions) for focused navigation."""
    if max_symbols < 1:
        raise ValueError("max_symbols must be >= 1")

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
            symbols.append(symbol)
    return symbols


@mcp.tool()
def read_symbol(
    name: str,
    path: str = ".",
    occurrence: int = 1,
    include_private: bool = True,
    recursive: bool = True,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Read source for a named Python symbol."""
    if occurrence < 1:
        raise ValueError("occurrence must be >= 1")

    matches: list[dict[str, Any]] = []
    for symbol in symbol_index(
        path=path,
        include_private=include_private,
        recursive=recursive,
        max_symbols=20000,
        encoding=encoding,
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

    return {
        "path": target["path"],
        "name": target["name"],
        "kind": target["kind"],
        "occurrence": occurrence,
        "line_start": start,
        "line_end": end,
        "content": content,
    }


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
    max_output_chars: int = MAX_OUTPUT_CHARS,
) -> dict[str, Any]:
    """Run a whitelisted command without shell interpolation."""
    if not command:
        raise ValueError("command must not be empty")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    if max_output_chars < 1:
        raise ValueError("max_output_chars must be >= 1")

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
        return {
            "ok": False,
            "exit_code": None,
            "command": command,
            "cwd": str(workdir.relative_to(REPO_PATH)),
            "stdout": _trim_text((exc.stdout or "") if isinstance(exc.stdout, str) else ""),
            "stderr": _trim_text((exc.stderr or "") if isinstance(exc.stderr, str) else ""),
            "timeout": True,
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "exit_code": None,
            "command": command,
            "cwd": str(workdir.relative_to(REPO_PATH)),
            "stdout": "",
            "stderr": str(exc),
            "timeout": False,
        }
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "command": command,
        "cwd": str(workdir.relative_to(REPO_PATH)),
        "stdout": _trim_text(proc.stdout, max_chars=max_output_chars),
        "stderr": _trim_text(proc.stderr, max_chars=max_output_chars),
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
        return {
            "base_ref": base_ref,
            "head_ref": head_ref,
            "changed_files": changed,
            "targeted_tests": tests,
            "command": full_cmd,
            "ok": False,
            "exit_code": None,
            "timeout": True,
            "stdout": _trim_text((exc.stdout or "") if isinstance(exc.stdout, str) else ""),
            "stderr": _trim_text((exc.stderr or "") if isinstance(exc.stderr, str) else ""),
        }
    except FileNotFoundError as exc:
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

    return {
        "base_ref": base_ref,
        "head_ref": head_ref,
        "changed_files": changed,
        "targeted_tests": tests,
        "command": full_cmd,
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": _trim_text(proc.stdout),
        "stderr": _trim_text(proc.stderr),
    }


@mcp.tool()
def summarize_diff(
    ref: str | None = None,
    staged: bool = False,
    pathspec: str | None = None,
) -> dict[str, Any]:
    """Return compact structured diff summary with risk hints."""
    _require_git_repo()
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

    return {
        "file_count": len(files),
        "total_added": total_added,
        "total_deleted": total_deleted,
        "files": files,
        "risk_flags": {
            "risky_files": risky_files,
            "todo_like_additions": todo_hits,
        },
    }


@mcp.tool()
def json_query(
    path: str,
    query: str = "",
    file_type: str | None = None,
) -> dict[str, Any]:
    """Query JSON/TOML/YAML content with a dot/index path."""
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
    return {
        "path": str(file_path.relative_to(REPO_PATH)),
        "query": query,
        "file_type": fmt,
        "value": value,
        "value_json": _trim_text(encoded),
    }


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
