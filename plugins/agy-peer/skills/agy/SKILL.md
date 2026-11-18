---
name: agy
description: Call Antigravity (agy / Gemini) for review, design analysis, debugging, or delegated implementation. Use only when the user explicitly selects agy, Antigravity, or Gemini as an agent (including direct skill invocation), or clearly continues an existing agy call.
---

# Antigravity (agy)

## Invoke

Always use the bundled wrapper; plain `agy` is interactive. Set `<skill-dir>` to the absolute directory of this loaded SKILL.md, expanding any skill-root alias.

Choose a heredoc delimiter that doesn’t appear on its own line in the prompt.

```bash
python3 "<skill-dir>/scripts/agy-ask.py" <<'PROMPT'
<task, target, context, constraints, expected output, and whether edits are authorized>
PROMPT
```

Additional flags pass through to `agy`, except prompt/input/output/log and interactive flags owned by the wrapper. Supply the prompt only through stdin. Keep configured reasoning effort and permissions unless the user requests an override.

Value flags require nonempty values. Use `--flag=VALUE` for values starting with `-` and unlisted flags (`--future-boolean=true` for a boolean).

Use the configured default, typically Gemini 4 Carbon (High), for deep reviews and general work. Consider Gemini 4 Hydrogen (High) (`gemini-4-hydrogen-high`) for focused checks where speed matters. A user-specified model takes precedence.

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
python3 "<skill-dir>/scripts/agy-ask.py" --conversation <SESSION_ID> <<'PROMPT'
<follow-up>
PROMPT
```

Repeat any `--model` or `--effort` overrides when resuming.

Recover a missing ID from the prior call's `DETAILS/envelope.json` (`conversation_id`), or its `init` event in `DETAILS/events.jsonl` if interrupted. On exit 5, recover the ID from an earlier verified call for that session. An `init` mismatch blocks delivery. A result mismatch occurs after delivery, so verify what the peer did before resending. Avoid `--continue`, which may select another call.

## Results

Read `DETAILS=<dir>` from stderr at startup. On exit 0, read the answer, `SESSION_ID=<id>`, and `DETAILS=<dir>` from stdout.

For long calls, keep waiting on the same execution handle and use `DETAILS` for diagnostics.

Requires Python 3 and authenticated agy with stream-json support. For flags or models, use `agy --help` / `agy models`.
