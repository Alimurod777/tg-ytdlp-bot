#!/bin/bash
# Alwaysdata: foreground processes; no Docker or root access required.
set -euo pipefail

task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$task_root"
# Include user-installed tools when Alwaysdata starts a non-interactive service.
export PATH="$task_root/venv/bin:$HOME/bin:$HOME/.local/bin:$PATH"
task_python="$task_root/venv/bin/python"
if [[ ! -x "$task_python" ]]; then
    echo "Create venv and install requirements.txt first; see ALWAYSDATA_UZ.md." >&2
    exit 1
fi

case "${1:-bot}" in
    bot)
        # Also starts the PO token provider when it is enabled and installed.
        exec "$task_python" -u scripts/pot_provider.py bot
        ;;
    dashboard)
        # Alwaysdata assigns IP and PORT to each User program website.
        exec "$task_python" -m uvicorn web.dashboard_app:app \
            --host "${IP:-::}" --port "${PORT:-5555}"
        ;;
    pot-install)
        exec "$task_python" -u scripts/pot_provider.py install
        ;;
    pot)
        exec "$task_python" -u scripts/pot_provider.py provider
        ;;
    check)
        "$task_python" -c 'from CONFIG.config import Config; print("Configuration loaded successfully")'
        for task_binary in ffmpeg ffprobe mediainfo node; do
            if ! command -v "$task_binary" >/dev/null 2>&1; then
                echo "Missing executable on PATH: $task_binary" >&2
                exit 1
            fi
        done
        "$task_python" scripts/pot_provider.py status
        ;;
    *)
        echo "Usage: bash scripts/start_alwaysdata.sh [bot|dashboard|check|pot-install|pot]" >&2
        exit 2
        ;;
esac
