#!/usr/bin/env python3
"""FlagGate audit chain: tamper-evident append-only audit log.

The audit log is FlagGate's answer to the missing audit trail on
self-hosted Flagsmith — so the log itself must not be silently editable.
Every appended line carries two extra fields:

  "prev": hash of the previous line (or "GENESIS" for the first chained
          line; for a line appended after a legacy pre-chain line, the
          SHA-256 of that line's raw bytes)
  "hash": SHA-256 of prev + the canonical JSON of the entry (all fields
          except "hash" and "prev", sorted keys, compact separators)

`append_chained` writes one line. `verify_lines` replays a list of raw
lines and reports where the chain breaks, if anywhere. A line without
"hash"/"prev" is a legacy line (written before chaining): it is shown
as unverified, and the next chained line hashes its raw bytes, so the
legacy line is still anchored into the chain.

This is tamper-EVIDENCE, not tamper-proofing: anyone with write access
to the file can rewrite the whole chain. What it gives is detection —
an edited, deleted or reordered line breaks the chain at a named line
number, and `log.py` exits 1 instead of printing a clean trail. A line
that is not parseable JSON is skipped by the chain check (log.py still
warns about it loudly) — the chain catches silent edits to valid
lines; garbage is loud on its own.

Stdlib only.
"""

import hashlib
import json

GENESIS = "GENESIS"


def canonical(entry):
    """Canonical JSON of an entry without its chain fields."""
    body = {k: v for k, v in entry.items() if k not in ("hash", "prev")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def entry_hash(prev, entry):
    return hashlib.sha256((prev + canonical(entry)).encode()).hexdigest()


def _last_prev(path):
    """The prev value for the next appended line: the last line's hash,
    the SHA-256 of a legacy last line's raw bytes, or GENESIS."""
    try:
        with open(path, "rb") as f:
            raw_lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return GENESIS
    if not raw_lines:
        return GENESIS
    last = raw_lines[-1]
    try:
        entry = json.loads(last.decode())
    except (ValueError, UnicodeDecodeError):
        return hashlib.sha256(last).hexdigest()
    if isinstance(entry, dict) and entry.get("hash"):
        return entry["hash"]
    return hashlib.sha256(last).hexdigest()


def append_chained(path, entry):
    """Append one entry with its chain fields. Returns the entry."""
    prev = _last_prev(path)
    entry["prev"] = prev
    entry["hash"] = entry_hash(prev, entry)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def verify_lines(raw_lines):
    """Replay raw audit lines. Returns
    (ok, broken_index, legacy_count, chained_count):
      ok: True when every chained line's prev + hash check out
      broken_index: 0-based index of the first broken line, or None
      legacy_count: lines without chain fields (shown, unverified)
      chained_count: lines with chain fields"""
    expected_prev = GENESIS
    legacy = 0
    chained = 0
    for i, raw in enumerate(raw_lines):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            # Unparseable: skipped here (log.py warns about it); the
            # chain covers valid lines, garbage is loud on its own.
            continue
        if not isinstance(entry, dict):
            continue
        if not entry.get("hash"):
            # Legacy line: unverified, but it anchors the next line.
            legacy += 1
            expected_prev = hashlib.sha256(raw.encode()).hexdigest()
            continue
        chained += 1
        if entry.get("prev") != expected_prev:
            return False, i, legacy, chained
        if entry["hash"] != entry_hash(entry["prev"], entry):
            return False, i, legacy, chained
        expected_prev = entry["hash"]
    return True, None, legacy, chained
