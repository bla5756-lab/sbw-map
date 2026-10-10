#!/usr/bin/env bash
# Publish gate: compile, unit tests, then the browser end-to-end suite. Stops at the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH= PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
.venv/bin/python -m compileall -q src tests
node --check src/web/app.js
.venv/bin/python -m src.pipeline.build_site >/dev/null
.venv/bin/python -m pytest tests/pipeline_tests tests/server_tests
.venv/bin/python -m pytest tests/web_tests/test_e2e.py
