# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repository is a **peer-agent marketplace** (`adversarial-peers`) holding three plugins:

- **codex-peer**: invokes OpenAI's Codex CLI (`codex exec`, verified against v0.142.2) as a peer coding agent
- **cc-peer**: invokes Claude Code (`claude -p`, verified against v2.1.206) as a peer coding agent
- **agy-peer**: invokes Antigravity CLI (`agy --print`, initially verified against v1.5.5) as a peer coding agent

All plugins provide the same responsibilities through different agents: flagship use case is **adversarial / steerable code review**, plus standard review, deep design analysis, debugging investigations, and delegated tasks.

No skill pins a model, reasoning effort, or permissions — each relies on the callee CLI's configured defaults unless the user requests an override. This avoids per-release maintenance churn.

**One plugin per callee**: install the agents you want to call (`codex-peer`, `cc-peer`, `agy-peer` — note `cc-peer` uses `cc` because Anthropic's plugin validator reserves the `claude-` prefix). Both hosts read `.claude-plugin/marketplace.json` / `plugin.json`; no duplicated Codex manifest is needed. Codex plugin installation and discovery are verified against v0.169.0.

**Explicit peer selection**: all three skills require the user to name the peer as an agent to call (or directly invoke the skill), or clearly continue an existing session with that peer. Generic review, second-opinion, and design-challenge requests must not select these skills. A mention as a topic or an ambiguous "continue" is insufficient. Use-case tables apply only after peer selection.

**Skill paths**: all three skills use `<skill-dir>` for the absolute parent of the loaded SKILL.md, expanding any supplied Skill roots alias first. Use the installed copy; never execute a literal placeholder or guess a cache path. Do not rely on skill-directory environment variables or hook variables. See README for prior Codex path-loading verification.

Codex snapshots installed plugin skills into a versioned cache. Changed released skills need a plugin version bump and reinstall/update before a host loads them. Do not edit installed caches to deliver repository changes.

## Architecture

```
adversarial-peers/                    # Marketplace root
├── .claude-plugin/
│   └── marketplace.json              # Marketplace config: lists all plugins
├── plugins/
│   ├── codex-peer/                   # Codex
│   │   ├── .claude-plugin/plugin.json
│   │   └── skills/codex/
│   │       ├── SKILL.md
│   │       └── scripts/codex-ask.py
│   ├── cc-peer/                      # Claude Code
│   │   ├── .claude-plugin/plugin.json
│   │   └── skills/claude/
│   │       ├── SKILL.md
│   │       └── scripts/claude-ask.py
│   └── agy-peer/                     # Antigravity
│       ├── .claude-plugin/plugin.json
│       └── skills/agy/
│           ├── SKILL.md
│           └── scripts/agy-ask.py
```

**Key distinction**: each `SKILL.md` is the authoritative workflow reference the host agent loads, while `README.md` remains the installation and usage guide for humans.

## Key Technical Constraints — codex-peer

### Invoke through the wrapper script
The skill's default path is `plugins/codex-peer/skills/codex/scripts/codex-ask.py`, which wraps `codex exec --json` (verified v0.145.0):

```bash
python3 "<skill-dir>/scripts/codex-ask.py" <<'PEER_PROMPT_END'
Multi-line prompt (heredoc is the default form; delimiter is PEER_PROMPT_END, never EOF —
reviewed diffs often contain a bare EOF line, which would break out of the heredoc)
PEER_PROMPT_END
```

Design rationale (the script encodes what the old inline chain did by prose):
- **Context economy**: stdout carries only the final agent message + `SESSION_ID=<id>` + `DETAILS=<dir>`. Reasoning summaries, command executions, and token stats never enter the caller's context.
- **Details on demand, never discarded**: the full `--json` event stream (`events.jsonl`) and stderr (`stderr.log`) are persisted in the per-call `DETAILS` dir (`mktemp -d`). Failure shows a 40-line stderr tail; everything else is pull-based.
- **Structured session ID**: taken from the `thread.started` event. It is emitted only after a successful child exit and valid non-empty final message; recover it later from `events.jsonl` if needed.
- `codex exec` only — plain `codex` is interactive and fails with "stdout is not a terminal". Long high-reasoning runs can exceed a 2-minute foreground timeout — run in the background.

The wrapper requires `codex exec --json` and python3. It reads the saved stream line by line after the child exits, and withholds the answer and session ID on a nonzero child exit, error event, malformed stream, or missing/empty result or ID.

### Resume by session ID, not `--last`
`codex exec resume --last` resumes the most recent recorded session — cwd-filtered but still racing with any other Codex call in the same repo (parallel reviews, the user invoking Codex directly). Resume with the captured ID through the same script:

```bash
python3 "<skill-dir>/scripts/codex-ask.py" resume <SESSION_ID> <<'PEER_PROMPT_END'
follow-up prompt
PEER_PROMPT_END
```

Resume inherits the original session's model and reasoning effort; override `-m`/`-c model_reasoning_effort` only to intentionally change the inherited values.

**`-s/--sandbox` must go before the `resume` subcommand.** `resume` accepts `-c`, `-m`, and `--json` (a genuine `resume` flag on v0.145.0) on either side, but `-s` placed after `resume` fails with exit 2 — which the wrapper surfaces as exit 3 with the usage error in the stderr tail. (`--skip-git-repo-check` is also a genuine `resume` flag and may follow the session ID.)

## Key Technical Constraints — cc-peer

All live-verified against claude 2.1.206; the traps mirror the Codex ones almost exactly. The skill's default path is `plugins/cc-peer/skills/claude/scripts/claude-ask.py` (wraps `claude -p --output-format json`; same output contract as `codex-ask.py`: answer + `SESSION_ID=` + `DETAILS=` with the full envelope and stderr persisted for on-demand reads).

- **Always `claude -p`** — plain `claude` is interactive and hangs in non-interactive contexts
- **Maintainer decision (2026-07): the skills impose NO restriction flags** — no `--tools`, no `--safe-mode` on the Claude side, no `-s` pin on the Codex side. Both CLIs run with the user's configured defaults; restrictions had real side effects (read-only sandbox blocks network/temp writes; `--tools` removes Bash so Claude can't collect diffs; `--safe-mode` blocks the repo's CLAUDE.md/skills/MCP that help reviews). Keep it simple — don't re-add them when tracking CLI updates. For the record, the verified facts that motivated (and then un-motivated) them: `-p` auto-approves workspace writes; per-invocation flags don't survive `--resume`; model **is** inherited on resume
- **Failure is JSON too**: the script checks `is_error`, validates the envelope and non-empty result/session ID, and rejects nonzero child exits even with success-shaped JSON. Failed calls emit neither an answer nor a session ID; raw output and stderr remain in DETAILS.
- **`--resume` needs the ID in print mode** — bare `--resume` opens an interactive picker and hangs under `-p`; `-c/--continue` is cwd-scoped-most-recent and races like Codex's `--last`
- Multi-line prompts go via stdin heredoc with a quoted delimiter (`PEER_PROMPT_END`, never `EOF`)

## Key Technical Constraints — agy-peer

Behavior traps were live-verified against agy 1.5.5; single-file `stream-json` invocation and ID-based context continuation were verified with agy 1.5.9 / 1.5.10 on 2026-11-17. Wrapper: `plugins/agy-peer/skills/agy/scripts/agy-ask.py` (single-file Python wrapper using `stream-json` input/output for both fresh and resumed calls; same output contract: answer + `SESSION_ID=` + `DETAILS=`; DETAILS holds `events.jsonl`, `envelope.json`, `agy.log` via `--log-file`, and `stderr.log`).

- **`-p` takes the prompt as its value** — `agy -p --output-format json "x"` swallows `--output-format` as the prompt (exit 2), piped stdin alongside `-p` is *merged* into the prompt, and bare piped stdin without `-p` falls back to the interactive TUI when empty. The wrapper reads the prompt from stdin and sends it as a single NDJSON user message after `init` (with `--print=`), avoiding all three traps as well as OS argv length limits. Heredoc delimiter is `PEER_PROMPT_END`
- **Unknown `--conversation <id>` does not fail** — stderr warning only, a NEW conversation starts, exit 0. Because both fresh and resumed calls run over `stream-json`, the wrapper inspects `init.conversation_id` in the same process before sending the prompt, and exits 5 without sending the task on mismatch. Note that agy creates an empty stub conversation during `init` even when aborted, so never follow Exit 5 with `-c`. Missing/unsupported stream-init support exits 4 without sending the task
- **Resume does NOT inherit the model** — a resumed conversation runs on the configured default (verified via `agy.log`: `Propagating selected model override`); re-pass `--model`/`--effort`
- The wrapper rejects prompt/input/output/log and interactive overrides (including Go-style single-dash aliases), empty/repeated IDs and conflicting resume selectors, non-object JSON, and empty responses, and withholds answers/session IDs on a nonzero child exit even if its envelope says SUCCESS
- Errors are JSON too: `status: "ERROR"` + `error`, exit 1 (including pre-`init` failures such as unknown `--model` or invalid `--effort` on fresh or resumed calls) → `envelope.json` saved and wrapper exit 3; unknown CLI flags print usage with no JSON → exit 4
- Under the originally tested agy 1.5.5 stock settings, `-p` auto-approved workspace writes and commands; the skill adds no restriction flags (same maintainer decision as the other two)
- `-c/--continue` is most-recent and races, like the other CLIs
- Codex needs to permit agy network access and writes to its state directories (`~/.gemini` in the verified installation). Use normal host permissions / configuration; do not add permission overrides to the wrapper
- For large reviews pass a scope and let agy read the files

## Plugin Installation Flow

Choose the plugin by the agent to invoke: `codex-peer`, `cc-peer`, or `agy-peer`. Installation differs by host; responsibilities do not.

- Codex: `codex plugin marketplace add wywwzjj/adversarial-peers`, then `codex plugin add <plugin>@adversarial-peers`.
- Claude Code: `/plugin marketplace add wywwzjj/adversarial-peers`, then `/plugin install <plugin>@adversarial-peers`.

Use the installed skill's path to resolve its wrapper. An install command does not establish that every host/agent combination has been live-tested; document verification scope precisely.

`adversarial-peers` (`codex-peer`, `cc-peer`, `agy-peer`) replaces the earlier `cc-skill-codex` repository / `cc-skill-codex-marketplace` (`cc-skill-codex`, `codex-skill-claude`, and local draft `cc-skill-agy`); remove old plugins and marketplace entries before installing the new names. `codex plugin remove` requires `plugin@marketplace`. Refresh released Git marketplaces with `codex plugin marketplace upgrade <name>`, then re-add a plugin to refresh its cached version and reload the host skill catalog.

## Sandbox Modes (Codex)

The skill does not pass `-s` — Codex uses whatever sandbox is configured in the user's `config.toml`. The modes (`read-only` / `workspace-write` / `danger-full-access`) are the user's choice; pass `-s` only when the user explicitly asks for a specific one.

## Skill Maintenance Principles

When updating a skill to track its callee CLI's updates:

### Keep responsibilities consistent

The three plugins are interchangeable in responsibility: review, design analysis, debugging, and delegated implementation. Keep selection rules, task handling, review prompts, configured defaults, installed-path resolution, and success/failure contracts aligned. Keep only genuine CLI differences in each skill: invocation flags, resume behavior, output schema, and common failures. Do not assign main/optional status or fixed caller roles to a plugin.

Review-only requests return findings without changes. Implementation already authorized by the user may proceed without another permission question. Assess peer findings against the code before presenting them as established facts.

### Focus on the calling scenario
Each skill enables **a host to call the selected callee's CLI** (`codex exec`, `claude -p`, or `agy --print`). No skill is a comprehensive manual for the callee.

### What to include
- Stable commands the calling agent actually uses
- Resume rules and heredoc examples
- A few common failures users hit in practice
- Pointers to local CLI help for anything version-sensitive

### What to exclude
- Mirrored `--help` output or full feature inventories
- Large configuration references that duplicate upstream docs
- Callee features the calling agent will not invoke in this skill
- Anything likely to churn every CLI release unless it is critical to the skill

### Guiding question
> "Will the calling agent use this when invoking the callee for design/review/debug tasks?"

If no, don't add it to the skill documentation.

### Design reference
OpenAI's official Claude→Codex plugin is [openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc) — a heavyweight design (persistent `codex app-server` JSON-RPC broker, Node 18+ runtime, slash commands, background job tracking, Stop-hook review gate). This repo deliberately stays lightweight (one-shot CLI calls + single-file Python wrappers, one plugin per callee). Its adversarial-review prompt structure and review-result discipline (review-only requests return findings without auto-fixes) are adopted in all three SKILL.md files; consult it for prompt-quality ideas, not architecture.

### Preferred documentation shape
- Keep each `SKILL.md` authoritative for its workflow
- For volatile CLI details, tell users to run the callee's own help (`codex --help`, `codex exec --help`, `claude --help`, `agy --help`)
- **Verify every behavioral claim against the live binary before documenting it** — callee CLIs have permission defaults and non-obvious inheritance rules that changed across releases

## Verification

Run `python3 -m unittest discover -s tests -v` for all three wrappers' shared result contract and agy's resume protocol. The tests use fake CLIs, installed-style paths with spaces, and no host skill-path variables. See README for coverage and the scope of earlier live checks. Validate manifests with `claude plugin validate <path>` and wrapper syntax with `python3 -c "import ast, pathlib; [ast.parse(p.read_text()) for p in pathlib.Path('plugins').rglob('*.py')]"`.
