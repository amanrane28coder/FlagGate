#!/bin/sh
# FlagGate test suite — runs every test script as a real subprocess.
# Usage: ./tests/run_tests.sh   (from the repo root)
set -u
cd "$(dirname "$0")/.."

fail=0
for t in tests/test_yamlmini.py tests/test_gate.py tests/test_probe.py tests/test_apply.py tests/test_log.py tests/test_demo.py; do
  echo "== $t"
  if ! python3 "$t"; then
    fail=1
  fi
  echo
done

if [ "$fail" -eq 0 ]; then
  echo "ALL TESTS PASSED"
else
  echo "TESTS FAILED"
fi
exit "$fail"
