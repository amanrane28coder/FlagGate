#!/usr/bin/env python3
"""Tests for agent/log.py — pretty-printing the audit log."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "agent" / "log.py"

ROWS = [
    {"ts": "2026-11-27T00:00:00+00:00", "who": "aman",
     "action": "apply", "verdict": "approved", "flag": "checkout-cta",
     "author": "aman", "approver": "harsh",
     "before": {"weights": {"control": 90, "variant_b": 10}},
     "after": {"weights": {"control": 50, "variant_b": 50}}},
    {"ts": "2026-11-27T00:01:00+00:00", "who": "aman",
     "action": "apply", "verdict": "refused", "flag": "checkout-cta",
     "author": "aman", "approver": "harsh",
     "reason": "gate blocked this change",
     "reasons": ["rollout jump for variant_b: 10% -> 100% (>50pp)"],
     "before": {"weights": {"control": 90, "variant_b": 10}},
     "after": None},
]


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

    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "audit.log"
        log.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")

        r = subprocess.run([sys.executable, str(LOG),
                            "--audit-log", str(log)],
                           capture_output=True, text=True, cwd=ROOT)
        check("log exits 0", r.returncode == 0, r.stderr)
        check("log shows the approved line",
              "APPROVED" in r.stdout and "checkout-cta" in r.stdout,
              r.stdout)
        check("log shows before -> after weights",
              "control:90" in r.stdout and "variant_b:50" in r.stdout,
              r.stdout)
        check("log shows the refused line with its reason",
              "REFUSED" in r.stdout and "rollout jump" in r.stdout,
              r.stdout)
        check("log prints the count", "2 decision(s)" in r.stdout, r.stdout)

        # missing file: no decisions yet, still exit 0
        r = subprocess.run([sys.executable, str(LOG), "--audit-log",
                            str(Path(tmp) / "missing.log")],
                           capture_output=True, text=True, cwd=ROOT)
        check("missing log exits 0", r.returncode == 0, r.stderr)
        check("missing log says so", "no decisions yet" in r.stdout,
              r.stdout)

        # malformed line: warned + skipped, count still right
        log.write_text(json.dumps(ROWS[0]) + "\nnot json {\n"
                       + json.dumps(ROWS[1]) + "\n")
        r = subprocess.run([sys.executable, str(LOG),
                            "--audit-log", str(log)],
                           capture_output=True, text=True, cwd=ROOT)
        check("malformed line exits 0", r.returncode == 0, r.stderr)
        check("malformed line is warned about",
              "malformed" in r.stdout, r.stdout)
        check("count counts only good lines",
              "2 decision(s), 1 malformed line(s) skipped" in r.stdout,
              r.stdout)

        # hash chain: chained rows verify, tampering is caught
        sys.path.insert(0, str(ROOT / "agent"))
        import audit as audit_mod
        chained = Path(tmp) / "chained.log"
        for row in ROWS:
            audit_mod.append_chained(str(chained), dict(row))
        r = subprocess.run([sys.executable, str(LOG),
                            "--audit-log", str(chained)],
                           capture_output=True, text=True, cwd=ROOT)
        check("chained log exits 0", r.returncode == 0, r.stderr)
        check("chained log reports chain OK",
              "chain: OK (2 chained)" in r.stdout, r.stdout)

        lines = chained.read_text().splitlines()
        tampered = json.loads(lines[1])
        tampered["verdict"] = "approved"  # was "refused": silent edit
        lines[1] = json.dumps(tampered)
        chained.write_text("\n".join(lines) + "\n")
        r = subprocess.run([sys.executable, str(LOG),
                            "--audit-log", str(chained)],
                           capture_output=True, text=True, cwd=ROOT)
        check("tampered log exits 1", r.returncode == 1, r.stderr)
        check("tampered log names the broken line",
              "chain BROKEN at line 2" in r.stdout, r.stdout)

        # deletion: dropping the first chained line breaks the next prev
        chained.unlink()
        for row in ROWS:
            audit_mod.append_chained(str(chained), dict(row))
        lines = chained.read_text().splitlines()
        chained.write_text(lines[1] + "\n")
        r = subprocess.run([sys.executable, str(LOG),
                            "--audit-log", str(chained)],
                           capture_output=True, text=True, cwd=ROOT)
        check("deleted line exits 1", r.returncode == 1, r.stderr)
        check("deleted line names the broken line",
              "chain BROKEN at line 1" in r.stdout, r.stdout)

        # tail truncation: dropping the LAST line leaves a valid chain.
        # Documented limit (audit.py docstring + README): a chain with
        # no external anchor cannot detect a shortened log.
        chained.unlink()
        for row in ROWS:
            audit_mod.append_chained(str(chained), dict(row))
        lines = chained.read_text().splitlines()
        chained.write_text(lines[0] + "\n")
        r = subprocess.run([sys.executable, str(LOG),
                            "--audit-log", str(chained)],
                           capture_output=True, text=True, cwd=ROOT)
        check("tail truncation is NOT detected (documented limit)",
              r.returncode == 0 and "chain: OK" in r.stdout, r.stdout)

        # legacy first line (pre-chain) + chained second: exits 0, noted
        legacy = Path(tmp) / "legacy.log"
        legacy.write_text(json.dumps(ROWS[0]) + "\n")
        audit_mod.append_chained(str(legacy), dict(ROWS[1]))
        r = subprocess.run([sys.executable, str(LOG),
                            "--audit-log", str(legacy)],
                           capture_output=True, text=True, cwd=ROOT)
        check("legacy first line exits 0", r.returncode == 0, r.stderr)
        check("legacy first line is noted",
              "1 legacy (unverified)" in r.stdout, r.stdout)

    print("test_log: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
