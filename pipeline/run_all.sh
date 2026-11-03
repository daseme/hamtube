#!/usr/bin/env bash
# Run every stage in order. Extra arguments go to 02_collect.py (e.g. --sources reddit,rtlsdr,hackaday).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
$PY pipeline/01_seed.py
$PY pipeline/02_collect.py "$@"
$PY pipeline/03_extract.py
$PY pipeline/04_classify.py
$PY pipeline/05_qualify.py
$PY pipeline/06_score.py
$PY pipeline/07_enrich.py
$PY pipeline/08_describe.py
$PY pipeline/09_dedupe.py
$PY pipeline/10_export.py
