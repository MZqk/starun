#!/bin/bash
# Deep Sky Advisor Analyzer Launcher
# Dependencies must be preinstalled in the selected Python runtime.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Starun exposes a fixed runtime through the workspace python wrapper.
if [[ "${STARUN_SKILL_SANDBOX:-}" == "1" ]]; then
    exec python "$SCRIPT_DIR/analyze_file.py" "$@"
fi

exec env \
    -u VIRTUAL_ENV \
    -u PYTHONHOME \
    -u PYTHONPATH \
    -u __PYVENV_LAUNCHER__ \
    python3 "$SCRIPT_DIR/analyze_file.py" "$@"
