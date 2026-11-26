#!/usr/bin/env python3
"""FlagGate log: pretty-print the audit log, one decision per line.

The audit log is FlagGate's answer to the missing audit trail on
self-hosted Flagsmith: every proposal decision — dry-run, approved,
refused, failed — is one JSON line in audit.log (see agent/apply.py).
This file reads it back for a human.

Usage:
    python3 agent/log.py [--audit-log FILE]

Exit codes: 0 ok (including an empty/missing log), 2 error.
"""

import json
import sys


def fmt_weights(weights):
    if not isinstance(weights, dict) or not weights:
        return "-"
    return ", ".join("%s:%s" % (k, v) for k, v in weights.items())


def fmt_entry(e):
    ts = e.get("ts", "?")
    who = e.get("who", "?")
    action = e.get("action", "?")
    verdict = str(e.get("verdict", "?")).upper()
    flag = e.get("flag", "?")
    author = e.get("author", "?")
    approver = e.get("approver", "?")
    before = e.get("before") or {}
    after = e.get("after")
    line = ("%s  %-12s %-6s %-8s %-16s author=%s approver=%s"
            % (ts, who, action, verdict, flag, author, approver))
    if isinstance(before, dict) and before:
        line += "  before=[%s]" % fmt_weights(before.get("weights"))
    if isinstance(after, dict) and after:
        line += "  after=[%s]" % fmt_weights(after.get("weights"))
    elif after is None and e.get("verdict") in ("refused", "failed"):
        line += "  after=[no change]"
    if e.get("reasons"):
        line += "  reasons: %s" % "; ".join(e["reasons"])
    if e.get("reason") and e.get("verdict") == "refused":
        line += "  (%s)" % e["reason"]
    if e.get("error"):
        line += "  error: %s" % e["error"]
    return line


def main(argv):
    args = argv[1:]
    audit_log = "audit.log"
    if "--audit-log" in args:
        i = args.index("--audit-log")
        if i + 1 < len(args):
            audit_log = args[i + 1]
    try:
        with open(audit_log) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        print("no decisions yet (no %s)" % audit_log)
        return 0
    shown = 0
    bad = 0
    for raw in lines:
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            bad += 1
            print("warning: skipping malformed audit line: %r"
                  % raw[:120])
            continue
        if not isinstance(entry, dict):
            bad += 1
            print("warning: skipping non-object audit line: %r"
                  % raw[:120])
            continue
        print(fmt_entry(entry))
        shown += 1
    print("%d decision(s)%s" % (shown,
                                (", %d malformed line(s) skipped" % bad)
                                if bad else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
