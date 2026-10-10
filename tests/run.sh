#!/usr/bin/env bash
# Run the test suites with the project interpreter, isolated from the ROS pytest plugins on zgx.
# Usage: tests/run.sh            -> unit suites (pipeline + server)
#        tests/run.sh e2e        -> browser end-to-end suite too (needs network for basemap tiles)
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
unset PYTHONPATH
if [[ "${1:-}" == "e2e" ]]; then
  exec .venv/bin/python -m pytest tests -q "${@:2}"
fi
exec .venv/bin/python -m pytest tests/pipeline_tests tests/server_tests -q "$@"
