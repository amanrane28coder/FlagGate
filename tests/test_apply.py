#!/usr/bin/env python3
"""Tests for agent/apply.py — real subprocess runs against a FAKE
Flagsmith Management API: a tiny stdlib http.server on an ephemeral
port that records requests and returns canned JSON.

The fake answers:
  - GET /api/__future__/environments/TEST_ENV/features/1/ ->
    the flag state (enabled, value "Buy now", variant_b weight 10)
  - PATCH same path -> requires `Authorization: Api-Key test-key`
    (else 401), echoes the new weights back as the updated flag state
  - anything else -> 404
Set FAKE_MISMATCH=1 to make the PATCH echo back the OLD weights
(the applier must refuse to audit a lie: exit 2, verdict failed).

The real Flagsmith instance is never touched.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPLY = ROOT / "agent" / "apply.py"

CLEAN = """flag: checkout-cta
environment: development
author: aman
approver: harsh
approved: true
reason: "Roll the new CTA copy to 50%"
change:
  enabled: true
  value: "Buy now"
  variants:
    - key: control
      weight: 50
    - key: variant_b
      weight: 50
"""

JUMP = """flag: checkout-cta
environment: development
author: aman
approver: harsh
approved: true
reason: "Ship the winner to everyone at once"
change:
  enabled: true
  value: "Buy now"
  variants:
    - key: control
      weight: 0
    - key: variant_b
      weight: 100
"""

STATE = {
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


class FakeFlagsmith(BaseHTTPRequestHandler):
    calls = []
    mismatch = False
    # Set state_change=True to make the flag move between the gate-check
    # GET and the pre-write re-read GET (2nd GET returns new weights).
    state_change = False
    get_count = 0

    def log_message(self, *args):
        pass

    def _send(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode() if length else ""
        FakeFlagsmith.calls.append(
            {"method": self.command, "path": self.path,
             "auth": self.headers.get("Authorization"), "body": raw})
        return raw

    def do_GET(self):
        self._record()
        if self.path == "/api/__future__/environments/TEST_ENV/features/1/":
            if self.headers.get("Authorization") != "Api-Key test-key":
                self._send(401, {"detail": "Authentication credentials "
                                            "were not provided."})
                return
            FakeFlagsmith.get_count += 1
            weight = 10.0
            if FakeFlagsmith.state_change and FakeFlagsmith.get_count >= 2:
                weight = 80.0  # someone else moved the flag meanwhile
            self._send(200, {
                "environment_default": {
                    "enabled": True,
                    "value": {"type": "string", "value": "Buy now"},
                    "variants": [{"id": 1, "weight": weight}]},
                "segment_overrides": []})
        else:
            self._send(404, {"detail": "Not found."})

    def do_PATCH(self):
        raw = self._record()
        if self.headers.get("Authorization") != "Api-Key test-key":
            self._send(401, {"detail": "Authentication credentials "
                                        "were not provided."})
            return
        if self.path != "/api/__future__/environments/TEST_ENV/features/1/":
            self._send(404, {"detail": "Not found."})
            return
        body = json.loads(raw) if raw else {}
        variants = (body.get("environment_default") or {}).get("variants")
        if FakeFlagsmith.mismatch:
            variants = [{"id": 1, "weight": 10.0}]  # echo the OLD weights
        self._send(200, {
            "environment_default": {
                "enabled": (body.get("environment_default") or {})
                            .get("enabled", True),
                "value": (body.get("environment_default") or {})
                         .get("value", {"type": "string",
                                        "value": "Buy now"}),
                "variants": variants},
            "segment_overrides": []})


def start_fake():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeFlagsmith)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def run_apply(tmp, args, extra_env=None):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(APPLY)] + args,
                          capture_output=True, text=True, cwd=ROOT, env=env)


def write_files(tmp, proposal=CLEAN, state=STATE):
    p = Path(tmp) / "proposal.yaml"
    p.write_text(proposal)
    s = Path(tmp) / "state.json"
    s.write_text(json.dumps(state))
    return p, s


def read_audit(tmp):
    log = Path(tmp) / "audit.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


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

    server = start_fake()
    port = server.server_address[1]
    api_base = "http://127.0.0.1:%d/api" % port
    live = ["--api-base", api_base, "--api-key", "test-key",
            "--env-key", "TEST_ENV", "--feature-id", "1",
            "--option-ids", '{"variant_b": 1}']

    with tempfile.TemporaryDirectory() as tmp:
        audit = str(Path(tmp) / "audit.log")

        # 1. dry-run with --state: no HTTP, prints PATCH, audit dry-run
        FakeFlagsmith.calls = []
        p, s = write_files(tmp)
        r = run_apply(tmp, [str(p), "--state", str(s),
                            "--audit-log", audit])
        check("dry-run exits 0", r.returncode == 0, r.stderr)
        check("dry-run makes no HTTP calls", FakeFlagsmith.calls == [],
              "calls: %s" % FakeFlagsmith.calls)
        check("dry-run prints the PATCH",
              "PATCH" in r.stdout and "__future__" in r.stdout, r.stdout)
        check("dry-run prints before/after",
              "'variant_b': 10" in r.stdout and "'variant_b': 50" in r.stdout,
              r.stdout)
        rows = read_audit(tmp)
        check("dry-run appends audit verdict dry-run",
              len(rows) == 1 and rows[0]["verdict"] == "dry-run"
              and rows[0]["approver"] == "harsh",
              "audit: %s" % rows)

        # 2. dry-run with live creds: GET only, no PATCH
        FakeFlagsmith.calls = []
        r = run_apply(tmp, [str(p)] + live + ["--audit-log", audit])
        check("live dry-run exits 0", r.returncode == 0, r.stderr)
        check("live dry-run makes only a GET",
              len(FakeFlagsmith.calls) == 1
              and FakeFlagsmith.calls[0]["method"] == "GET",
              "calls: %s" % FakeFlagsmith.calls)
        check("live dry-run sends the Api-Key header",
              FakeFlagsmith.calls[0]["auth"] == "Api-Key test-key",
              "calls: %s" % FakeFlagsmith.calls)

        # 3. --approve with --state: one PATCH, audit approved, rollback
        FakeFlagsmith.calls = []
        r = run_apply(tmp, [str(p), "--state", str(s), "--approve"]
                      + live + ["--audit-log", audit])
        check("approve exits 0", r.returncode == 0, r.stderr)
        patches = [c for c in FakeFlagsmith.calls
                   if c["method"] == "PATCH"]
        check("approve sends exactly one PATCH", len(patches) == 1,
              "calls: %s" % FakeFlagsmith.calls)
        if patches:
            body = json.loads(patches[0]["body"])
            check("PATCH sets variant_b weight 50",
                  body == {"environment_default": {
                      "enabled": True,
                      "value": {"type": "string", "value": "Buy now"},
                      "variants": [{"id": 1, "weight": 50}]}},
                  "body: %s" % patches[0]["body"])
            check("PATCH targets the update-flag endpoint",
                  patches[0]["path"] == "/api/__future__/environments/"
                  "TEST_ENV/features/1/",
                  "path: %s" % patches[0]["path"])
        check("approve prints APPLIED", "APPLIED" in r.stdout, r.stdout)
        check("approve prints rollback",
              "rollback" in r.stdout and "'variant_b': 10" in r.stdout,
              r.stdout)
        rows = read_audit(tmp)
        check("approve appends audit verdict approved",
              rows and rows[-1]["verdict"] == "approved"
              and rows[-1]["via"] == "update-flag (__future__)",
              "audit: %s" % rows)

        # 4. gate BLOCK refusal: no HTTP, audit refused
        FakeFlagsmith.calls = []
        p2, _ = write_files(tmp, proposal=JUMP)
        r = run_apply(tmp, [str(p2), "--state", str(s), "--approve"]
                      + live + ["--audit-log", audit])
        check("gate BLOCK refused (exit 1)", r.returncode == 1,
              "exit %d" % r.returncode)
        check("refusal makes no PATCH (the state GET precedes the gate)",
              all(c["method"] != "PATCH" for c in FakeFlagsmith.calls),
              "calls: %s" % FakeFlagsmith.calls)
        check("refusal names the jump", "rollout jump" in r.stdout, r.stdout)
        rows = read_audit(tmp)
        check("refusal appends audit verdict refused",
              rows and rows[-1]["verdict"] == "refused"
              and "rollout jump" in " ".join(rows[-1]["reasons"]),
              "audit: %s" % rows)

        # 5. approval refusal: approver == author
        FakeFlagsmith.calls = []
        bad = CLEAN.replace("approver: harsh", "approver: aman")
        p3, _ = write_files(tmp, proposal=bad)
        r = run_apply(tmp, [str(p3), "--state", str(s), "--approve"]
                      + live + ["--audit-log", audit])
        check("self-approval refused (exit 1)", r.returncode == 1,
              "exit %d" % r.returncode)
        check("self-approval makes no PATCH",
              all(c["method"] != "PATCH" for c in FakeFlagsmith.calls),
              "calls: %s" % FakeFlagsmith.calls)
        rows = read_audit(tmp)
        check("self-approval appends audit verdict refused",
              rows and rows[-1]["verdict"] == "refused",
              "audit: %s" % rows)

        # 6. approval refusal: approved: false
        bad = CLEAN.replace("approved: true", "approved: false")
        p4, _ = write_files(tmp, proposal=bad)
        r = run_apply(tmp, [str(p4), "--state", str(s), "--approve"]
                      + live + ["--audit-log", audit])
        check("unapproved refused (exit 1)", r.returncode == 1,
              "exit %d" % r.returncode)

        # 6b. approval refusal: no author — an authorless proposal
        #     used to pass vacuously (approver != None). Now refused.
        FakeFlagsmith.calls = []
        bad = CLEAN.replace("author: aman\n", "")
        p5, _ = write_files(tmp, proposal=bad)
        r = run_apply(tmp, [str(p5), "--state", str(s), "--approve"]
                      + live + ["--audit-log", audit])
        check("authorless proposal refused (exit 1)", r.returncode == 1,
              "exit %d" % r.returncode)
        check("authorless proposal makes no PATCH",
              all(c["method"] != "PATCH" for c in FakeFlagsmith.calls),
              "calls: %s" % FakeFlagsmith.calls)
        rows = read_audit(tmp)
        check("authorless proposal appends audit verdict refused",
              rows and rows[-1]["verdict"] == "refused"
              and "no author" in (rows[-1].get("reason") or ""),
              "audit: %s" % rows)

        # 7. live mode without an API key -> exit 2
        r = run_apply(tmp, [str(p), "--env-key", "TEST_ENV",
                            "--feature-id", "1", "--audit-log", audit])
        check("no state and no key exits 2", r.returncode == 2,
              "exit %d" % r.returncode)

        # 8. wrong API key -> 401 -> exit 2, audit failed
        FakeFlagsmith.calls = []
        r = run_apply(tmp, [str(p), "--api-base", api_base,
                            "--api-key", "wrong-key", "--env-key",
                            "TEST_ENV", "--feature-id", "1",
                            "--option-ids", '{"variant_b": 1}',
                            "--approve", "--audit-log", audit])
        check("wrong key exits 2", r.returncode == 2, "exit %d" % r.returncode)
        rows = read_audit(tmp)
        check("wrong key appends audit verdict failed",
              rows and rows[-1]["verdict"] == "failed"
              and "401" in rows[-1]["error"],
              "audit: %s" % rows)

        # 9. server echoes wrong weights -> exit 2, audit failed
        # (restore the clean proposal: cases 4-6 overwrote the file)
        FakeFlagsmith.calls = []
        FakeFlagsmith.mismatch = True
        p, s = write_files(tmp)
        try:
            r = run_apply(tmp, [str(p), "--state", str(s), "--approve"]
                          + live + ["--audit-log", audit])
        finally:
            FakeFlagsmith.mismatch = False
        check("response mismatch exits 2", r.returncode == 2,
              "exit %d" % r.returncode)
        check("mismatch is reported",
              "server returned weights" in r.stdout + r.stderr,
              r.stdout + r.stderr)
        rows = read_audit(tmp)
        check("mismatch appends audit verdict failed",
              rows and rows[-1]["verdict"] == "failed",
              "audit: %s" % rows)

        # 10. flag moves between the gate check and the write -> exit 1,
        #     no PATCH (the pre-write re-read catches the race)
        FakeFlagsmith.calls = []
        FakeFlagsmith.state_change = True
        FakeFlagsmith.get_count = 0
        p, s = write_files(tmp)
        try:
            r = run_apply(tmp, [str(p), "--state", str(s), "--approve"]
                          + live + ["--audit-log", audit])
        finally:
            FakeFlagsmith.state_change = False
        check("state change exits 1", r.returncode == 1,
              "exit %d" % r.returncode)
        check("state change is reported",
              "state changed since the gate check" in r.stdout + r.stderr,
              r.stdout + r.stderr)
        check("state change makes no PATCH",
              all(c["method"] != "PATCH" for c in FakeFlagsmith.calls),
              "calls: %s" % FakeFlagsmith.calls)
        rows = read_audit(tmp)
        check("state change appends audit verdict refused",
              rows and rows[-1]["verdict"] == "refused"
              and "changed" in rows[-1].get("reason", ""),
              "audit: %s" % rows)

        # 11. --help: usage, no HTTP
        FakeFlagsmith.calls = []
        r = run_apply(tmp, ["--help"])
        check("--help exits 0", r.returncode == 0, r.stderr)
        check("--help prints usage", "usage:" in r.stdout, r.stdout)
        check("--help makes no HTTP calls", FakeFlagsmith.calls == [])

    server.shutdown()
    print("test_apply: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
