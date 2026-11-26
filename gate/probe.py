#!/usr/bin/env python3
"""FlagGate probe runner: adversarial proposals through the REAL gate.

Builds 7 adversarial proposals + 1 clean proposal and runs each through
gate/gate.py as a subprocess (real YAML files + real state JSON),
printing one verdict line each + a summary. Exit 0 iff every verdict
matches expectation, else 1.

Base flag state is examples/state-example.json (current weights
control 90 / variant_b 10, kill-switch on, last evaluated/changed
2026-11-20). Cases that need a focused state override:
  - rollout jump: the base state puts variant_b at 10% so the plan
    can jump it to 100%.
  - kill-switch off: the base state has the kill-switch on.
  - stale: the base state is fresh; the override dates it to January.
Gate rules are FROZEN — this file only exercises them, it changes none.

Usage:
    python3 gate/probe.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "gate" / "gate.py"
STATE_FILE = ROOT / "examples" / "state-example.json"


def proposal(flag="checkout-cta", environment="development",
             variants=(( "control", 50), ("variant_b", 50)),
             enabled=True, value=None):
    lines = [
        "flag: %s" % flag,
        "environment: %s" % environment,
        "author: aman",
        "approver: harsh",
        "approved: true",
        'reason: "probe proposal"',
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


# (name, proposal yaml, state overrides, expected exit, expected substring)
CASES = [
    ("clean-50-50",
     proposal(), None, 0, "PASS"),
    ("weights-sum-99",
     proposal(variants=(("control", 49), ("variant_b", 50))),
     None, 1, "sum to 99"),
    ("rollout-jump-10-to-100",
     proposal(variants=(("control", 0), ("variant_b", 100))),
     None, 1, "rollout jump"),
    ("kill-switch-off",
     proposal(flag="kill-switch", variants=None, enabled=False),
     {"flag": "kill-switch"}, 1, "kill-switch flag"),
    ("kill-switch-global-off",
     proposal(), {"kill_switch_on": False}, 1, "safety net is down"),
    ("prod-staging-flag",
     proposal(flag="internal-debug-flag", environment="production"),
     {"flag": "internal-debug-flag"}, 1, "staging-only"),
    ("dev-prod-flag",
     proposal(flag="billing-v2", environment="development"),
     {"flag": "billing-v2"}, 1, "production-only"),
    ("stale-flag",
     proposal(),
     {"last_evaluated_at": "2026-01-01T00:00:00Z",
      "last_changed_at": "2026-01-01T00:00:00Z"},
     1, "stale"),
]


def run_gate(proposal_text, state_obj, tmp):
    p, s = Path(tmp) / "proposal.yaml", Path(tmp) / "state.json"
    p.write_text(proposal_text)
    s.write_text(json.dumps(state_obj))
    return subprocess.run([sys.executable, str(GATE), str(p), str(s)],
                          capture_output=True, text=True, cwd=ROOT)


def main():
    base_state = json.loads(STATE_FILE.read_text())
    passed = 0
    mismatches = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, text, overrides, want_exit, want_text in CASES:
            state_obj = dict(base_state)
            if overrides:
                state_obj.update(overrides)
            result = run_gate(text, state_obj, tmp)
            out = result.stdout + result.stderr
            reasons = [ln.strip()[2:] for ln in out.splitlines()
                       if ln.startswith("  - ")]
            verdict = "PASS" if result.returncode == 0 else "BLOCK"
            detail = "; ".join(reasons) if reasons else (
                "safe to approve" if result.returncode == 0 else out.strip())
            ok = (result.returncode == want_exit and want_text in out)
            if ok:
                passed += 1
                print("%s %s — %s" % (verdict, name, detail))
            else:
                mismatches.append(name)
                print("MISMATCH %s: exit %d (want %d), output: %s"
                      % (name, result.returncode, want_exit, out.strip()))
    print("probe: %d/%d as expected" % (passed, len(CASES)))
    if mismatches:
        print("probe: mismatched cases: %s" % ", ".join(mismatches))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
