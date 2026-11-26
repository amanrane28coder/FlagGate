#!/usr/bin/env python3
"""FlagGate apply: apply an approved, gate-checked proposal to Flagsmith.

The human gate lives here, not in gate.py: gate.py checks safety, this
file checks that a human approved the change — and that the approver is
not the author. Only then does it touch the Flagsmith API.

Flow:
  1. load the proposal YAML + the flag state (from --state, or from the
     Flagsmith API when --api-key/--env-key/--feature-id are given;
     when both are given, the state file supplies the slow-moving
     metadata — kill-switch flags, staging lists, timestamps — and the
     API supplies the fresh weights/value/enabled)
  2. re-run gate.check on proposal + state; BLOCK -> REFUSED, audit
     verdict "refused", exit 1
  3. approval check: approved must be true and approver must differ
     from author; otherwise REFUSED, audit verdict "refused", exit 1
  4. build the PATCH (update-flag endpoint) from the proposal change
  5. dry-run (default): print the PATCH, audit verdict "dry-run", exit 0
  6. --approve: PATCH via urllib, verify the published state from the
     response, audit verdict "approved", print APPLIED + rollback

The write goes through Flagsmith's experimental update-flag endpoint
(`PATCH api/__future__/environments/<env-key>/features/<feature-id>/`),
verified present in the self-hosted build's own OpenAPI schema
(/api/v1/swagger.json, 2026-11-27): the endpoint is versioning-agnostic
and publishes a new version per call on v2 environments. Auth is
`Authorization: Api-Key <management API key>` (Flagsmith's own docs;
the local schema declares no security scheme, so the header comes from
the docs, and the API 401s without it). The environment key in the URL
is the SDK key for the environment; the API key is the management key
from the dashboard. They are different keys.

Usage:
    python3 agent/apply.py examples/proposal-clean.yaml --state examples/state-example.json
    python3 agent/apply.py examples/proposal-clean.yaml --state examples/state-example.json --approve
    python3 agent/apply.py proposal.yaml --api-key KEY --env-key ENVKEY --feature-id 1 --option-ids '{"variant_b": 1}' --approve
    python3 agent/apply.py --help          # usage, no API call

Exit codes: 0 applied (or dry-run printed), 1 refusal, 2 error.
"""

import datetime
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gate"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate as gate_mod
import audit as audit_mod

DEFAULT_API_BASE = "http://localhost:8002/api"
DEFAULT_AUDIT_LOG = "audit.log"

USAGE = ("usage: apply.py <proposal.yaml> [--state FILE] [--approve]\n"
         "       [--api-base URL] [--api-key KEY] [--env-key KEY]\n"
         "       [--feature-id ID] [--option-ids JSON] [--audit-log FILE]\n"
         "       [--who NAME]\n"
         "       apply.py --help")


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def append_audit(path, entry):
    """Append one audit line, hash-chained (see agent/audit.py)."""
    audit_mod.append_chained(path, entry)


def api_request(api_base, api_key, method, path, body=None):
    """One Management API call via stdlib urllib. Returns parsed JSON."""
    url = api_base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Api-Key " + api_key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise RuntimeError("%s %s -> HTTP %d: %s"
                           % (method, path, e.code, detail))
    except urllib.error.URLError as e:
        raise RuntimeError("%s %s -> %s" % (method, path, e.reason))


def state_from_api(api_base, api_key, env_key, feature_id, option_ids,
                   proposal):
    """Fetch the flag's current state via the update-flag GET."""
    path = ("/__future__/environments/%s/features/%s/"
            % (env_key, feature_id))
    flag = api_request(api_base, api_key, "GET", path)
    if not isinstance(flag, dict):
        raise RuntimeError("flag %s returned unexpected data: %r"
                           % (feature_id, flag))
    env_default = flag.get("environment_default") or {}
    id_to_key = {oid: k for k, oid in option_ids.items()}
    weights = {}
    total_variant = 0
    for v in env_default.get("variants") or []:
        key = id_to_key.get(v.get("id"))
        if key is None:
            raise RuntimeError("live state has unknown option id %s "
                               "(pass --option-ids for every variant)"
                               % v.get("id"))
        weights[key] = v.get("weight")
        total_variant += v.get("weight") or 0
    weights["control"] = 100 - total_variant
    value = (env_default.get("value") or {}).get("value")
    return {
        "flag": proposal.get("flag"),
        "environment": proposal.get("environment"),
        "feature_id": feature_id,
        "option_ids": option_ids,
        "current_weights": weights,
        "current_value": value,
        "enabled": env_default.get("enabled"),
        # The live API state carries none of these; the gate skips
        # the checks that need them (stale) and defaults the rest
        # (kill-switch on, no staging lists). Pass --state as well
        # to supply them from a fixture.
        "kill_switch_on": True,
        "kill_switch_flags": [],
        "staging_only_flags": [],
        "production_only_flags": [],
    }


def build_patch(proposal, option_ids):
    """Build the update-flag PATCH body from the proposal change."""
    change = proposal.get("change") or {}
    env_default = {}
    if "enabled" in change:
        env_default["enabled"] = change["enabled"]
    if "value" in change:
        env_default["value"] = {"type": "string",
                                "value": str(change["value"])}
    if "variants" in change:
        listed = {v["key"] for v in change["variants"]}
        known = set(option_ids)
        if listed - {"control"} != known:
            raise RuntimeError(
                "option_ids %s do not match the proposal variants %s "
                "(every non-control variant needs an option id)"
                % (sorted(known), sorted(listed - {"control"})))
        variants = []
        for v in change["variants"]:
            if v["key"] == "control":
                continue
            variants.append({"id": option_ids[v["key"]],
                             "weight": v["weight"]})
        env_default["variants"] = variants
    return {"environment_default": env_default}


def before_after(proposal, state):
    """(before, after) dicts for printing + audit."""
    change = proposal.get("change") or {}
    before = {
        "enabled": state.get("enabled"),
        "value": state.get("current_value"),
        "weights": dict(state.get("current_weights", {})),
    }
    after = {
        "enabled": change.get("enabled", before["enabled"]),
        "value": (str(change["value"]) if "value" in change
                  else before["value"]),
        "weights": dict(before["weights"]),
    }
    if "variants" in change:
        for v in change["variants"]:
            after["weights"][v["key"]] = v["weight"]
    return before, after


def main(argv):
    args = argv[1:]
    if "--help" in args or "-h" in args:
        print(USAGE)
        return 0
    if not args or args[0].startswith("-"):
        print(USAGE, file=sys.stderr)
        return 2

    proposal_path = args[0]
    approve = "--approve" in args

    def opt_value(name, default=None):
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return args[i + 1]
        return default

    state_path = opt_value("--state")
    api_base = opt_value("--api-base", DEFAULT_API_BASE)
    api_key = opt_value("--api-key") or os.environ.get("FLAGATE_API_KEY")
    env_key = opt_value("--env-key")
    feature_id = opt_value("--feature-id")
    option_ids_arg = opt_value("--option-ids")
    audit_log = opt_value("--audit-log", DEFAULT_AUDIT_LOG)
    who = opt_value("--who") or os.environ.get("USER") or getpass.getuser()

    try:
        proposal = gate_mod.load_proposal(proposal_path)
    except (FileNotFoundError, ValueError) as e:
        print("apply error: cannot read proposal: %s" % e, file=sys.stderr)
        return 2
    if not isinstance(proposal, dict):
        print("apply error: proposal must be a mapping", file=sys.stderr)
        return 2
    flag = proposal.get("flag")
    author = proposal.get("author")
    approver = proposal.get("approver")

    def fail_audit(error):
        """Record a failed apply attempt (proposal was readable, so the
        attempt itself belongs in the trail)."""
        append_audit(audit_log, {
            "ts": utcnow(),
            "who": who,
            "action": "apply",
            "verdict": "failed",
            "flag": flag,
            "author": author,
            "approver": approver,
            "error": str(error),
            "before": None,
            "after": None,
        })

    # ---- 1. load the flag state ----
    state = None
    live = bool(api_key and env_key)
    try:
        if state_path:
            with open(state_path) as f:
                state = json.load(f)
            if not isinstance(state, dict):
                fail_audit("state must be a JSON object")
                print("apply error: state must be a JSON object",
                      file=sys.stderr)
                return 2
        if live:
            if feature_id is None:
                feature_id = (state or {}).get("feature_id")
            if feature_id is None:
                fail_audit("live mode needs --feature-id (or a state "
                           "file carrying feature_id)")
                print("apply error: live mode needs --feature-id "
                      "(or a state file carrying feature_id)",
                      file=sys.stderr)
                return 2
            option_ids = None
            if option_ids_arg:
                option_ids = json.loads(option_ids_arg)
            elif state:
                option_ids = state.get("option_ids")
            if not option_ids:
                fail_audit("live mode needs --option-ids (or a state "
                           "file carrying option_ids)")
                print("apply error: live mode needs --option-ids "
                      "(or a state file carrying option_ids)",
                      file=sys.stderr)
                return 2
            live_state = state_from_api(api_base, api_key, env_key,
                                        feature_id, option_ids, proposal)
            if state:
                # Fixture metadata wins; the API wins on fresh values.
                merged = dict(state)
                merged.update(live_state)
                merged["kill_switch_on"] = state.get("kill_switch_on",
                                                     True)
                merged["kill_switch_flags"] = state.get(
                    "kill_switch_flags", [])
                merged["staging_only_flags"] = state.get(
                    "staging_only_flags", [])
                merged["production_only_flags"] = state.get(
                    "production_only_flags", [])
                merged["last_evaluated_at"] = state.get(
                    "last_evaluated_at")
                merged["last_changed_at"] = state.get("last_changed_at")
                state = merged
            else:
                state = live_state
        elif state is None:
            fail_audit("no state — pass --state, or live credentials "
                       "(--api-key + --env-key + --feature-id)")
            print("apply error: no state — pass --state, or live "
                  "credentials (--api-key + --env-key + --feature-id)",
                  file=sys.stderr)
            return 2
    except FileNotFoundError as e:
        fail_audit(e)
        print("apply error: %s" % e, file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as e:
        fail_audit(e)
        print("apply error: %s" % e, file=sys.stderr)
        return 2

    option_ids = None
    if option_ids_arg:
        try:
            option_ids = json.loads(option_ids_arg)
        except ValueError as e:
            fail_audit("--option-ids is not valid JSON: %s" % e)
            print("apply error: --option-ids is not valid JSON: %s" % e,
                  file=sys.stderr)
            return 2
    elif state:
        option_ids = state.get("option_ids") or {}
    if feature_id is None:
        feature_id = (state or {}).get("feature_id")

    before, after = before_after(proposal, state)

    def refuse(reason, reasons=None):
        print("REFUSED: %s" % reason)
        if reasons:
            for r in reasons:
                print("  - %s" % r)
        append_audit(audit_log, {
            "ts": utcnow(),
            "who": who,
            "action": "apply",
            "verdict": "refused",
            "flag": flag,
            "author": author,
            "approver": approver,
            "reason": reason,
            "reasons": reasons or [],
            "before": before,
            "after": None,
        })
        return 1

    # ---- 2. re-run the gate on proposal + state ----
    reasons, skipped = gate_mod.check(proposal, state)
    for s in skipped:
        print("note: %s" % s)
    if reasons:
        return refuse("gate blocked this change", reasons)

    # ---- 3. the human gate: approved, by someone other than the author ----
    if proposal.get("approved") is not True:
        return refuse("proposal is not approved (approved: true + an "
                      "approver are required)")
    if not approver:
        return refuse("proposal names no approver")
    if approver == author:
        return refuse("approver '%s' is the author — the approver must "
                      "be a different person" % approver)

    # ---- 4. build the PATCH ----
    try:
        patch = build_patch(proposal, option_ids or {})
    except RuntimeError as e:
        fail_audit(e)
        print("apply error: %s" % e, file=sys.stderr)
        return 2
    if env_key is None or feature_id is None:
        # Dry-run against a fixture: there is no live endpoint to
        # address, so show the PATCH shape without a URL.
        patch_url = ("<api-base>/__future__/environments/<env-key>/"
                     "features/%s/" % feature_id)
    else:
        patch_url = ("%s/__future__/environments/%s/features/%s/"
                     % (api_base.rstrip("/"), env_key, feature_id))

    print("== FlagGate apply: %s (%s) by %s, approved by %s"
          % (flag, proposal.get("environment", "?"), author, approver))
    print("   before: %s" % before)
    print("   after:  %s" % after)
    print("   PATCH %s" % patch_url)
    print("   %s" % json.dumps(patch))
    if not approve:
        print("   dry-run: re-run with --approve to apply. "
              "No change made.")
        append_audit(audit_log, {
            "ts": utcnow(),
            "who": who,
            "action": "apply",
            "verdict": "dry-run",
            "flag": flag,
            "author": author,
            "approver": approver,
            "before": before,
            "after": after,
        })
        return 0

    if api_key is None or env_key is None or feature_id is None:
        fail_audit("--approve needs live credentials (--api-key + "
                   "--env-key + --feature-id)")
        print("apply error: --approve needs live credentials "
              "(--api-key + --env-key + --feature-id)", file=sys.stderr)
        return 2

    # ---- 4b. re-read the live state right before the write. The gate
    # checked the state from step 1; if the flag moved since (another
    # dashboard user, another process), the gate's picture is stale and
    # this write would silently overwrite their change — refuse instead.
    # The endpoint has no compare-and-swap, so this narrows the race
    # window; it does not close it (see README, Limits).
    if live:
        try:
            fresh = state_from_api(api_base, api_key, env_key,
                                   feature_id, option_ids, proposal)
        except RuntimeError as e:
            fail_audit(e)
            print("apply error: re-read before write failed: %s" % e,
                  file=sys.stderr)
            return 2
        fresh_before = {
            "enabled": fresh.get("enabled"),
            "value": fresh.get("current_value"),
            "weights": dict(fresh.get("current_weights", {})),
        }
        if fresh_before != before:
            return refuse("flag state changed since the gate check "
                          "(re-read before write): %s -> %s"
                          % (before, fresh_before))

    # ---- 5. PATCH, then verify the published state ----
    try:
        updated = api_request(api_base, api_key, "PATCH",
                              "/__future__/environments/%s/features/%s/"
                              % (env_key, feature_id), patch)
    except RuntimeError as e:
        print("apply error: %s" % e, file=sys.stderr)
        append_audit(audit_log, {
            "ts": utcnow(),
            "who": who,
            "action": "apply",
            "verdict": "failed",
            "flag": flag,
            "author": author,
            "approver": approver,
            "error": str(e),
            "before": before,
            "after": None,
        })
        return 2
    if not isinstance(updated, dict):
        updated = {}
    resp_default = updated.get("environment_default") or {}
    id_to_key = {oid: k for k, oid in (option_ids or {}).items()}
    resp_weights = {k: 0 for k in after["weights"]}
    for v in resp_default.get("variants") or []:
        key = id_to_key.get(v.get("id"))
        if key is not None:
            resp_weights[key] = v.get("weight")
    if resp_weights:
        total_variant = sum(w for k, w in resp_weights.items()
                            if k != "control")
        resp_weights["control"] = 100 - total_variant
    mismatch = None
    if resp_weights != after["weights"]:
        mismatch = ("server returned weights %s, expected %s"
                    % (resp_weights, after["weights"]))
    elif ("value" in (proposal.get("change") or {})
          and (resp_default.get("value") or {}).get("value")
          != str(proposal["change"]["value"])):
        mismatch = ("server returned value %r, expected %r"
                    % ((resp_default.get("value") or {}).get("value"),
                       str(proposal["change"]["value"])))
    elif ("enabled" in (proposal.get("change") or {})
          and resp_default.get("enabled")
          != proposal["change"]["enabled"]):
        mismatch = ("server returned enabled=%r, expected %r"
                    % (resp_default.get("enabled"),
                       proposal["change"]["enabled"]))
    if mismatch:
        print("apply error: %s" % mismatch, file=sys.stderr)
        append_audit(audit_log, {
            "ts": utcnow(),
            "who": who,
            "action": "apply",
            "verdict": "failed",
            "flag": flag,
            "author": author,
            "approver": approver,
            "error": mismatch,
            "before": before,
            "after": None,
        })
        return 2

    append_audit(audit_log, {
        "ts": utcnow(),
        "who": who,
        "action": "apply",
        "verdict": "approved",
        "via": "update-flag (__future__)",
        "flag": flag,
        "author": author,
        "approver": approver,
        "before": before,
        "after": after,
    })
    rollback = {"environment_default": {}}
    if before["weights"]:
        rollback["environment_default"]["variants"] = [
            {"id": (option_ids or {}).get(k), "weight": w}
            for k, w in before["weights"].items() if k != "control"]
    if before["value"] is not None:
        rollback["environment_default"]["value"] = {
            "type": "string", "value": str(before["value"])}
    if before["enabled"] is not None:
        rollback["environment_default"]["enabled"] = before["enabled"]
    print("== APPLIED: %s now serves %s" % (flag, after))
    print("== rollback: PATCH %s with %s"
          % (patch_url, json.dumps(rollback)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
