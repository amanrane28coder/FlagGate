#!/usr/bin/env python3
"""Tests for gate/probe.py — the probe runner must exit 0 with all
cases as expected."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "gate" / "probe.py"


def main():
    passed = failed = 0

    def check(name, ok, detail=""):
        nonlocal passed, failed
        if ok:
            print("PASS %s" % name)
            passed += 1
        else:
            print("FAIL %s: %s" % (name, detail))
            failed += 1

    r = subprocess.run([sys.executable, str(PROBE)],
                       capture_output=True, text=True, cwd=ROOT)
    check("probe exits 0", r.returncode == 0, r.stdout + r.stderr)
    check("probe reports 8/8 as expected",
          "probe: 8/8 as expected" in r.stdout, r.stdout)
    for name in ["clean-50-50", "weights-sum-99", "rollout-jump-10-to-100",
                 "kill-switch-off", "kill-switch-global-off",
                 "prod-staging-flag", "dev-prod-flag", "stale-flag"]:
        check("probe covers %s" % name, name in r.stdout, r.stdout)

    print("test_probe: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
