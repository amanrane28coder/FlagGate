# FlagGate — approval + audit for self-hosted Flagsmith (Toggle Wars, Track 3)

A flag change is a file, not a dashboard click. FlagGate gates it, a human
approves it, and only then does it touch Flagsmith — with every decision
written to an append-only audit log. Release Gate's pattern, applied to
Flagsmith flag changes on a self-hosted instance.

**Track 3: Build on Open Source Flagsmith — PS5 + GTA 6.**

## Live proof (verified Nov 27, 00:05 IST)

- **Apply, proven live** — flag `checkout-cta` (id 1) created via the
  Management API; variant `variant_b` ("Start free trial", option id 1).
  `apply.py --approve`: gate PASS, before {variant_b 0, control 100} →
  after {variant_b 50, control 50}, `APPLIED`. The published state was
  read back from the server before the audit line was written.
- **Demo flip, proven live** — 12 identities checked: 8 serve control
  ("Buy now"), 4 serve variant_b. Demo pair: alice + eve. Demo serves
  200 on :8003.
- **Tests: 92/92 pass** (VM + Mac). **One-command setup:**
  `docker-compose.yml` in this repo spins up the whole stack.
- **Not yet confirmed by a human click:** whether the sidebar audit-log /
  change-request entries ask for Enterprise (the gap premise).
  Flagsmith's own docs say they are Enterprise/Scale-Up-only.

## What it demonstrates

- **Use of Flagsmith** — a real self-hosted instance; the flag does real
  work end to end (create → gate → apply → server-verified 50/50 →
  per-identity SDK copy) through the update-flag endpoint.
- **Impact** — the gap is real: self-hosted Flagsmith ships no usable
  approval or audit trail, so any dashboard user can flip a flag to
  production with no record. FlagGate supplies both, without forking
  Flagsmith.
- **Creativity** — the approval is a file (proposal YAML), the gate is
  deterministic and testable, and the applier verifies what the server
  published before it audits — it refuses to audit a lie.
- **Demo video** — `VIDEO-SCRIPT.md`, ~2:10: cold-open flip → gap →
  gate BLOCK → apply evidence.
- **Reuse** — `docker-compose.yml` (sanitized, CHANGE-ME secrets),
  README, starter templates in `examples/`, 92/92 tests.

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
| **8002** | **Flagsmith — frontend + Management API** (`http://localhost:8002/`, health returns the Django "System status" page, API docs `/api/v1/docs/`) |
| 3100 / 5173 | **Rakazo, a different stack** — not Flagsmith. Do not create the admin there. |
| 8000 | **ExpAgent demo server** (Track 2) — `python -m http.server` in `~/expagent/demo-app`. Do not stop it. |

Or bring up your own instance from this repo — `docker-compose.yml` is a
sanitized copy of the official Flagsmith compose (same services, ports
8002/8001, experimentation on). Replace every `CHANGE-ME` value first,
then `docker compose up -d`; the admin, project and flag steps below
are still manual browser steps.

The live stack maps `8002:8000` (it was `8000:8000`, which collided with
the ExpAgent demo server, so the API was unreachable from the host until
the remap). After a Mac restart: `docker compose up -d` in
`~/flagsmith-local`, then check `http://localhost:8002/health`.

## One-command run

```sh
./tests/run_tests.sh          # full suite (yamlmini + gate + probe + apply + log + demo)
```

End-to-end on one proposal (dry-run — nothing touches Flagsmith).
`state-live.json` mirrors the live flag (50/50, Nov 27); `state-example.json`
is the probe's fixture (90/10) — do not edit it, the jump case depends on it:

```sh
python3 gate/gate.py examples/proposal-clean.yaml examples/state-live.json
python3 gate/probe.py                            # 7 adversarial + 1 clean proposal
python3 agent/apply.py examples/proposal-clean.yaml --state examples/state-live.json
python3 agent/log.py                             # every decision, one line each
```

Live apply (already run — see Live proof above):

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
request on v1 and v2 environments. There is no stable alternative: the
legacy `featurestates` PUT returns 400 on v2-versioned environments, so
the update-flag endpoint is the write path, not a shortcut. Auth is
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

## Live proof — proven on the self-hosted instance (Nov 27, 00:05 IST)

- **Apply path, proven live.** Flag `checkout-cta` (id 1) created via
  the Management API; variant `variant_b` ("Start free trial", option
  id 1). `apply.py examples/proposal-clean.yaml --approve`: gate PASS,
  before {variant_b 0, control 100} → after {variant_b 50, control 50},
  `APPLIED`. The published state was read back from the server
  (variants `[{id 1, weight 50}]`) before the audit line was written.
  The audit line is in `audit.log` (gitignored, local only).
- **Demo flip, proven live.** `demo-app/index.html` is wired to the
  Development environment key and the flag, serving 200 on :8003.
  12 identities checked against the SDK API: 8 serve control
  ("Buy now"), 4 serve variant_b ("Start free trial"). Demo pair:
  alice (control) + eve (variant_b).
- **What is also verified live:** the stack is up (API + frontend on
  :8002), the update-flag endpoint exists in this build's schema with
  the exact request shape the applier sends, and the API enforces auth
  (401 without a key). The gate, probe runner, applier and log are all
  green: 92/92 tests on VM + Mac.
- **Not yet confirmed by a human click:** whether the sidebar
  audit-log / change-request entries ask for Enterprise (the gap
  premise). Flagsmith's own docs say they are Enterprise/Scale-Up-only;
  the click is the last confirmation.

## Setup — 5 minutes in the browser

At `http://localhost:8002/` (NOT :5173 — that is Rakazo):

1. **Create the admin account** — first visit offers account creation.
   Any admin login works.
2. **Create 1 project** (OSS self-hosted allows 1 project per
   organisation) — name it `flaggate`.
3. **Create 1 flag** in the project's Development environment —
   name `checkout-cta`, type multivariate, default value `Buy now`,
   add variant `variant_b` with value `Start free trial`, weight 0.
   (Done Nov 27: flag id 1, variant_b option id 1. `control` is a
   reserved variant key — control is the default value plus the
   unallocated weight, and the applier already handles that.)
4. **Confirm the gap** — look at the sidebar: audit log / change
   requests are shown but license-gated (clicking one asks for
   Enterprise). That is the gap FlagGate fills; note what you see.
5. **Copy two keys** — the environment key (Development environment
   settings) and a management API key from **Organisation Settings →
   API Access** (NOT Account Settings → API keys: that key is rejected
   by the Management API on both self-hosted and Cloud). Paste both
   into the live-apply block below — never into the repo.

Live proof — already run (Nov 27, 00:05 IST):

```sh
cd ~/flaggate
python3 agent/apply.py examples/proposal-clean.yaml \
  --api-key <management API key> --env-key <environment key> \
  --feature-id 1 --option-ids '{"variant_b": 1}' --approve
python3 agent/log.py
```

Result: `APPLIED`, the demo app's button flips for the variant
identity, and `log.py` shows the approved line. If the PATCH 401s, the
API key is wrong (it must be the Organisation API token, step 5); if
it 404s, the flag id is wrong.

## Demo app (demo-app/)

A static checkout page modelled on ExpAgent's demo, pointing at the
self-hosted instance. Serve it:

```sh
cd demo-app && python3 -m http.server 8003
# http://localhost:8003/?identity=alice
```

It reads the flag for the identity in the URL and shows the variant
copy on the button. It is wired to the Development environment key and
the `checkout-cta` flag, and the flip is proven live: alice → control
("Buy now"), eve → variant_b ("Start free trial"). Try
`http://localhost:8003/?identity=alice` vs `?identity=eve`.

## Video script

Full shot list + narration: `VIDEO-SCRIPT.md` (~2:10, cold-open flip,
gap, gate BLOCK, apply evidence, close). Record silent screen
(Cmd+Shift+5, mic OFF) + separate voiceover; merge with ffmpeg. Do not
run the agent live on camera — replay the verified run. Do NOT re-run
`apply.py --approve` on camera (duplicate audit line) and do NOT flip
the flag in the dashboard (breaks the live 50/50).

## Files

```
gate/gate.py         proposal YAML + flag state -> PASS / BLOCK / error
gate/yamlmini.py     strict YAML-subset parser (stdlib has no YAML)
gate/probe.py        7 adversarial + 1 clean proposal through the real gate
agent/apply.py       human gate + update-flag PATCH + audit + rollback
agent/log.py         pretty-print audit.log, one decision per line
examples/            proposal-clean.yaml, proposal-jump.yaml,
                     proposal-killswitch.yaml, state-example.json
                     (probe fixture, 90/10), state-live.json (live, 50/50)
demo-app/index.html  static checkout page on the self-hosted instance
docker-compose.yml   sanitized official Flagsmith compose (CHANGE-ME secrets)
VIDEO-SCRIPT.md      video shot list + narration (~2:10)
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
