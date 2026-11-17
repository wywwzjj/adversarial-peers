---
name: codex
description: Call Codex for review, design analysis, debugging, or delegated implementation. Use only when the user explicitly selects Codex as an agent (including direct skill invocation), or clearly continues an existing Codex call. Generic review requests, topic mentions, and ambiguous follow-ups do not trigger this skill.
---

# Codex

## Invoke

Always use the bundled wrapper; plain `codex` is interactive. Set `<skill-dir>` to the absolute directory of this loaded SKILL.md, expanding any skill-root alias. Use this copy; do not guess paths or rely on environment variables.

```bash
python3 "<skill-dir>/scripts/codex-ask.py" <<'PEER_PROMPT_END'
<task, target, context, constraints, expected output, and whether edits are authorized>
PEER_PROMPT_END
```

Use a quoted, task-specific heredoc delimiter absent from the prompt (never `EOF`). Additional flags pass through to `codex exec`. Keep configured model, reasoning effort, and permissions unless the user requests an override.

## Task handling

Choose the workflow from the request: adversarial or standard review, design analysis, debugging, or delegated implementation.

- Give the peer a concrete scope and the user's specific concern. For diffs, name the scope (e.g. uncommitted changes or comparison against main); let the peer collect it.
- Review/investigation alone: request findings without file changes. Implementation requested: allow authorized changes and require a change summary and verification; do not ask for the same authorization again.
- Check material claims against the code and report unresolved uncertainty.

For long tasks, allow several minutes or run in the background if the foreground timeout is short; collect the result when complete.

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
python3 "<skill-dir>/scripts/codex-ask.py" resume <SESSION_ID> <<'PEER_PROMPT_END'
<follow-up>
PEER_PROMPT_END
```

Keep the ID with the agent identity; do not pass another peer's ID. Sessions persist across host restarts. Resume inherits the session's model and reasoning effort. Pass `-m` or `-c model_reasoning_effort=...` only for a requested override; `-s/--sandbox` must precede `resume`.

Recover a lost ID from `DETAILS/events.jsonl` (`thread.started.thread_id`). Only if unrecoverable, use `resume --last`; it can select another concurrent call.

## Results and failures

Success returns the answer, `SESSION_ID=<id>`, and `DETAILS=<dir>`; failure returns nonzero without an answer or ID. Save the ID for follow-ups. Read logs for failures, ID recovery, or user requests. `DETAILS` contains `events.jsonl` and `stderr.log`; failures show the error message from an `error` / `turn.failed` event when available, followed by the last 40 stderr lines.

- Exit 3: child failure, error event, or no final answer. Exit 4: malformed events or missing thread ID. Inspect `DETAILS`.
- `Not inside a trusted directory`: run in the repository or pass `--skip-git-repo-check`.
- Requires `python3` and `codex exec --json`; for unsupported flags, check `codex exec --help` / `codex exec resume --help`. Authenticate with `codex login`. Network access and writes to `~/.codex` must be permitted by the host environment.
