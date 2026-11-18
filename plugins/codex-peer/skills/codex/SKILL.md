---
name: codex
description: Call Codex for review, design analysis, debugging, or delegated implementation. Use only when the user explicitly selects Codex as an agent (including direct skill invocation), or clearly continues an existing Codex call.
---

# Codex

## Invoke

Always use the bundled wrapper; plain `codex` is interactive. Set `<skill-dir>` to the absolute directory of this loaded SKILL.md, expanding any skill-root alias.

Choose a heredoc delimiter that doesn’t appear on its own line in the prompt.

```bash
python3 "<skill-dir>/scripts/codex-ask.py" <<'PROMPT'
<task, target, context, constraints, expected output, and whether edits are authorized>
PROMPT
```

Additional flags pass through to `codex exec`. Keep configured model, reasoning effort, and permissions unless the user requests an override.

## Task handling

- Give the peer a concrete scope and the user's specific concern. For diffs, name the scope (e.g. uncommitted changes or comparison against main); let the peer collect it.
- Keep review and investigation read-only. For implementation, request the authorized changes, verification, and a summary.
- Check material claims against the code and report unresolved uncertainty.

## Adversarial review

When asked to challenge or pressure-test a design, adapt this prompt to the target and concern:

```text
Challenge <target> on <concern>. Find material failure scenarios, question assumptions, and consider simpler alternatives. Ground findings in code; rank by severity and include file/line, trigger, impact, and a concrete fix. Mark uncertainty, omit style nits, and say if no material issues are found.

Report findings only; do not modify files.
```

## Continue a session

Resume a clearly linked follow-up with the captured session ID.

```bash
python3 "<skill-dir>/scripts/codex-ask.py" resume <SESSION_ID> <<'PROMPT'
<follow-up>
PROMPT
```

Resume inherits the model and reasoning effort. Place any requested `-s` or `--sandbox` before `resume`.

Recover a missing ID from `DETAILS/events.jsonl` (`thread.started.thread_id`). Avoid `resume --last`, which may select another call.

## Results

Read `DETAILS=<dir>` from stderr at startup. On exit 0, read the answer, `SESSION_ID=<id>`, and `DETAILS=<dir>` from stdout.

For long calls, keep waiting on the same execution handle and use `DETAILS` for diagnostics.

Requires Python 3 and authenticated Codex. For flags, use `codex exec --help` / `codex exec resume --help`. For `Not inside a trusted directory`, run in the repository or pass `--skip-git-repo-check`.
