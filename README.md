# FlagGate

Approval + audit for self-hosted Flagsmith flag changes. The open-source
self-hosted edition has no change requests or audit logs — FlagGate supplies
both, without forking Flagsmith.

Track 3 — Build on Open Source Flagsmith, Toggle Wars (WeMakeDevs × Flagsmith),
Nov 26–29, 2026. Team Void, solo.

## How it works

1. **Proposed-change file (YAML)** — flag, environment, new value / weights /
   rollout %, author, reason. A change is a file, not a dashboard click.
2. **Gate** — deterministic checks. BLOCK on weights ≠ 100, a rollout jump
   > 50 pp, a kill-switch turned off, a prod change with a staging-only flag,
   a stale flag (> 90 days without evaluation or change).
3. **Approve + apply + log** — a human approves (not the author); only then
   does the applier call the Flagsmith API, and append one line per decision
   to an append-only audit log (who, what, verdict, timestamp, before/after).

## Run

One `docker compose up` for Flagsmith + FlagGate. (Wiring added during hacking, Nov 26.)

## Pre-work (done)

- Self-hosted Flagsmith up on the Mac (~/flagsmith-local): API :3100 = 200,
  web :5173 = 200. Admin account + gap check pending in the browser.
