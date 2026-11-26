#!/usr/bin/env python3
"""FlagGate gate: deterministic checks on a proposed flag change.

Release Gate / ExpAgent style: pure checks, no network, no Flagsmith
calls. Reads a proposal YAML and a flag-state JSON, prints a verdict,
exits:
    0  PASS  — change is safe to approve (still needs a human approver)
    1  BLOCK — one or more checks failed; reasons printed
    2  ERROR — unreadable/missing file or malformed input

Proposal YAML shape (see examples/proposal-clean.yaml):
    flag: checkout-cta
    environment: development
    author: aman
    approver: harsh
    approved: true
    reason: "Roll the new CTA copy to 50%"
    change:
      enabled: true
      value: "Buy now"            # optional, string value
      variants:                   # optional; all variants incl. control
        - key: control
          weight: 50
        - key: variant_b
          weight: 50

Flag-state JSON shape (see examples/state-example.json):
    {
      "flag": "checkout-cta",
      "environment": "development",
      "feature_id": 1,
      "option_ids": {"variant_b": 1},
      "current_weights": {"control": 90, "variant_b": 10},
      "current_value": "Buy now",
      "enabled": true,
      "kill_switch_on": true,
      "kill_switch_flags": ["kill-switch"],
      "staging_only_flags": ["internal-debug-flag"],
      "production_only_flags": ["billing-v2"],
      "last_evaluated_at": "2026-11-20T00:00:00Z",
      "last_changed_at": "2026-11-20T00:00:00Z"
    }

Checks (all deterministic, order is stable):
  1. variant weights sum to 100 (when the change sets variants)
  2. no variant moves more than 50pp vs its current weight
  3. the change does not turn off a kill-switch / safety flag
  4. the global kill-switch is on (no changes while the safety
     net is down)
  5. a staging-only flag is not changed in production, and a
     production-only flag is not changed outside production
  6. the flag is not stale: no evaluation or change in > 90 days
     (a cleanup flag — BLOCK, never auto-delete). Skipped with a
     printed note when the state carries no evaluation timestamps
     (the live API state has none); skipped is not a pass.

Usage:
    python3 gate/gate.py proposal.yaml state.json
"""

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yamlmini

MAX_ROLLOUT_JUMP_PP = 50
STALE_DAYS = 90


def _parse_ts(value):
    """Parse an ISO timestamp (trailing Z allowed). None on failure."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(
            value.replace("Z", "+00:00"))
    except ValueError:
        return None


def check(proposal, state, now=None):
    """Return (reasons, skipped): block reasons, and checks skipped
    because the state lacks the data they need. Empty reasons = PASS."""
    reasons = []
    skipped = []
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(proposal, dict):
        return ["proposal is not a mapping"], skipped
    if not isinstance(state, dict):
        return ["state is not a mapping"], skipped

    flag = proposal.get("flag")
    env = proposal.get("environment")
    change = proposal.get("change")
    if not flag or not env or not isinstance(change, dict):
        return ["proposal is missing 'flag', 'environment' or 'change'"], \
            skipped
    if state.get("flag") is not None and state.get("flag") != flag:
        return ["state is for flag '%s', proposal is for '%s'"
                % (state.get("flag"), flag)], skipped

    # 1 + 2. variant weights (only when the change sets variants).
    variants = change.get("variants")
    if variants is not None:
        if not isinstance(variants, list) or not variants:
            return ["change variants must be a non-empty list"], skipped
        weights = []
        for v in variants:
            if (not isinstance(v, dict) or "key" not in v
                    or "weight" not in v):
                return ["change variant is missing 'key' or 'weight'"],
            weights.append(v["weight"])
        if any(not isinstance(w, (int, float)) for w in weights):
            return ["variant weights must be numbers"], skipped
        total = sum(weights)
        if abs(total - 100) > 0.001:
            reasons.append("variant weights sum to %s, not 100" % total)
        current = state.get("current_weights", {})
        for v in variants:
            old = current.get(v["key"], 0)
            if abs(v["weight"] - old) > MAX_ROLLOUT_JUMP_PP:
                reasons.append("rollout jump for %s: %s%% -> %s%% (>%dpp)"
                               % (v["key"], old, v["weight"],
                                  MAX_ROLLOUT_JUMP_PP))

    # 3. turning off a kill-switch flag.
    kill_flags = state.get("kill_switch_flags", [])
    if flag in kill_flags and change.get("enabled") is False:
        reasons.append("change turns off kill-switch flag '%s'" % flag)

    # 4. the global kill-switch is down.
    if state.get("kill_switch_on", True) is False:
        reasons.append("kill-switch is off — no changes while the "
                       "safety net is down")

    # 5. environment / flag-name mismatch.
    if env == "production" and flag in state.get("staging_only_flags", []):
        reasons.append("flag '%s' is staging-only, cannot change it in "
                       "production" % flag)
    if env != "production" and flag in state.get("production_only_flags",
                                                 []):
        reasons.append("flag '%s' is production-only, cannot change it "
                       "in %s" % (flag, env))

    # 6. stale flag: no evaluation or change in > 90 days.
    last_eval = _parse_ts(state.get("last_evaluated_at"))
    last_change = _parse_ts(state.get("last_changed_at"))
    seen = [t for t in (last_eval, last_change) if t is not None]
    if not seen:
        skipped.append("stale check: no evaluation timestamps in state")
    else:
        latest = max(seen)
        if (now - latest).days > STALE_DAYS:
            reasons.append("flag is stale: last evaluated/changed %s "
                           "(>%d days ago) — flag for cleanup, do not "
                           "apply" % (latest.date().isoformat(),
                                      STALE_DAYS))

    return reasons, skipped


def load_proposal(path):
    with open(path) as f:
        return yamlmini.parse(f.read())


def load_json(path):
    with open(path) as f:
        return json.load(f)


def main(argv):
    if len(argv) != 3:
        print("usage: gate.py <proposal.yaml> <state.json>",
              file=sys.stderr)
        return 2
    try:
        proposal = load_proposal(argv[1])
        state = load_json(argv[2])
    except FileNotFoundError as e:
        print("gate error: %s" % e, file=sys.stderr)
        return 2
    except (ValueError, json.JSONDecodeError) as e:
        print("gate error: malformed input: %s" % e, file=sys.stderr)
        return 2
    if not isinstance(proposal, dict) or not isinstance(state, dict):
        print("gate error: proposal and state must be mappings",
              file=sys.stderr)
        return 2

    reasons, skipped = check(proposal, state)
    for s in skipped:
        print("note: %s" % s)
    if not reasons:
        print("PASS: change to '%s' (%s) is safe to approve"
              % (proposal.get("flag", "?"),
                 proposal.get("environment", "?")))
        return 0
    print("BLOCK: %d check(s) failed" % len(reasons))
    for r in reasons:
        print("  - %s" % r)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
