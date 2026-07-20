#!/usr/bin/env python3
"""review_gate_hook.py — STOP hook: early warning that a review went stale.

Fires when the model tries to END its turn. Scans the ship root for skills whose
attestation no longer matches their content — i.e. a full review passed, and then
the content was EDITED, so the pass no longer covers what is on disk. It blocks
the stop once and says so.

THIS HOOK IS NOT THE GATE. The gate is `review_gate.py check`, invoked by the
PreToolUse attestation_guard on a ship and by the pre-push git hook. Shipping an
unattested or drifted skill is DENIED there, fail-closed, regardless of what this
hook does. This hook only surfaces drift early, while you can still act on it.

Which states nudge (env REVIEW_GATE_NUDGE_STATES, default "stale"):
  * stale   — a pass existed and the content drifted. Actionable, transient,
              and caused by something you just did. Worth interrupting for.
  * missing — never reviewed. This is the normal state of a skill under
              construction and does NOT clear until a full review passes, so
              nudging on it fires every single turn for every unbuilt skill.
              An alarm that always sounds gets switched off, taking the drift
              signal with it. Off by default; the ship-block still denies it.

Contract (mirrors ../hooks/gate.py exactly):
  * FAIL-OPEN. A Stop hook that denied on error would TRAP the user in a turn
    they cannot end. So: any parse/scan/import error -> ALLOW + a loud stderr
    note. (Contrast: review_gate.py's `check` GATE fails LOUD/closed.)
  * NUDGE ONCE PER TURN. Honors `stop_hook_active`: the first pending stop is
    blocked (one strong reminder); if the model stops again in the same
    continuation, we ALLOW (never trap) but warn.
  * STDLIB ONLY — runs under bare system python3.
  * Silent (exit 0, no output) when clean -> normal stop proceeds.

Ship root = env REVIEW_SHIP_ROOT, else the Stop event's cwd. If the root IS
itself a skill (you are working inside one), that skill is what gets scanned.
"""
import sys, os, json

DEFAULT_NUDGE_STATES = ("stale",)

# Nudge ONCE PER CONTENT HASH, not once per turn.
#
# The first cut of this hook nudged every turn a skill was stale. That was wrong,
# and rca proved it within the hour: a stale skill CANNOT become un-stale until a
# full review passes, which can take days of fixing. "Drift is transient and
# actionable" is true of the *event*, not of the *state* — so a per-turn nudge on a
# state fires forever. An alarm that always sounds gets switched off, and it takes
# the real protections with it (see the module docstring).
#
# So we remember the hash we last nudged for. A new nudge fires only when the
# content CHANGES again (a genuinely new drift event) or when a different skill
# drifts. Standing still in a known-stale tree is silent. This weakens nothing:
# the nudge was never the gate. Shipping a stale skill is still denied, fail-closed,
# by the PreToolUse attestation_guard and by pre-push.
STATE_PATH = os.path.expanduser(
    os.environ.get("REVIEW_GATE_NUDGE_STATE", "~/.claude/.review-gate-nudged.json"))


def nudge_states():
    raw = os.environ.get("REVIEW_GATE_NUDGE_STATES", "")
    states = tuple(s.strip() for s in raw.split(",") if s.strip())
    return states or DEFAULT_NUDGE_STATES


def _load_seen():
    """{skill_dir: last_nudged_hash}. Unreadable -> {} so we nudge rather than miss."""
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_seen(seen):
    """Best-effort. A failure here must never block or crash the stop."""
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(seen, f)
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        warn("could not record nudge state (%s); will nudge again next turn." % e)


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)

def allow():
    sys.exit(0)                                         # silent -> stop proceeds

def warn(msg):
    print("[review-gate] " + msg, file=sys.stderr)


def _import_lib():
    """Import review_gate from this hook's own directory (no PYTHONPATH assumed)."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import review_gate                                  # noqa: E402
    return review_gate


def scan_pending(rg, root, states=None):
    """Return [(name, state, skill_dir, content_hash)] for skills we nudge on.

    Scans the skills under `root`, and `root` itself when you are standing inside
    a skill (otherwise working in skills/rca/ would find nothing to check).
    """
    states = states or nudge_states()
    candidates = list(rg.find_skills(root))
    if rg.is_skill_dir(root) and root not in candidates:
        candidates.append(root)
    pending = []
    for skill_dir in candidates:
        state = rg.attestation_state(skill_dir)         # missing | stale | attested-current
        if state in states:
            pending.append((os.path.basename(skill_dir.rstrip(os.sep)), state,
                            os.path.abspath(skill_dir), rg.compute_artifact_hash(skill_dir)))
    return pending


def unseen(pending, seen):
    """Drop skills already nudged at their CURRENT content hash."""
    return [p for p in pending if seen.get(p[2]) != p[3]]


def reason_text(pending):
    shown = ", ".join("%s (%s)" % (n, s) for n, s, _d, _h in pending[:8])
    more = "" if len(pending) <= 8 else " (+%d more)" % (len(pending) - 8)
    return ("Review attestation STALE — %d skill(s) passed a full review and have been "
            "edited since: %s%s. The pass no longer covers what is on disk, and the "
            "ship-block will refuse them. Re-run the adversarial review on the current "
            "content, then `review_gate.py attest --from-result <review.json>` to re-key "
            "the attestation. (Counts are derived from the review's findings; they are "
            "not yours to type.)\nThis fires once per content change, not once per turn — "
            "you will not see it again until these files change." % (len(pending), shown, more))


def main():
    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
    except Exception:
        warn("could not parse Stop event JSON; allowing (fail-open).")
        allow()

    root = os.environ.get("REVIEW_SHIP_ROOT") or event.get("cwd") or os.getcwd()

    try:
        rg = _import_lib()
    except Exception as e:
        warn("could not import review_gate (%s); allowing (fail-open)." % e)
        allow()

    try:
        pending = scan_pending(rg, root)
    except Exception as e:
        warn("scan error (%s); allowing (fail-open)." % e)
        allow()

    if not pending:
        allow()

    # Already nudged for exactly these bytes? Then this is the same drift event we
    # already reported, not a new one. Stay silent — the ship-block still refuses it.
    try:
        seen = _load_seen()
        fresh = unseen(pending, seen)
    except Exception as e:
        warn("nudge-state error (%s); nudging anyway." % e)
        fresh = pending

    if not fresh:
        warn("review gate still stale for %d skill(s), already reported at this content "
             "hash; staying quiet. The ship-block still refuses them." % len(pending))
        allow()

    # gate is dirty
    if event.get("stop_hook_active"):
        # already nudged this continuation chain — never trap the session
        warn("review gate STILL pending (%d) after a prior nudge; allowing to avoid a loop."
             % len(fresh))
        allow()

    for _n, _s, skill_dir, content_hash in fresh:
        seen[skill_dir] = content_hash
    _save_seen(seen)
    block(reason_text(fresh))


if __name__ == "__main__":
    main()
