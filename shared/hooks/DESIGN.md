# The Guard-Hook Layer — design note

*A candidate "proven extension" for PATTERN.md. Optional, opt-in, portable across both
deployments (shipped skills + the job-search source). Not part of the minimal core.*

> **Build status (this session):** guard.py + guard.config.json + test_guards.py + README
> built and **22/22 offline checks green**; real PreToolUse JSON I/O smoke-verified
> (deny/ask emit JSON, allow is silent). Living in scratch pending placement. All four
> decisions LOCKED (§7). One design change from the note below: **config is JSON, not YAML**
> — the hook runs under bare system `python3` (pyyaml is NOT guaranteed present; it was absent
> in this very environment), and a safety guard must not silently degrade to floor-only over a
> missing import. Stdlib `json` is always available. `guard.py` carries zero third-party deps.

---

## 1. Purpose & non-goals

**Purpose.** An enforcement *belt* that blocks consequential actions taken **around** the
deterministic scripts, in the one deployment where an autonomous model can go off-script:
a live Claude Code session.

**The gap it closes (the whole reason it exists):**

> Script-level checks can only guard actions that go **through** the script.
> Hooks guard actions that go **around** it.

```
              ┌─────────────────── Claude Code session ───────────────────┐
  model  ──►  │  emit.py / build.py   ← in-script checks (PORTABLE belt)   │
              │       (goes THROUGH your validation — already hardened)     │
              │                                                             │
  model  ──►  │  send tool / Write profile.yaml / git commit creds          │
              │       (goes AROUND your script — currently only PROSE)  ◄── guard hook
              └─────────────────────────────────────────────────────────────┘
```

**Non-goals.**
- Not a replacement for any in-script check (those are stronger than RCA's already).
- Not part of the portable core — hooks live in `settings.json`, which only fires in a
  CC session. Under an API/VM judge the hook is absent; the in-script checks still hold.
  → ships as an **optional module**, exactly like the other proven extensions.
- v1 covers the highest-leverage guard (**block auto-send/submit**); the state-inspecting
  "un-skippable critique gate" is a later increment.

---

## 2. The guard contract

A guard is a small shell script wired as a `PreToolUse` hook. It is a pure decision function:

**Input** (stdin, JSON from Claude Code):
```json
{ "hook_event_name": "PreToolUse", "tool_name": "<name>", "tool_input": { ... },
  "cwd": "<dir>", "session_id": "..." }
```

**Reads** a per-deployment **config file** (the parameterization — see §3). No policy in the
script body; the script is domain-blind, the config is the domain. *(Your "tuning lives in
data, not code" non-negotiable, applied to hooks.)*

**Emits** a decision, via JSON on stdout (preferred — gives 3-way control):
```json
{ "hookSpecificOutput": { "hookEventName": "PreToolUse",
    "permissionDecision": "deny",              // allow | ask | deny
    "permissionDecisionReason": "Blocked: auto-submit is a human action (Rule 3). Config: send_guard." } }
```
Fallback simple form: `exit 0` = allow, `exit 2` = block (stderr → model as reason).

**Fail mode: fail-CLOSED.** If the guard script itself errors, treat as `deny` for
send/submit surfaces. A safety belt that fails open isn't one. (Louder, but correct.)

---

## 3. Config schema — the thing that makes it port

One script, N configs. The matcher in `settings.json` is coarse (tool-name only), so the
fine logic lives in the guard reading this file. The submit/send surface is **heterogeneous**
(a named MCP tool, a `Bash` curl, a browser action) — so the config matches on all three.

`guard.config.yaml` (per deployment):
```yaml
send_guard:
  decision: deny                 # deny (hard) | ask (human confirms)
  allow_drafts: true             # create_draft is safe — it doesn't send
  blocked_tools:                 # exact tool-name matches
    - mcp__claude_ai_Gmail__send_message
  blocked_bash_patterns:         # regex vs tool_input.command when tool_name == Bash
    - 'curl .*(apply|submit|/application)'
  blocked_url_patterns:          # vs claude-in-chrome navigate/computer targets
    - '(greenhouse|lever|ashby|workable)\..*/(apply|application)'

data_guard:                      # (guard #2 — protect human-authored data)
  decision: ask
  protected_paths:
    - profile.yaml
    - '*/pack.yaml'
    - '*/rubric.yaml'

secret_guard:                    # (guard #3 — P0 containment)
  decision: deny
  protected_paths:
    - sheets-creds.json
    - '*service-account*.json'
```

Config resolution: fixed path anchored on `$CLAUDE_PROJECT_DIR` (env), the way RCA anchors
its hooks. No CLI-flag layer — hooks can't take flags.

---

## 4. The two wirings — same script, two configs

**Shipped-skill deployment** (`~/.claude/settings.json` or a skill-local settings snippet):
```json
{ "hooks": { "PreToolUse": [
  { "matcher": "mcp__.*|Bash", "hooks": [
    { "type": "command", "command": "$CLAUDE_PROJECT_DIR/hooks/guard-send.sh", "timeout": 5 } ] } ] } }
```
Config: blocks email-send tools + generic submit-looking Bash. Protects synthetic examples.

**Pipeline deployment** (a `.claude/settings.json` that wires this hook — the backport):
```json
{ "hooks": { "PreToolUse": [
  { "matcher": "mcp__.*|Bash|mcp__claude-in-chrome__.*", "hooks": [
    { "type": "command", "command": "$CLAUDE_PROJECT_DIR/hooks/guard-send.sh", "timeout": 5 } ] } ] } }
```
Config: same script, but real ATS apply URL/Bash patterns + `sheets-creds.json` in
`secret_guard`. This is where the guard protects **the real thing** and where Rule 3
("a human submits") becomes physically enforced.

→ **Backport = copy `hooks/guard-send.sh` + write one config.** No rewrite.

---

## 5. Guard #1 worked shape (block auto-send/submit)

Decision logic (pseudocode — the script):
```
event = json.load(stdin);  cfg = load(config).send_guard
name, inp = event.tool_name, event.tool_input
if name in cfg.blocked_tools:                          -> DENY
if name == "Bash"      and any(p ~ inp.command): p in cfg.blocked_bash_patterns  -> DENY
if name ~ chrome-nav   and any(p ~ inp.url):     p in cfg.blocked_url_patterns   -> DENY
if name == create_draft and cfg.allow_drafts:          -> ALLOW  (drafting ≠ sending)
otherwise                                              -> ALLOW
```
Note in this environment the Gmail MCP exposes only `create_draft` (no send) — so email is
*already* draft-only. The guard's live value is the **submit** surface (ATS apply via Bash
curl or a browser click), which is exactly the source's Rule-3 surface.

---

## 6. Testing (offline, matches your `test_*.py` style)

Two fixture sets piped to the guard on stdin:
- **benign** → assert `allow` / exit 0 (a `SELECT` Bash, a `create_draft`, a read).
- **blocked** → assert `deny` / exit 2 (a `curl …/apply`, a browser nav to an apply URL,
  a hypothetical send tool, a `git commit sheets-creds.json`).
- **guard-crashes** → assert fail-closed (deny) on malformed config / bad stdin.

`test_guards.sh` (or pytest that shells out); zero network; ships with the layer.

---

## 7. Decisions — LOCKED

1. **Decision mode per guard.** ✅ `deny` for send/submit + secret (Rule 3 = human acts
   *outside* the pipeline); `ask` for profile/data writes (legit edits happen).
2. **v1 surface scope.** ✅ Named tools + `blocked_bash_patterns` only. **Browser click
   (`blocked_url_patterns`) is OFF for v1** — revisit in v1.1 if the source's real submit
   path turns out to be a claude-in-chrome action.
3. **Config location.** ✅ **Each skill carries its own** config —
   `<skill>/hooks/guard.config.yaml`, anchored on `$CLAUDE_PROJECT_DIR`. No user-global
   config; keeps every skill a self-contained shippable folder (matches the factory's
   output model).
4. **Fail mode.** ✅ (pending final confirm) **Fail-CLOSED with a hardcoded safety floor.**
   The script carries a minimal built-in denylist (known send tool names + an obvious
   `curl …/apply` pattern) that applies *even if the config won't load*. On config-load
   failure: apply the floor, deny only floor matches, emit a loud "config broken" notice,
   allow everything else — so a YAML typo never bricks the session, but the core
   never-auto-submit guarantee survives config breakage. On per-call eval error with a
   valid config: deny that call. Data guard fails open (`ask` default).

---

## 8. Ship checklist (when we build)

- [ ] `hooks/guard-send.sh` — portable, config-driven, fail-closed
- [ ] `guard.config.yaml` — skill deployment (synthetic surfaces)
- [ ] `test_guards.sh` — benign + blocked + crash fixtures
- [ ] `settings.json` snippet + a 3-line "paste this to enable" README block
- [ ] **Backport:** a deployment's `.claude/` config with real domain/secret patterns; verify
      end-to-end against one real action URL (dry, no send)

---

## 9. Guard #4 — the un-skippable critique gate (Stop hook) — BUILT 2026-07-08

Separate mechanism from guards 1–3: a **Stop** hook (`gate.py`), not PreToolUse. It fires when
the model tries to END its turn and blocks if a critique/QA gate was skipped.

**Signal (verified in code).** tailor-artifacts writes `records/*.json` with
`status: "Built - pending critique"` at build, flipped to `"Ready for Review"` / `"Needs rework"`
(and `critique` set non-null) only when `python3 tailor.py critique` runs. So a record still at
`"Built - pending critique"` is an exact "gate skipped" signal. Config keys on it; CWD-relative
glob so one wiring covers whichever project is active.

**Contract difference — FAIL-OPEN.** A Stop hook that denied on error would trap the user in a
turn they can't end. So the polarity flips vs the PreToolUse guard: any parse/config/scan error →
**allow the stop** + loud stderr. And it honors `stop_hook_active` → **nudges once per turn**
(one hard block; if ignored and the model re-stops, allow — never trap).

**Status.** Engine + config + `test_gate.py` (15 checks green) in `shared/hooks/`; wired
**user-global** (`~/.claude/hooks/gate.py` + a `Stop` entry in `~/.claude/settings.json`), config
carries the tailor-artifacts gate. Deployed-config e2e verified (blocks a pending record, allows a
critiqued one, loop-safe on re-stop).

**Source (job-search) NOT yet gated — needs a marker.** `record.py` flips to `"Ready for Review"`
at *build* time regardless of critique, and `generate.py critique-report` only prints — so there
is **no detectable "critiqued" signal** in `runs/*.json`. Smallest fix: have the critique step
stamp `critiqued_at` (or a `critique` block) onto each `runs/{id}.json`, then add a source gate
entry keyed on its absence. Proposed, not yet applied (would touch the 148-test pipeline).
