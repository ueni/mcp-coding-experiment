#!/usr/bin/env bash
set -euo pipefail

umask 027

ollama serve >/tmp/ollama.log 2>&1 &

ready=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if [[ "${ready}" -ne 1 ]]; then
  echo "ollama failed to start; see /tmp/ollama.log" >&2
  exit 1
fi

exec python /app/server.py
