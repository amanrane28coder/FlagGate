#!/usr/bin/env python3
"""Tests for gate/gate.py — real subprocess runs on proposal YAML +
state JSON files in a temp dir (ExpAgent style)."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "gate" / "gate.py"

BASE_STATE = {
    "flag": "checkout-cta",
    "environment": "development",
    "feature_id": 1,
    "option_ids": {"variant_b": 1},
    "current_weights": {"control": 90, "variant_b": 10},
    "current_value": "Buy now",
    "enabled": True,
    "kill_switch_on": True,
    "kill_switch_flags": ["kill-switch"],
    "staging_only_flags": ["internal-debug-flag"],
    "production_only_flags": ["billing-v2"],
    "last_evaluated_at": "2026-11-20T00:00:00Z",
    "last_changed_at": "2026-11-20T00:00:00Z",
}


def proposal(flag="checkout-cta", environment="development",
             variants=(("control", 50), ("variant_b", 50)),
             enabled=True, value="Buy now"):
    lines = [
        "flag: %s" % flag,
        "environment: %s" % environment,
        "author: aman",
        "approver: harsh",
        "approved: true",
        'reason: "test proposal"',
        "change:",
        "  enabled: %s" % ("true" if enabled else "false"),
    ]
    if value is not None:
        lines.append('  value: "%s"' % value)
    if variants is not None:
        lines.append("  variants:")
        for key, weight in variants:
            lines.append("    - key: %s" % key)
            lines.append("      weight: %s" % weight)
    return "\n".join(lines) + "\n"


def run_gate(tmp, text, state):
    p, s = Path(tmp) / "proposal.yaml", Path(tmp) / "state.json"
    p.write_text(text)
    s.write_text(json.dumps(state))
    return subprocess.run([sys.executable, str(GATE), str(p), str(s)],
                          capture_output=True, text=True, cwd=ROOT)


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
        # 1. clean change passes
        r = run_gate(tmp, proposal(), BASE_STATE)
        check("clean 50/50 passes (exit 0)", r.returncode == 0, r.stdout)
        check("clean prints PASS", "PASS" in r.stdout, r.stdout)

        # 2. weights sum != 100
        r = run_gate(tmp, proposal(variants=(("control", 49),
                                             ("variant_b", 50))),
                     BASE_STATE)
        check("weights sum 99 blocked (exit 1)", r.returncode == 1,
              r.stdout)
        check("block names the sum", "sum to 99" in r.stdout, r.stdout)

        # 3. rollout jump > 50pp
        r = run_gate(tmp, proposal(variants=(("control", 0),
                                             ("variant_b", 100))),
                     BASE_STATE)
        check("jump 10->100 blocked", r.returncode == 1, r.stdout)
        check("block names the jump", "rollout jump" in r.stdout, r.stdout)

        # 4. turning off a kill-switch flag
        state = dict(BASE_STATE, flag="kill-switch")
        r = run_gate(tmp, proposal(flag="kill-switch", variants=None,
                                   enabled=False, value=None),
                     state)
        check("kill-switch off blocked", r.returncode == 1, r.stdout)
        check("block names the kill-switch", "kill-switch flag" in r.stdout,
              r.stdout)

        # 5. global kill-switch down blocks everything
        state = dict(BASE_STATE, kill_switch_on=False)
        r = run_gate(tmp, proposal(), state)
        check("global kill-switch off blocked", r.returncode == 1,
              r.stdout)
        check("block names the safety net", "safety net is down" in r.stdout,
              r.stdout)

        # 6. prod + staging-only flag
        state = dict(BASE_STATE, flag="internal-debug-flag")
        r = run_gate(tmp, proposal(flag="internal-debug-flag",
                                   environment="production"),
                     state)
        check("prod staging-only blocked", r.returncode == 1, r.stdout)
        check("block names staging-only", "staging-only" in r.stdout,
              r.stdout)

        # 7. dev + production-only flag
        state = dict(BASE_STATE, flag="billing-v2")
        r = run_gate(tmp, proposal(flag="billing-v2",
                                   environment="development"),
                     state)
        check("dev production-only blocked", r.returncode == 1, r.stdout)
        check("block names production-only", "production-only" in r.stdout,
              r.stdout)

        # 8. stale flag
        state = dict(BASE_STATE,
                     last_evaluated_at="2026-01-01T00:00:00Z",
                     last_changed_at="2026-01-01T00:00:00Z")
        r = run_gate(tmp, proposal(), state)
        check("stale flag blocked", r.returncode == 1, r.stdout)
        check("block names stale", "stale" in r.stdout, r.stdout)

        # 9. stale check skipped (no timestamps) — clean change passes
        state = {k: v for k, v in BASE_STATE.items()
                 if k not in ("last_evaluated_at", "last_changed_at")}
        r = run_gate(tmp, proposal(), state)
        check("no timestamps: clean change still passes",
              r.returncode == 0, r.stdout)
        check("no timestamps: skip is noted",
              "stale check" in r.stdout, r.stdout)

        # 10. value-only change (no variants) passes
        r = run_gate(tmp, proposal(variants=None), BASE_STATE)
        check("value-only change passes", r.returncode == 0, r.stdout)

        # 11. state/proposal flag mismatch
        state = dict(BASE_STATE, flag="other-flag")
        r = run_gate(tmp, proposal(), state)
        check("flag mismatch blocked", r.returncode == 1, r.stdout)

        # 12. malformed YAML -> exit 2
        p = Path(tmp) / "bad.yaml"
        p.write_text("a: 1\na: 2\n")
        s = Path(tmp) / "state.json"
        s.write_text(json.dumps(BASE_STATE))
        r = subprocess.run([sys.executable, str(GATE), str(p), str(s)],
                           capture_output=True, text=True, cwd=ROOT)
        check("malformed YAML exits 2", r.returncode == 2, r.stdout)

        # 13. missing file -> exit 2
        r = subprocess.run([sys.executable, str(GATE),
                            str(Path(tmp) / "nope.yaml"), str(s)],
                           capture_output=True, text=True, cwd=ROOT)
        check("missing file exits 2", r.returncode == 2, r.stdout)

        # 14. --help / no args -> exit 2 with usage
        r = subprocess.run([sys.executable, str(GATE)],
                           capture_output=True, text=True, cwd=ROOT)
        check("no args exits 2", r.returncode == 2, r.stdout)

    print("test_gate: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
