# FlagGate — approval + audit for self-hosted Flagsmith (Toggle Wars, Track 3)

A flag change is a file, not a dashboard click. FlagGate gates it, a human
approves it, and only then does it touch Flagsmith — with every decision
written to an append-only audit log. Release Gate's pattern, applied to
Flagsmith flag changes on a self-hosted instance.

**Track 3: Build on Open Source Flagsmith — PS5 + GTA 6.** Judging:
self-hosted Flagsmith; integrations and plugins; developer tools; easy
for others to reuse.

## The gap

The open-source self-hosted edition of Flagsmith does not include change
requests or audit logs — Flagsmith's own self-hosted page says the OSS
version "doesn't include security and management features like SAML,
change requests, audit logs, and more". Those are Enterprise/Scale-Up
features. So on a self-hosted instance, anyone with dashboard access can
flip a flag straight to production and there is no record of who did it,
who approved it, or what the previous value was.

Nuance, stated honestly: the OSS dashboard sidebar may *show* audit-log /
change-request entries, but they are license-gated and unusable without
Enterprise. FlagGate does not claim the buttons don't exist — it claims
there is no *usable* approval or audit trail on the free self-hosted
build, and supplies both externally, without forking Flagsmith.

## What it does

```
proposal (YAML) ──► gate (gate/gate.py) ──► human approves (approver ≠ author)
                                                            │
                                                            ▼
                                              apply (agent/apply.py --approve)
                                              PATCH via the update-flag API
                                              + audit log line + rollback
                                                            │
                                                            ▼
                                              log (agent/log.py)
                                              every decision, one line each
```

The gate is deterministic and testable. The applier re-runs the gate
itself before every write, checks the human approval, and verifies the
published state from the PATCH response before it audits. Zero autonomous
production changes: without a human approver who is not the author,
nothing is applied.

## Self-hosted instance — read this first

The Flagsmith stack runs on the Mac in `~/flagsmith-local`
(`docker compose up -d`). The ports changed during the build, because
the original note was wrong:

| Port | What it actually is |
|------|---------------------|
| **8002** | **Flagsmith — frontend + Management API** (`http://localhost:8002/`, health `{"ok":true}`, API docs `/api/v1/docs/`) |
| 3100 / 5173 | **Rakazo, a different stack** — not Flagsmith. Do not create the admin there. |
| 8000 | **ExpAgent demo server** (Track 2) — `python -m http.server` in `~/expagent/demo-app`. Do not stop it. |

The Flagsmith container maps `8002:8000` in `~/flagsmith-local/docker-compose.yml`
(it was `8000:8000`, which collided with the ExpAgent demo server, so the
API was unreachable from the host until the remap). After a Mac restart:
`docker compose up -d` in `~/flagsmith-local`, then check
`http://localhost:8002/health`.

## One-command run

```sh
./tests/run_tests.sh          # full suite (yamlmini + gate + probe + apply + log + demo)
```

End-to-end on one proposal (dry-run — nothing touches Flagsmith):

```sh
python3 gate/gate.py examples/proposal-clean.yaml examples/state-example.json
python3 gate/probe.py                            # 7 adversarial + 1 clean proposal
python3 agent/apply.py examples/proposal-clean.yaml --state examples/state-example.json
python3 agent/apply.py examples/proposal-clean.yaml --state examples/state-example.json --approve  # needs live credentials (below)
python3 agent/log.py                             # every decision, one line each
```

Live apply (after the admin step below):

```sh
python3 agent/apply.py examples/proposal-clean.yaml \
  --api-key <management API key> --env-key <environment key> \
  --feature-id <flag id> --option-ids '{"variant_b": <option id>}' --approve
```

## The gate (gate/gate.py — rules are frozen)

BLOCK (exit 1) on:

1. **Variant weights don't sum to 100.**
2. **Rollout jump > 50pp** vs the flag's current weights (e.g. 10% → 100%).
3. **Turning off a kill-switch / safety flag.**
4. **The global kill-switch is off** — no changes while the safety net is down.
5. **Environment / flag-name mismatch** — a staging-only flag in production,
   or a production-only flag outside production.
6. **Stale flag** — no evaluation or change in > 90 days. BLOCK as a
   cleanup flag, never auto-delete. Skipped with a printed note when the
   state carries no evaluation timestamps (the live API state has none);
   skipped is not a pass.

PASS (exit 0) otherwise, with a written recommendation only — the gate
approves nothing itself. Exit 2 is an error (unreadable file, malformed
YAML/JSON).

## The human gate (agent/apply.py)

The proposal names an `author` and an `approver`, and carries
`approved: true`. The applier refuses (exit 1, audited as `refused`) unless
all three hold: the proposal is approved, an approver is named, and the
approver is not the author. The reviewer recommends only; the approver
logs the decision — the Release Gate roles, verbatim.

## The write path (agent/apply.py)

The write goes through Flagsmith's experimental update-flag endpoint
(`PATCH api/__future__/environments/<env-key>/features/<feature-id>/`),
verified present in this build's own OpenAPI schema
(`http://localhost:8002/api/v1/swagger.json`): `UpdateFlagRequest` takes
`environment_default` with `enabled`, `value` (`{type, value}`) and
`variants` (`[{id, weight}]`). It is versioning-agnostic — the same
request on v1 and v2 environments. Auth is
`Authorization: Api-Key <management API key>` (Flagsmith's own docs; the
local schema declares no security scheme, and the API 401s without a key).
The environment key in the URL is the SDK key for the environment; the
API key is the management key from the dashboard. They are different keys.

The applier reads the flag's current state with the update-flag GET
(fresh weights/value/enabled), re-runs the gate, checks the human
approval, then PATCHes. It verifies the published state from the PATCH
response — weights, value, enabled — and refuses to audit a lie: a
mismatch exits 2 with verdict `failed`. The rollback is the previous
weights/value as a PATCH, printed on every apply.

State sources: `--state` (a JSON fixture — the only source the gate's
kill-switch lists, staging lists and stale timestamps can come from),
live credentials (`--api-key` + `--env-key` + `--feature-id` +
`--option-ids`), or both (fixture metadata + fresh API values).

## Honest status — what is NOT yet proven live

- **The apply path is NOT yet live-proven.** No admin, project, flag or
  API token exists on the self-hosted instance yet (creating them is a
  browser step — below). The applier is built and fake-tested (a local
  HTTP server stands in for the Management API, including a 401 case and
  a response-mismatch case), but no real PATCH has run against the
  self-hosted instance. The README will say so until it has.
- **What IS verified live:** the stack is up (API + frontend on :8002),
  the update-flag endpoint exists in this build's schema with the exact
  request shape the applier sends, and the API enforces auth (401
  without a key). The gate, probe runner, applier (fake-tested) and log
  are all green: 92/92 tests on VM + Mac.
- **The demo flip is NOT yet live.** `demo-app/index.html` points at the
  self-hosted API (`http://localhost:8002/api/v1/`) but carries
  `REPLACE_WITH_ENV_KEY` / `REPLACE_WITH_FLAG_NAME` placeholders until
  the admin step creates them. It serves; the flip waits on the admin step.

## Admin step — 5 minutes in the browser (Aman)

At `http://localhost:8002/` (NOT :5173 — that is Rakazo):

1. **Create the admin account** — first visit offers account creation.
   Use the same Google account as the Cloud project if offered
   (`rane.aman28@gmail.com`); otherwise any admin login works.
2. **Create 1 project** (OSS self-hosted allows 1 project per
   organisation) — name it `flaggate`.
3. **Create 1 flag** in the project's Development environment —
   name `checkout-cta`, type multivariate, default value `Buy now`,
   add variant `variant_b` with value `Start free trial`, weight 0.
   Note the flag id (from the URL) and the variant_b option id.
4. **Confirm the gap** — look at the sidebar: audit log / change
   requests are shown but license-gated (clicking one asks for
   Enterprise). That is the gap FlagGate fills; note what you see.
5. **Copy two keys** — the environment key (Development environment
   settings) and a management API key (Account Settings → API keys, or
   the organisation settings). Paste both into the live-apply block
   below — never into the repo.

Then run the live proof (Saturday):

```sh
cd ~/flaggate
python3 agent/apply.py examples/proposal-clean.yaml \
  --api-key <management API key> --env-key <environment key> \
  --feature-id <flag id> --option-ids '{"variant_b": <option id>}' --approve
python3 agent/log.py
```

Expected: `APPLIED`, the demo app's button flips for the variant
identity, and `log.py` shows the approved line. If the PATCH 401s, the
API key is wrong; if it 404s, the flag id is wrong.

## Demo app (demo-app/)

A static checkout page modelled on ExpAgent's demo, pointing at the
self-hosted instance. Serve it:

```sh
cd demo-app && python3 -m http.server 8003
# http://localhost:8003/?identity=alice
```

It reads the flag for the identity in the URL and shows the variant
copy on the button. Until the placeholders are filled it shows
"Not configured yet" — that is honest, not broken. After the admin step,
fill in `ENV_KEY` + `FLAG_NAME` in `demo-app/index.html` and the flip
works: alice → control, carol → variant_b (or whatever the rollout is).

## Video outline (~2 min)

Record silent screen (Cmd+Shift+5, mic OFF) + separate voiceover; merge
with ffmpeg. Do not run the agent live on camera — replay the verified run.

- 0:00 Problem: self-hosted Flagsmith has no change approval or audit
  log — show a flag flipped straight in the dashboard with no record.
- 0:30 Gate: submit a bad change (rollout 10% → 100%) — BLOCK, reason shown.
- 1:00 Human approves a clean change in the log; applier applies it;
  demo app behaviour flips.
- 1:30 Audit log: every decision listed, before/after values.
- 1:50 Close: "FlagGate. One docker compose up. Repo on screen."

## Files

```
gate/gate.py         proposal YAML + flag state -> PASS / BLOCK / error
gate/yamlmini.py     strict YAML-subset parser (stdlib has no YAML)
gate/probe.py        7 adversarial + 1 clean proposal through the real gate
agent/apply.py       human gate + update-flag PATCH + audit + rollback
agent/log.py         pretty-print audit.log, one decision per line
examples/            proposal-clean.yaml, proposal-jump.yaml,
                     proposal-killswitch.yaml, state-example.json
demo-app/index.html  static checkout page on the self-hosted instance
tests/               run_tests.sh + test_yamlmini.py, test_gate.py,
                     test_probe.py, test_apply.py, test_log.py, test_demo.py
audit.log            appended by apply.py runs (gitignored, never committed)
```

## Tests

`./tests/run_tests.sh` — 92 checks, all real subprocess runs. The applier
is tested against a tiny stdlib HTTP server that stands in for the
Management API (records requests, enforces the Api-Key header, echoes
weights back, and can be told to echo wrong weights — the applier must
refuse to audit that). The gate and probe run against real YAML files
and real state JSON. No mocks of the code under test.
