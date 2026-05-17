#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Refresh and validate hash-pinned Python dependency locks.

The locks are pip-compatible requirements files intended for opt-in Docker builds
with ``pip install --require-hashes``.  The JSON manifest carries compact digests
so CI/runtime checks can detect stale locks without dumping package lists.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable

LOCK_SCHEMA = "codebase_tooling_mcp.hashed_requirements.v1"
MANIFEST_SCHEMA = "codebase_tooling_mcp.dependency_locks.v1"


@dataclasses.dataclass(frozen=True)
class LockSection:
    name: str
    input_path: Path
    lock_path: Path
    description: str
    optional: bool = False


SECTIONS: tuple[LockSection, ...] = (
    LockSection(
        name="runtime",
        input_path=Path("source/requirements.txt"),
        lock_path=Path("source/requirements.lock"),
        description="Default MCP runtime dependencies installed in the image.",
    ),
    LockSection(
        name="embedding",
        input_path=Path("source/requirements-embedding.txt"),
        lock_path=Path("source/requirements-embedding.lock"),
        description="Optional sentence-transformers embedding backend dependencies.",
        optional=True,
    ),
    LockSection(
        name="coding-tools",
        input_path=Path("source/requirements-coding-tools.txt"),
        lock_path=Path("source/requirements-coding-tools.lock"),
        description="Pinned developer tools installed into the coding virtualenv.",
    ),
)
MANIFEST_PATH = Path("source/dependency-locks.json")

_PACKAGE_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")
_HASH_RE = re.compile(r"--hash=sha256:([0-9a-fA-F]{64})")


class LockError(RuntimeError):
    """Raised when a lock cannot be refreshed or validated."""


def _project_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _selected_sections(names: Iterable[str] | None) -> list[LockSection]:
    if not names:
        return list(SECTIONS)
    wanted = set(names)
    by_name = {section.name: section for section in SECTIONS}
    missing = sorted(wanted.difference(by_name))
    if missing:
        raise LockError(f"unknown section(s): {', '.join(missing)}")
    return [section for section in SECTIONS if section.name in wanted]


def _hash_from_report_item(item: dict[str, Any]) -> str:
    archive_info = (
        item.get("download_info", {})
        .get("archive_info", {})
    )
    hashes = archive_info.get("hashes", {}) if isinstance(archive_info, dict) else {}
    sha256 = hashes.get("sha256") if isinstance(hashes, dict) else None
    if isinstance(sha256, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        return sha256.lower()
    legacy_hash = archive_info.get("hash") if isinstance(archive_info, dict) else None
    if isinstance(legacy_hash, str) and legacy_hash.startswith("sha256="):
        digest = legacy_hash.split("=", 1)[1]
        if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return digest.lower()
    name = item.get("metadata", {}).get("name", "<unknown>")
    raise LockError(f"pip report item for {name!r} did not include a sha256 archive hash")


def _lock_rows_from_report(report: dict[str, Any]) -> list[tuple[str, str, str]]:
    rows: dict[str, tuple[str, str, str]] = {}
    for item in report.get("install", []):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise LockError("pip report item is missing metadata")
        raw_name = metadata.get("name")
        version = metadata.get("version")
        if not isinstance(raw_name, str) or not isinstance(version, str):
            raise LockError("pip report item metadata must include name and version")
        canonical = _canonical_name(raw_name)
        digest = _hash_from_report_item(item)
        row = (canonical, version, digest)
        existing = rows.get(canonical)
        if existing and existing != row:
            raise LockError(f"resolver produced duplicate package rows for {canonical}")
        rows[canonical] = row
    if not rows:
        raise LockError("pip report did not contain any install items")
    return [rows[name] for name in sorted(rows)]


def _run_pip_resolve(
    *,
    project_root: Path,
    section: LockSection,
    python_executable: str,
    extra_pip_args: list[str],
) -> tuple[list[tuple[str, str, str]], dict[str, Any]]:
    input_file = project_root / section.input_path
    if not input_file.is_file():
        raise LockError(f"missing input requirements file: {section.input_path}")
    with tempfile.TemporaryDirectory(prefix="mcp-dependency-lock-") as tmp:
        report_path = Path(tmp) / f"{section.name}.pip-report.json"
        cmd = [
            python_executable,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--ignore-installed",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "--report",
            str(report_path),
            *extra_pip_args,
            "-r",
            str(input_file),
        ]
        proc = subprocess.run(
            cmd,
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise LockError(
                "pip resolve failed for "
                f"{section.name} (exit {proc.returncode})\n"
                f"command: {' '.join(cmd)}\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LockError("pip did not write the requested --report file") from exc
    return _lock_rows_from_report(report), report


def _format_lock_file(
    *,
    section: LockSection,
    rows: list[tuple[str, str, str]],
    input_digest: str,
    target: dict[str, str],
    generated_at: str,
) -> str:
    header = [
        "# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt",
        "#",
        "# SPDX-License-Identifier: MIT",
        "#",
        "# This file is generated by scripts/dependency_lock.py; do not edit by hand.",
        f"# schema: {LOCK_SCHEMA}",
        f"# section: {section.name}",
        f"# input: {section.input_path.as_posix()}",
        f"# input-digest: {input_digest}",
        f"# generated-at: {generated_at}",
        f"# target-python: {target.get('python_version', '')}",
        f"# target-platform: {target.get('platform', '')}",
        "# install-command: python -m pip install --require-hashes --only-binary=:all: -r "
        f"{section.lock_path.name}",
        "",
    ]
    body: list[str] = []
    for name, version, digest in rows:
        body.append(f"{name}=={version} \\")
        body.append(f"    --hash=sha256:{digest}")
    return "\n".join([*header, *body, ""])


def _validate_lock_text(lock_text: str, section: LockSection) -> dict[str, Any]:
    logical_lines: list[str] = []
    current = ""
    for raw_line in lock_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1].strip() + " "
            continue
        logical_lines.append((current + stripped).strip())
        current = ""
    if current:
        raise LockError(f"{section.lock_path}: dangling line continuation")

    packages: list[str] = []
    hashes: list[str] = []
    seen: set[str] = set()
    for line in logical_lines:
        package_match = _PACKAGE_RE.match(line)
        if not package_match:
            raise LockError(
                f"{section.lock_path}: requirement must be exact 'name==version': {line!r}"
            )
        name = _canonical_name(package_match.group(1))
        if name in seen:
            raise LockError(f"{section.lock_path}: duplicate package in lock: {name}")
        seen.add(name)
        line_hashes = _HASH_RE.findall(line)
        if not line_hashes:
            raise LockError(f"{section.lock_path}: missing sha256 hash for {name}")
        packages.append(name)
        hashes.extend(h.lower() for h in line_hashes)
    if not packages:
        raise LockError(f"{section.lock_path}: lock contains no packages")
    digest_material = "\n".join(
        [section.name, *packages, *sorted(hashes)]
    ).encode("utf-8")
    return {
        "package_count": len(packages),
        "hash_count": len(hashes),
        "content_digest": _sha256_bytes(digest_material),
    }


def _target_metadata(report: dict[str, Any], python_executable: str) -> dict[str, str]:
    pip_version = str(report.get("pip_version", ""))
    environment = report.get("environment", {})
    if not isinstance(environment, dict):
        environment = {}
    return {
        "python_executable": python_executable,
        "python_version": str(
            environment.get("python_full_version")
            or environment.get("python_version")
            or platform.python_version()
        ),
        "implementation": str(
            environment.get("implementation_name")
            or platform.python_implementation().lower()
        ),
        "platform": str(environment.get("platform_platform") or platform.platform()),
        "machine": str(environment.get("platform_machine") or platform.machine()),
        "pip_version": pip_version,
    }


def _load_manifest(project_root: Path) -> dict[str, Any]:
    path = project_root / MANIFEST_PATH
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LockError(f"missing lock manifest: {MANIFEST_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise LockError(f"invalid JSON in {MANIFEST_PATH}: {exc}") from exc
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise LockError(f"{MANIFEST_PATH} has unexpected schema")
    if not isinstance(manifest.get("sections"), dict):
        raise LockError(f"{MANIFEST_PATH} must contain a sections object")
    return manifest


def refresh(
    *,
    project_root: Path,
    sections: list[LockSection],
    python_executable: str,
    extra_pip_args: list[str],
) -> dict[str, Any]:
    generated_at = _now_iso()
    manifest_sections: dict[str, dict[str, Any]] = {}
    target: dict[str, str] | None = None

    for section in sections:
        rows, report = _run_pip_resolve(
            project_root=project_root,
            section=section,
            python_executable=python_executable,
            extra_pip_args=extra_pip_args,
        )
        if target is None:
            target = _target_metadata(report, python_executable)
        input_digest = _sha256_file(project_root / section.input_path)
        lock_text = _format_lock_file(
            section=section,
            rows=rows,
            input_digest=input_digest,
            target=target,
            generated_at=generated_at,
        )
        validation = _validate_lock_text(lock_text, section)
        lock_file = project_root / section.lock_path
        lock_file.write_text(lock_text, encoding="utf-8")
        manifest_sections[section.name] = {
            "description": section.description,
            "optional": section.optional,
            "input": section.input_path.as_posix(),
            "lock": section.lock_path.as_posix(),
            "input_digest": input_digest,
            "lock_digest": _sha256_file(lock_file),
            "content_digest": validation["content_digest"],
            "package_count": validation["package_count"],
            "hash_count": validation["hash_count"],
        }

    if set(sections) != set(SECTIONS) and (project_root / MANIFEST_PATH).is_file():
        existing = _load_manifest(project_root)
        for name, payload in existing.get("sections", {}).items():
            if name not in manifest_sections:
                manifest_sections[name] = payload

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": generated_at,
        "generated_by": "scripts/dependency_lock.py",
        "target": target or {
            "python_executable": python_executable,
            "python_version": platform.python_version(),
            "implementation": platform.python_implementation().lower(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "pip_version": "",
        },
        "install": {
            "locked_build_arg": "MCP_USE_LOCKED_DEPS=true",
            "pip_mode": "--require-hashes --only-binary=:all:",
            "default_section": "runtime",
            "optional_embedding_section": "embedding",
            "coding_tools_section": "coding-tools",
        },
        "sections": dict(sorted(manifest_sections.items())),
    }
    manifest_path = project_root / MANIFEST_PATH
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return check(project_root=project_root, sections=sections)


def check(*, project_root: Path, sections: list[LockSection]) -> dict[str, Any]:
    manifest = _load_manifest(project_root)
    manifest_sections = manifest.get("sections", {})
    section_results: dict[str, dict[str, Any]] = {}
    ok = True

    for section in sections:
        errors: list[str] = []
        manifest_section = manifest_sections.get(section.name)
        if not isinstance(manifest_section, dict):
            errors.append("missing section in manifest")
            section_results[section.name] = {"ok": False, "errors": errors}
            ok = False
            continue

        input_file = project_root / section.input_path
        lock_file = project_root / section.lock_path
        input_digest = _sha256_file(input_file) if input_file.is_file() else "missing"
        lock_digest = _sha256_file(lock_file) if lock_file.is_file() else "missing"
        if manifest_section.get("input") != section.input_path.as_posix():
            errors.append("manifest input path mismatch")
        if manifest_section.get("lock") != section.lock_path.as_posix():
            errors.append("manifest lock path mismatch")
        if input_digest != manifest_section.get("input_digest"):
            errors.append("input requirements digest changed; refresh lock")
        if lock_digest != manifest_section.get("lock_digest"):
            errors.append("lock file digest changed; refresh manifest")

        validation: dict[str, Any] = {}
        if lock_file.is_file():
            try:
                lock_text = lock_file.read_text(encoding="utf-8")
                validation = _validate_lock_text(lock_text, section)
                if validation.get("content_digest") != manifest_section.get("content_digest"):
                    errors.append("lock content digest mismatch")
                for marker, expected in (
                    (f"# schema: {LOCK_SCHEMA}", "schema header"),
                    (f"# section: {section.name}", "section header"),
                    (f"# input: {section.input_path.as_posix()}", "input header"),
                    (f"# input-digest: {manifest_section.get('input_digest')}", "input digest header"),
                ):
                    if marker not in lock_text:
                        errors.append(f"missing {expected}")
            except LockError as exc:
                errors.append(str(exc))
        else:
            errors.append("lock file missing")

        section_ok = not errors
        ok = ok and section_ok
        section_results[section.name] = {
            "ok": section_ok,
            "input": section.input_path.as_posix(),
            "lock": section.lock_path.as_posix(),
            "input_digest": input_digest,
            "lock_digest": lock_digest,
            "package_count": validation.get("package_count", manifest_section.get("package_count", 0)),
            "hash_count": validation.get("hash_count", manifest_section.get("hash_count", 0)),
            "content_digest": validation.get("content_digest", manifest_section.get("content_digest", "")),
            "errors": errors,
        }

    return {
        "schema": MANIFEST_SCHEMA + ".check",
        "ok": ok,
        "manifest": MANIFEST_PATH.as_posix(),
        "manifest_digest": _sha256_file(project_root / MANIFEST_PATH),
        "generated_at": manifest.get("generated_at", ""),
        "target": manifest.get("target", {}),
        "sections": section_results,
    }


def _compact_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": status["ok"],
        "manifest": status["manifest"],
        "manifest_digest": status["manifest_digest"],
        "generated_at": status.get("generated_at", ""),
        "sections": {
            name: {
                "ok": payload.get("ok", False),
                "input_digest": payload.get("input_digest", ""),
                "lock_digest": payload.get("lock_digest", ""),
                "package_count": payload.get("package_count", 0),
                "hash_count": payload.get("hash_count", 0),
                "errors": payload.get("errors", []),
            }
            for name, payload in status.get("sections", {}).items()
        },
    }


def _print_result(payload: dict[str, Any], *, compact: bool) -> None:
    print(json.dumps(_compact_status(payload) if compact else payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("refresh", "check"))
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_project_root_from_script(),
        help="Repository root (default: parent of scripts/).",
    )
    parser.add_argument(
        "--section",
        action="append",
        choices=[section.name for section in SECTIONS],
        help="Limit to one section; may be repeated. Default: all sections.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used for pip resolution in refresh mode.",
    )
    parser.add_argument(
        "--pip-arg",
        action="append",
        default=[],
        help="Additional argument passed to pip before -r in refresh mode.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact status JSON.",
    )
    args = parser.parse_args(argv)

    project_root = args.project_root.resolve()
    sections = _selected_sections(args.section)
    try:
        if args.command == "refresh":
            payload = refresh(
                project_root=project_root,
                sections=sections,
                python_executable=args.python,
                extra_pip_args=list(args.pip_arg),
            )
        else:
            payload = check(project_root=project_root, sections=sections)
    except LockError as exc:
        print(f"dependency-lock {args.command} failed: {exc}", file=sys.stderr)
        return 1
    _print_result(payload, compact=args.compact)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
