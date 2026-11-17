---
name: agy
description: Call Antigravity (agy / Gemini) for review, design analysis, debugging, or delegated implementation. Use only when the user explicitly selects agy, Antigravity, or Gemini as an agent (including direct skill invocation), or clearly continues an existing agy call. Generic review requests, topic mentions, and ambiguous follow-ups do not trigger this skill.
---

# Antigravity (agy)

## Invoke

Always use the bundled wrapper; plain `agy` is interactive. Set `<skill-dir>` to the absolute directory of this loaded SKILL.md, expanding any skill-root alias. Use this copy; do not guess paths or rely on environment variables.

```bash
python3 "<skill-dir>/scripts/agy-ask.py" <<'PEER_PROMPT_END'
<task, target, context, constraints, expected output, and whether edits are authorized>
PEER_PROMPT_END
```

Use a quoted, task-specific heredoc delimiter absent from the prompt (never `EOF`). Additional flags pass through to `agy`, except prompt/input/output/log and interactive flags owned by the wrapper. Supply the prompt only through stdin. Keep configured model, reasoning effort, and permissions unless the user requests an override.

Value flags require nonempty values. Use `--flag=VALUE` for values starting with `-` and unlisted flags (`--future-boolean=true` for a boolean).

## Task handling

Choose the workflow from the request: adversarial or standard review, design analysis, debugging, or delegated implementation.

- Give the peer a concrete scope and the user's specific concern. For diffs, name the scope (e.g. uncommitted changes or comparison against main); let the peer collect it.
- Review/investigation alone: request findings without file changes. Implementation requested: allow authorized changes and require a change summary and verification; do not ask for the same authorization again.
- Check material claims against the code and report unresolved uncertainty.

## Adversarial review

When asked to challenge or pressure-test a design, adapt this prompt to the target and concern:

```text
Challenge <target>, focusing on <user's concern>. Find material reasons this design could fail. Question assumptions and whether another approach would be safer or simpler.

Prioritize auth/trust boundaries, data loss, rollback/retry/idempotency, concurrency, null/empty/timeout paths, and migration/compatibility risks. Report findings by severity: file/line, failure scenario, impact, and concrete fix. Ground claims in the code, mark inferences, and omit style nits or invented risks. If no material issues are found, say so.

Report findings only; do not modify files.
```

## Continue a session

For a follow-up clearly tied to this peer's prior call, use its captured ID. Omit the resume selector for a new task.

```bash
python3 "<skill-dir>/scripts/agy-ask.py" --conversation <SESSION_ID> <<'PEER_PROMPT_END'
<follow-up>
PEER_PROMPT_END
```

Keep the ID with the agent identity; do not pass another peer's ID. Sessions persist across host restarts. Resume does not inherit model overrides: re-pass any explicitly selected `--model` / `--effort` on every resume.

Recover a lost ID from `DETAILS/envelope.json` (`conversation_id`). If interrupted before that file exists, recover `conversation_id` from the `init` event in `DETAILS/events.jsonl`. Only if unrecoverable, use `--continue`; it can select another concurrent call. Never use it after Exit 5: the rejected call may have created a newer empty conversation.

## Results and failures

Before launch, stderr announces `DETAILS=<dir>`. Success returns the answer, `SESSION_ID=<id>`, and `DETAILS=<dir>`; failure returns nonzero without an answer or session ID. Save the peer ID for follow-ups.

For long calls, use background execution and longer waits on the host execution handle, avoiding sleep/short-poll loops. Silence alone is no reason to cancel or restart. For progress or diagnosis, use this call's announced directory and start with concise metadata; activity does not imply imminent completion.

Logs: `events.jsonl`, `envelope.json`, `agy.log`, and `stderr.log`. Progress events use `event: "step_update"`; summarize `step_update` fields `step_index`, `state`, `step_type`, and `tool_name`. A step marked `DONE` is not run completion. There is no overall reasoning timeout.

- Exit 2: invalid arguments, empty prompt/ID, or conflicting resume selectors.
- Exit 3: agy reported an error; inspect the envelope. Exit 4: unusable stream/result or child failure; inspect the logs.
- Exit 5: resume ID mismatch; no task was sent. Recheck the ID or start fresh; never bypass the guard with a direct CLI call.
- Exit 130 / 143: interrupted. Recover the peer ID from `DETAILS` before resuming.
- Requires `python3` and agy stream-input support. For flags/models, use `agy --help` / `agy models`. Sign in by running `agy` interactively once. Network access and state writes (including `~/.gemini`) must be permitted by the host environment.
