#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

# Collect deterministic diagnostics for VS Code Dev Containers attach failures where
# VS Code Server exits with code 137. Exit 137 usually means SIGKILL; on developer
# workstations the most common cause is a container or host OOM kill.
#
# Usage:
#   scripts/devcontainer_exit137_diagnostics.sh [container-name-or-id]
#
# Run from the host, or from inside the devcontainer when /var/run/docker.sock is
# mounted and the docker CLI can inspect the container.

set -u

CONTAINER_REF="${1:-${DEVCONTAINER_NAME:-${HOSTNAME:-}}}"
TAIL_LINES="${DEVCONTAINER_EXIT137_TAIL_LINES:-200}"

section() {
  printf '\n## %s\n' "$1"
}

run() {
  printf '+ %s\n' "$*"
  "$@" 2>&1 || printf '[exit %s]\n' "$?"
}

read_cgroup_file() {
  local path="$1"
  printf '### %s\n' "$path"
  if [[ -r "$path" ]]; then
    cat "$path" 2>&1 || true
  else
    printf 'not readable\n'
  fi
}

section "metadata"
printf 'timestamp_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'hostname=%s\n' "${HOSTNAME:-unknown}"
printf 'container_ref=%s\n' "${CONTAINER_REF:-unset}"
printf 'tail_lines=%s\n' "$TAIL_LINES"

section "docker inspect OOM and exit state"
if command -v docker >/dev/null 2>&1 && [[ -n "${CONTAINER_REF}" ]]; then
  run docker inspect \
    --format 'name={{.Name}} id={{.Id}} status={{.State.Status}} running={{.State.Running}} oom_killed={{.State.OOMKilled}} exit_code={{.State.ExitCode}} error={{.State.Error}} started_at={{.State.StartedAt}} finished_at={{.State.FinishedAt}} pid={{.State.Pid}} memory={{.HostConfig.Memory}} memory_swap={{.HostConfig.MemorySwap}}' \
    "$CONTAINER_REF"
else
  printf 'docker CLI unavailable or no container reference supplied; run this script from the host with the devcontainer name/id.\n'
fi

section "cgroup memory current peak events"
if [[ -r /sys/fs/cgroup/cgroup.controllers || -r /sys/fs/cgroup/memory.current ]]; then
  read_cgroup_file /sys/fs/cgroup/memory.current
  read_cgroup_file /sys/fs/cgroup/memory.peak
  read_cgroup_file /sys/fs/cgroup/memory.max
  read_cgroup_file /sys/fs/cgroup/memory.swap.current
  read_cgroup_file /sys/fs/cgroup/memory.swap.max
  read_cgroup_file /sys/fs/cgroup/memory.events
  read_cgroup_file /sys/fs/cgroup/memory.events.local
else
  read_cgroup_file /sys/fs/cgroup/memory/memory.usage_in_bytes
  read_cgroup_file /sys/fs/cgroup/memory/memory.max_usage_in_bytes
  read_cgroup_file /sys/fs/cgroup/memory/memory.limit_in_bytes
  read_cgroup_file /sys/fs/cgroup/memory/memory.failcnt
  read_cgroup_file /sys/fs/cgroup/memory/memory.oom_control
  read_cgroup_file /sys/fs/cgroup/memory/memory.memsw.usage_in_bytes
  read_cgroup_file /sys/fs/cgroup/memory/memory.memsw.limit_in_bytes
fi

section "process list"
if command -v ps >/dev/null 2>&1; then
  run ps -eo pid,ppid,user,stat,%mem,%cpu,rss,vsz,comm,args --sort=-rss
else
  printf 'ps unavailable\n'
fi

section "memory and swap"
if command -v free >/dev/null 2>&1; then
  run free -h
else
  printf 'free unavailable\n'
fi
if command -v swapon >/dev/null 2>&1; then
  run swapon --show
else
  printf 'swapon unavailable\n'
fi
if [[ -r /proc/meminfo ]]; then
  run grep -E '^(MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree|CommitLimit|Committed_AS):' /proc/meminfo
fi

section "kernel OOM messages"
if command -v dmesg >/dev/null 2>&1; then
  dmesg -T 2>&1 | grep -Ei 'out of memory|oom-kill|oom_reaper|killed process|memory cgroup out of memory|exit code 137|vscode-server|code server|node' | tail -n "$TAIL_LINES" || true
else
  printf 'dmesg unavailable\n'
fi
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --no-pager -n 2000 2>&1 | grep -Ei 'out of memory|oom-kill|oom_reaper|killed process|memory cgroup out of memory|exit code 137|vscode-server|code server|node' | tail -n "$TAIL_LINES" || true
else
  printf 'journalctl unavailable\n'
fi

section "docker OOM messages"
if command -v docker >/dev/null 2>&1; then
  if [[ -n "${CONTAINER_REF}" ]]; then
    run docker logs --tail "$TAIL_LINES" "$CONTAINER_REF"
  fi
  run docker events --since 24h --until 0s --filter event=oom --filter event=die --filter event=kill
else
  printf 'docker CLI unavailable\n'
fi
