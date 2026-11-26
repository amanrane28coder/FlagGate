# FlagGate — video script (~2 min, target ~2:10, hard cap 3 min)

Method: silent screen (Cmd+Shift+5, mic OFF) + separate voiceover, merge:
`ffmpeg -i screen.mp4 -i voice.m4a -c:v copy -c:a aac -shortest flaggate-demo.mp4`.
Upload unlisted to YouTube; link needed before Sun Nov 29, 20:00 IST.

Story (the Dibs lesson): the interesting part isn't the flag flip — it's
that no change *through FlagGate* reaches production without a human
approving it and a record existing. Self-hosted Flagsmith ships neither;
FlagGate supplies both without forking Flagsmith. (A dashboard flip still
bypasses FlagGate — it is a process tool, not a lock; say so if asked.)

## Shots

### 1. Cold open — the flip (0:00–0:12, first 8s matter)
Two tabs side by side: `localhost:8003/?identity=alice` shows **Buy now**,
`?identity=eve` shows **Start free trial**. No narration yet, or one line:
"Same page, same flag, two different buttons. That's Flagsmith doing its
job. The question is: who decided this change — and where's the record?"

Demo server must be up: if :8003 is dead, restart it first:
`cd ~/flaggate/demo-app && nohup python3 -m http.server 8003 > /tmp/flaggate-demo.log 2>&1 &`

### 2. Problem — the gap (0:12–0:35)
Dashboard sidebar at localhost:8002: **Audit Log** and **Feature Change
Requests**. Click one — expected: it asks for Enterprise. Narration:
"This is self-hosted Flagsmith. It has no change approval and no audit
log — those are Enterprise-only. Anyone with dashboard access can flip
a flag straight to production, and there is no record of who did it, who
approved it, or what the value was before."
Say "no *usable* approval or audit trail", never "the buttons don't exist"
— they exist, they are license-gated. **Do the click before recording;
if it shows something else, say what it shows — the narration above is
the expected outcome, not yet confirmed.**

### 3. Gate BLOCK (0:35–1:00)
`python3 gate/probe.py` live — safe, local, no writes. The 10% → 100%
jump proposal BLOCKs with the reason shown; 8/8 probes as expected.
Narration: "FlagGate puts a gate in front of every change. A proposal is
a file. The gate checks it — weights, rollout jumps, kill-switches, stale
flags — and a bad change stops here, before it touches anything."

### 4. Approve + apply evidence (1:00–1:30)
Do NOT re-run `apply.py --approve` on camera — the 50/50 is already live
and a re-run appends a duplicate audit line. Instead, in this order:
- `python3 agent/log.py` FIRST — the approved line from the live run:
  `APPROVED checkout-cta, author=aman approver=harsh, before
  [variant_b:0, control:100] after [variant_b:50, control:50]`.
  (A dry-run appends its own audit line, so log.py must come before it.)
- Dry-run apply (no `--approve`): set the key off-camera first —
  `export FLAGATE_API_KEY=<org token>` (never type the real key on
  screen; apply.py reads it from the environment) — then
  `python3 agent/apply.py examples/proposal-clean.yaml --env-key <env-key>
  --feature-id 1 --option-ids '{"variant_b": 1}'` — prints gate PASS +
  before/after, writes nothing.
- Flag state: dashboard or API showing variant_b at weight 50.
Narration: "A clean change through FlagGate still needs a human who isn't
the author. The applier re-runs the gate, checks the approval, writes
through the API, verifies what the server published, and only then writes
the audit line. That 50/50 went live with a record."

### 5. Flip + audit (1:30–1:50)
Back to the two demo tabs (alice / eve), then the log line once more.
Narration: "The same flag, two users, two buttons — and one line in the
log saying who approved it and what changed."

### 6. Close (1:50–2:10)
Repo on screen: `github.com/amanrane28coder/FlagGate`. Narration:
"FlagGate. One docker compose up spins up the stack — then five manual
browser steps (admin, project, flag, two keys) in the README. The gate,
the approval, and the audit trail self-hosted Flagsmith doesn't ship."
Mention 104/104 tests in one line if it fits, or as an on-screen label.

## Do NOT on camera

- Re-run `apply.py --approve` (duplicate audit line).
- Flip the flag in the dashboard (breaks the live 50/50).
- Delete the flag.
- Claim the audit-log / change-request entries don't exist — they exist
  but are license-gated; the claim is "no usable approval or audit
  trail".
- Claim traffic results or a winner — FlagGate is not an experiment;
  the 50/50 is a rollout split, not measured results.
- Present anything as measured that isn't (no manual timings here).

Terminal font 16–18pt for gate/probe/audit shots. Optional one-word
text labels per shot.
