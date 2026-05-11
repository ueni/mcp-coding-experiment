#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

set -u

container_ref="${1:-${DEVCONTAINER_NAME:-}}"
if [[ -z "${container_ref}" ]] && [[ -f /.dockerenv ]]; then
  container_ref="$(hostname 2>/dev/null || true)"
fi

section() {
  printf '\n## %s\n' "$1"
}

run_optional() {
  local label="$1"
  shift
  section "${label}"
  "$@" 2>&1 || printf 'unavailable: %s\n' "$*"
}

print_file_if_exists() {
  local path="$1"
  if [[ -e "${path}" ]]; then
    printf '\n### %s\n' "${path}"
    cat "${path}" 2>&1 || true
  fi
}

printf '# Devcontainer diagnostics\n'
printf 'timestamp: %s\n' "$(date -Is 2>/dev/null || date)"
printf 'hostname: %s\n' "$(hostname 2>/dev/null || true)"
printf 'user: %s\n' "$(id 2>/dev/null || true)"
printf 'container_ref: %s\n' "${container_ref:-unknown}"

run_optional "OS" sh -c 'cat /etc/os-release 2>/dev/null || uname -a'
run_optional "Memory and swap" sh -c 'free -h 2>/dev/null || true; printf "\n/proc/meminfo excerpt:\n"; grep -E "^(MemTotal|MemAvailable|SwapTotal|SwapFree|Committed_AS|CommitLimit):" /proc/meminfo 2>/dev/null || true; printf "\n/proc/swaps:\n"; cat /proc/swaps 2>/dev/null || true'
run_optional "Process snapshot" sh -c 'ps -eo pid,ppid,user,stat,pcpu,pmem,rss,vsz,comm,args --sort=-rss 2>/dev/null | head -80'

section "Cgroup memory"
for base in /sys/fs/cgroup /sys/fs/cgroup/memory; do
  for name in memory.max memory.high memory.current memory.peak memory.events memory.swap.max memory.swap.current memory.stat memory.limit_in_bytes memory.usage_in_bytes memory.max_usage_in_bytes memory.oom_control; do
    print_file_if_exists "${base}/${name}"
  done
done

section "Container runtime evidence"
if command -v docker >/dev/null 2>&1 && [[ -n "${container_ref:-}" ]]; then
  docker inspect --format='State={{json .State}} HostConfig.Memory={{.HostConfig.Memory}} HostConfig.MemorySwap={{.HostConfig.MemorySwap}} HostConfig.OomKillDisable={{.HostConfig.OomKillDisable}} Name={{.Name}}' "${container_ref}" 2>&1 || true
  printf '\n### docker events recent oom/kill/die\n'
  timeout 5 docker events --since 30m --until "$(date -Is)" --filter "container=${container_ref}" --filter event=oom --filter event=kill --filter event=die 2>&1 || true
else
  printf 'docker CLI unavailable or container ref unknown; run this script on the host with the container name/id for docker inspect OOMKilled/ExitCode evidence.\n'
fi

section "Kernel/container OOM messages"
if command -v dmesg >/dev/null 2>&1; then
  dmesg -T 2>/dev/null | grep -Ei 'out of memory|oom-kill|oom killed|killed process|memory cgroup' | tail -80 || true
fi
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k --since '30 minutes ago' 2>/dev/null | grep -Ei 'out of memory|oom-kill|oom killed|killed process|memory cgroup|docker|containerd' | tail -80 || true
fi
