import contextlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route


REPO_PATH = Path(os.getenv("REPO_PATH", "/repo")).resolve()
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
ALLOW_MUTATIONS = os.getenv("ALLOW_MUTATIONS", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MAX_READ_BYTES = int(os.getenv("MAX_READ_BYTES", "262144"))
MAX_OUTPUT_CHARS = int(os.getenv("MAX_OUTPUT_CHARS", "200000"))
ALLOW_ORIGINS = [x.strip() for x in os.getenv("ALLOW_ORIGINS", "*").split(",") if x.strip()]

mcp = FastMCP(
    "git-repo-manager",
    instructions=(
        "Manage exactly one mounted Git repository and its files. "
        "All paths are relative to the repository root."
    ),
    stateless_http=True,
    json_response=True,
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
        msg = stderr or stdout or f"git {' '.join(args)} failed with exit code {result.returncode}"
        raise RuntimeError(msg)
    return result


def _normalize_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    for p in paths:
        resolved = _resolve_repo_path(p)
        normalized.append(str(resolved.relative_to(REPO_PATH)))
    return normalized


@mcp.tool()
def repo_info() -> dict[str, Any]:
    """Return repository state and server settings."""
    _ensure_repo_path_exists()

    info: dict[str, Any] = {
        "repo_path": str(REPO_PATH),
        "repo_exists": REPO_PATH.exists(),
        "is_git_repo": _is_git_repo(),
        "allow_mutations": ALLOW_MUTATIONS,
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
def read_file(path: str, encoding: str = "utf-8", max_bytes: int = MAX_READ_BYTES) -> str:
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

    if create_dirs:
        file_path.parent.mkdir(parents=True, exist_ok=True)

    file_path.write_text(content, encoding=encoding)
    return {
        "path": str(file_path.relative_to(REPO_PATH)),
        "bytes_written": len(content.encode(encoding)),
        "existed_before": file_path.exists(),
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
def git_pull(remote: str = "origin", branch: str | None = None, rebase: bool = False) -> str:
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


async def healthz(_request):
    return JSONResponse(
        {
            "ok": True,
            "repo_path": str(REPO_PATH),
            "is_git_repo": _is_git_repo(),
            "allow_mutations": ALLOW_MUTATIONS,
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
