---
name: claude
description: Call Claude Code for review, design analysis, debugging, or delegated implementation. Use only when the user explicitly selects Claude as an agent (including direct skill invocation), or clearly continues an existing Claude call. Generic review requests, topic mentions, and ambiguous follow-ups do not trigger this skill.
---

# Claude Code

## Invoke

Always use the bundled wrapper; plain `claude` is interactive. Set `<skill-dir>` to the absolute directory of this loaded SKILL.md, expanding any skill-root alias. Use this copy; do not guess paths or rely on environment variables.

```bash
python3 "<skill-dir>/scripts/claude-ask.py" <<'PEER_PROMPT_END'
<task, target, context, constraints, expected output, and whether edits are authorized>
PEER_PROMPT_END
```

Use a quoted, task-specific heredoc delimiter absent from the prompt (never `EOF`). Additional flags pass through to `claude -p`. Keep configured model, reasoning effort, and permissions unless the user requests an override.

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
python3 "<skill-dir>/scripts/claude-ask.py" --resume <SESSION_ID> <<'PEER_PROMPT_END'
<follow-up>
PEER_PROMPT_END
```

Keep the ID with the agent identity; do not pass another peer's ID. Sessions persist across host restarts. Resume inherits the session's model; pass `--model` / `--effort` only for a requested override. Always supply the ID: bare `--resume` opens an interactive picker.

Recover a lost ID from `DETAILS/envelope.json` (`session_id`). Only if unrecoverable, use `--continue`; it can select another concurrent call in the same directory.

## Results and failures

Before launch, stderr announces `DETAILS=<dir>`. Success returns the answer, `SESSION_ID=<id>`, and `DETAILS=<dir>`; failure returns nonzero without an answer or session ID. Save the peer ID for follow-ups.

For long calls, use background execution and longer waits on the host execution handle, avoiding sleep/short-poll loops. Silence alone is no reason to cancel or restart. For progress or diagnosis, use this call's announced directory and start with concise metadata; activity does not imply imminent completion.

Logs: `envelope.json` and `stderr.log`. The envelope is final output and may remain empty or incomplete while Claude runs; it is not a progress stream.

- Exit 3: Claude reported `is_error`; the envelope has details. Exit 4: unusable output, missing result/ID, or nonzero child exit. Inspect `DETAILS`.
- Requires `python3`. For authentication, run `claude` interactively once; for flag or connection problems, check `claude --help` / `claude doctor`. Network access and writes to `~/.claude` must be permitted by the host environment.
