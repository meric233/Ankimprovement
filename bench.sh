#!/usr/bin/env bash
# One-command benchmark (PRD §10, spec §7h): loads a 50k-card deck and prints
# p50 / p95 / worst for each latency-critical action, then runs the crash /
# durability test. Usage:  ./bench.sh [--cards N]
set -euo pipefail
cd "$(dirname "$0")"

PY="out/pyenv/bin/python"
export PYTHONPATH="$PWD/out/pylib:$PWD/out/qt"
CARDS="${2:-50000}"

echo "############################################################"
echo "# make bench — speed + reliability on a ${CARDS}-card deck"
echo "############################################################"
"$PY" perf_bench.py --cards "$CARDS"

echo
echo "############################################################"
echo "# crash / durability (20 SIGKILL-mid-write cycles)"
echo "############################################################"
"$PY" crash_test.py --cycles 20
