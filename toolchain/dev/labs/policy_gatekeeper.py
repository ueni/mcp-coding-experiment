#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Policy-as-Code Gatekeeper.

Evaluates repository state against configurable policy rules.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], check=check)


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    return data


def list_files() -> list[str]:
    cp = git("ls-files")
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()]


def changed_files(ref: str) -> tuple[list[str], str | None]:
    cp = git("diff", "--name-only", f"{ref}...HEAD", check=False)
    if cp.returncode != 0:
        cp = git("diff", "--name-only", ref, check=False)
    if cp.returncode != 0:
        return [], f"Unable to diff against reference '{ref}'"
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()], None


def _is_doc_path(path: str, doc_roots: list[str]) -> bool:
    for root in doc_roots:
        norm = root.strip()
        if not norm:
            continue
        if norm.endswith("/"):
            if path.startswith(norm):
                return True
        elif path == norm:
            return True
    return False


def _is_excluded_path(path: str, excluded_paths: list[str]) -> bool:
    for item in excluded_paths:
        norm = item.strip()
        if not norm:
            continue
        if norm.endswith("/"):
            if path.startswith(norm):
                return True
        elif path == norm:
            return True
    return False


def check_docs_index(index_path: str, doc_files: list[str]) -> list[str]:
    path = Path(index_path)
    if not path.exists():
        return [f"Missing docs index: {index_path}"]

    try:
        index_text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"Unable to read docs index '{index_path}': {exc}"]

    missing: list[str] = []
    for doc in sorted(doc_files):
        if doc == index_path:
            continue
        if doc not in index_text:
            missing.append(doc)

    if not missing:
        return []
    return [
        (
            f"Docs index '{index_path}' is missing entries for: "
            + ", ".join(missing[:20])
            + (" ..." if len(missing) > 20 else "")
        )
    ]


def check_forbidden_patterns(
    files: list[str], patterns: list[str], max_bytes: int, excluded_paths: list[str]
) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    compiled = [re.compile(p) for p in patterns]
    for rel in files:
        if _is_excluded_path(rel, excluded_paths):
            continue
        path = Path(rel)
        if not path.exists() or not path.is_file():
            continue
        if path.stat().st_size > max_bytes:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            for rx in compiled:
                if rx.search(line):
                    violations.append(
                        {
                            "file": rel,
                            "line": str(idx),
                            "pattern": rx.pattern,
                            "snippet": line.strip()[:200],
                        }
                    )
    return violations


def run_command_checks(commands: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for cmd in commands:
        proc = subprocess.run(cmd, shell=True, text=True, capture_output=True)
        results.append(
            {
                "command": cmd,
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run policy gatekeeper checks")
    parser.add_argument(
        "--config",
        default=".config/dev/labs/policy_gatekeeper.json",
        help="Path to policy config JSON",
    )
    parser.add_argument(
        "--changed-ref",
        default=None,
        help="Diff reference for changed-files rules",
    )
    parser.add_argument(
        "--report-path",
        default=".build/reports/POLICY_GATEKEEPER.md",
        help="Output markdown report",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    max_file_size = int(cfg.get("max_file_size_bytes", 1024 * 1024))
    forbidden_patterns = cfg.get("forbidden_patterns", [])
    forbidden_pattern_excluded_paths = cfg.get("forbidden_pattern_excluded_paths", [])
    required_paths = cfg.get("required_paths", [])
    required_when_paths_change = cfg.get("required_when_paths_change", {})
    commands = cfg.get("command_checks", [])
    docs_policy = cfg.get("docs_policy", {})
    default_target_branch = str(cfg.get("default_target_branch", "main"))

    if not isinstance(forbidden_patterns, list) or not all(
        isinstance(x, str) for x in forbidden_patterns
    ):
        raise ValueError("forbidden_patterns must be a list of strings")
    if not isinstance(required_paths, list) or not all(
        isinstance(x, str) for x in required_paths
    ):
        raise ValueError("required_paths must be a list of strings")
    if not isinstance(forbidden_pattern_excluded_paths, list) or not all(
        isinstance(x, str) for x in forbidden_pattern_excluded_paths
    ):
        raise ValueError("forbidden_pattern_excluded_paths must be a list of strings")
    if not isinstance(commands, list) or not all(isinstance(x, str) for x in commands):
        raise ValueError("command_checks must be a list of strings")
    if not isinstance(required_when_paths_change, dict):
        raise ValueError("required_when_paths_change must be an object")
    if not isinstance(docs_policy, dict):
        raise ValueError("docs_policy must be an object")

    changed_ref = args.changed_ref if args.changed_ref is not None else default_target_branch

    tracked = list_files()
    tracked_set = set(tracked)
    changed, changed_error = changed_files(changed_ref)

    violations: list[str] = []
    if changed_error:
        violations.append(changed_error)

    missing_required = [
        p for p in required_paths if p not in tracked_set and not Path(p).exists()
    ]
    if missing_required:
        violations.append(
            "Missing required paths: " + ", ".join(sorted(missing_required))
        )

    for rel, must_exist in required_when_paths_change.items():
        if not isinstance(rel, str) or not isinstance(must_exist, list):
            continue
        triggered = any(path.startswith(rel) for path in changed)
        if not triggered:
            continue
        missing = [
            p for p in must_exist if p not in tracked_set and not Path(p).exists()
        ]
        if missing:
            violations.append(f"Changes under '{rel}' require: {', '.join(missing)}")

    oversize = []
    for rel in tracked:
        path = Path(rel)
        if path.exists() and path.is_file() and path.stat().st_size > max_file_size:
            oversize.append((rel, path.stat().st_size))
    if oversize:
        details = ", ".join(f"{p} ({s} bytes)" for p, s in oversize[:20])
        violations.append(f"Files exceeding {max_file_size} bytes: {details}")

    pattern_hits = check_forbidden_patterns(
        tracked,
        forbidden_patterns,
        max_bytes=max_file_size,
        excluded_paths=forbidden_pattern_excluded_paths,
    )
    if pattern_hits:
        violations.append(f"Forbidden pattern hits: {len(pattern_hits)}")

    cmd_results = run_command_checks(commands)
    failed_cmds = [r for r in cmd_results if not r["ok"]]
    if failed_cmds:
        violations.append(f"Command checks failed: {len(failed_cmds)}")

    docs_violations: list[str] = []
    docs_enabled = bool(docs_policy.get("enabled", False))
    if docs_enabled:
        raw_roots = docs_policy.get("doc_roots", ["docs/", "README.md"])
        index_path = str(docs_policy.get("index_path", "docs/index.md"))
        require_docs_for_impl = bool(
            docs_policy.get("require_docs_for_impl_diff", True)
        )
        impl_path_prefixes = docs_policy.get("impl_path_prefixes", ["toolchain/"])
        docs_target_branch = str(
            docs_policy.get("target_branch", default_target_branch)
        ).strip()

        if not isinstance(raw_roots, list) or not all(
            isinstance(x, str) for x in raw_roots
        ):
            raise ValueError("docs_policy.doc_roots must be a list of strings")
        if not isinstance(impl_path_prefixes, list) or not all(
            isinstance(x, str) for x in impl_path_prefixes
        ):
            raise ValueError(
                "docs_policy.impl_path_prefixes must be a list of strings"
            )

        effective_target = docs_target_branch or default_target_branch
        if args.changed_ref is None and effective_target != changed_ref:
            changed_ref = effective_target
            changed, changed_error = changed_files(changed_ref)
            if changed_error:
                docs_violations.append(changed_error)

        doc_roots = [x.strip() for x in raw_roots if x.strip()]
        doc_files = [p for p in tracked if _is_doc_path(p, doc_roots)]
        docs_changed = [p for p in changed if _is_doc_path(p, doc_roots)]
        impl_changed = [
            p
            for p in changed
            if not _is_doc_path(p, doc_roots)
            and any(p.startswith(prefix) for prefix in impl_path_prefixes)
        ]

        docs_violations.extend(check_docs_index(index_path, doc_files))
        if require_docs_for_impl and impl_changed and not docs_changed:
            docs_violations.append(
                (
                    f"Implementation changes vs '{changed_ref}' require docs updates "
                    f"under {', '.join(doc_roots)}; none were changed."
                )
            )

    if docs_violations:
        violations.extend(docs_violations)

    passed = not violations

    lines = [
        "# Policy Gatekeeper Report",
        "",
        f"Result: `{'PASS' if passed else 'FAIL'}`",
        f"Changed reference: `{changed_ref}`",
        "",
        "## Summary",
        "",
        f"- Tracked files checked: `{len(tracked)}`",
        f"- Changed files detected: `{len(changed)}`",
        f"- Pattern hits: `{len(pattern_hits)}`",
        f"- Failed command checks: `{len(failed_cmds)}`",
        f"- Docs policy violations: `{len(docs_violations)}`",
        "",
    ]

    if violations:
        lines.extend(["## Violations", ""])
        for v in violations:
            lines.append(f"- {v}")
        lines.append("")

    if pattern_hits:
        lines.extend(["## Pattern Hits", ""])
        for hit in pattern_hits[:100]:
            lines.append(
                f"- `{hit['file']}:{hit['line']}` matched `{hit['pattern']}` -> `{hit['snippet']}`"
            )
        lines.append("")

    lines.extend(["## Command Checks", ""])
    for item in cmd_results:
        status = "PASS" if item["ok"] else "FAIL"
        lines.append(f"- `{item['command']}` -> {status} (exit={item['exit_code']})")

    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {args.report_path}")
    return 0 if passed else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
