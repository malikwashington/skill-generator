#!/usr/bin/env python3
"""gate.py — portable STOP hook: un-skippable critique/QA gate.

Fires when the model tries to END its turn. Scans the project's per-item
records for any "built but not yet critiqued" state; if found, BLOCKS the
stop and tells the model to run the critique gate first. This makes the
critique gate impossible to silently skip (the class-f "model forgot the
gate" failure).

Contract (see DESIGN.md):
  * FAIL-OPEN, unlike the PreToolUse guard. A Stop hook that denied on error
    would TRAP the user in a turn they cannot end — worse than missing one
    gate check. So: any parse/config/scan error -> ALLOW the stop + a loud
    stderr note.
  * NUDGE ONCE PER TURN. Honors `stop_hook_active`: the first stop with a
    skipped gate is blocked (one strong reminder); if the model ignores it
    and stops again in the same continuation, we ALLOW (never trap) but warn.
  * STDLIB ONLY, JSON config — runs under bare system python3.
  * Silent (exit 0, no output) when the gate is clear -> normal stop proceeds.

Records-scan is CWD-relative (globs resolved against the Stop event's cwd),
so one user-global wiring covers whichever project is active.
"""
import sys, os, json, glob


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)

def allow():
    sys.exit(0)                                     # silent -> stop proceeds

def warn(msg):
    print("[gate] " + msg, file=sys.stderr)


def load_config():
    path = os.environ.get("GATE_CONFIG") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "gate.config.json")
    if not os.path.exists(path):
        return None, "no config at %s" % path
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        return None, "parse error: %s" % e
    if not isinstance(data, dict):
        return None, "config is not a JSON object"
    return data, None


def scan(cfg, base):
    """Return list of dicts: {gate, id, remediation} for pending records."""
    pending = []
    for g in (cfg.get("gates") or []):
        if not isinstance(g, dict):
            continue
        pat = os.path.join(base, g.get("records_glob", "records/*.json"))
        field = g.get("status_field", "status")
        statuses = set(g.get("pending_statuses", []) or [])
        remediation = g.get("remediation", "(run the critique gate)")
        name = g.get("name", "critique gate")
        for f in glob.glob(pat):
            try:
                rec = json.load(open(f))
            except Exception:
                continue                            # unreadable record: skip, never trap
            if isinstance(rec, dict) and rec.get(field) in statuses:
                pending.append({"gate": name, "id": os.path.basename(f),
                                "remediation": remediation})
    return pending


def reason_text(pending):
    by_rem = {}
    for p in pending:
        by_rem.setdefault(p["remediation"], []).append(p)
    lines = ["Critique gate not cleared — %d artifact(s) were built but never critiqued. "
             "Nothing ships without the critique gate." % len(pending)]
    for rem, items in by_rem.items():
        shown = ", ".join(i["id"] for i in items[:5])
        more = "" if len(items) <= 5 else " (+%d more)" % (len(items) - 5)
        lines.append("  %s [%s]: %s%s" % (items[0]["gate"], rem, shown, more))
        lines.append("  -> run: %s" % rem)
    lines.append("If you are intentionally pausing, run the critique or this reminds you once per turn.")
    return "\n".join(lines)


def main():
    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
    except Exception:
        warn("could not parse Stop event JSON; allowing (fail-open).")
        allow()

    base = event.get("cwd") or os.getcwd()

    cfg, err = load_config()
    if err:
        warn("config unusable (%s); allowing (fail-open)." % err)
        allow()

    try:
        pending = scan(cfg, base)
    except Exception as e:
        warn("scan error (%s); allowing (fail-open)." % e)
        allow()

    if not pending:
        allow()

    # gate is dirty
    if event.get("stop_hook_active"):
        # already nudged this continuation chain — never trap the session
        warn("gate STILL pending (%d) after a prior nudge; allowing to avoid a loop." % len(pending))
        allow()

    block(reason_text(pending))


if __name__ == "__main__":
    main()
