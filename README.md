# adversarial-peers

Peer coding agents for adversarial / steerable review, design discussion, debugging, and delegated implementation. The three plugins provide the same responsibilities through different agents:

| Plugin | Agent | CLI | Skill |
|--------|-------|-----|-------|
| `codex-peer` | Codex | `codex exec` | `codex` |
| `cc-peer` | Claude Code | `claude -p` | `claude` |
| `agy-peer` | Antigravity / Gemini | `agy --print` | `agy` |

Choose the agent explicitly, then choose the workflow by task fit. Each agent uses its configured model, reasoning effort, and permissions unless the user requests an override.

## Installation

Install the CLI for the agent you want to call, authenticate it, and have `python3` on PATH. The host loads the skill and runs its bundled wrapper.

- Codex: `codex --version`, `codex login`; the wrapper requires `codex exec --json` (verified on v0.145.0).
- Claude Code: `claude --version`; run `claude` interactively once to authenticate.
- Antigravity: `agy --version`; run `agy` interactively once to sign in. The wrapper requires `stream-json` input/output support (verified on v1.5.9 / v1.5.10).

Choose a plugin from the table and replace `<plugin>` in the commands below with its name.

### Codex CLI

Requires a Codex version with plugin support (`codex plugin --help`; installation and skill discovery tested on v0.169.0).

```bash
codex plugin marketplace add wywwzjj/adversarial-peers
codex plugin add <plugin>@adversarial-peers
codex plugin list
```

### Claude Code

```text
/plugin marketplace add wywwzjj/adversarial-peers
/plugin install <plugin>@adversarial-peers
```

You can also use `/plugin` to browse and manage plugins interactively, or install an individual skill via the open Agent Skills CLI (`npx skills add wywwzjj/adversarial-peers --skill <codex|claude|agy> -g`).

### Updates and migration

In Codex, refresh the marketplace and re-add the selected plugin to refresh its cached version:

```bash
codex plugin marketplace upgrade adversarial-peers
codex plugin add <plugin>@adversarial-peers
```

Removal uses the full identifier: `codex plugin remove <plugin>@adversarial-peers`. Check the installed `codex plugin --help` for version-specific details.

In Claude Code:

```text
/plugin marketplace update adversarial-peers
/plugin update <plugin>@adversarial-peers
```

Reload the host session's skill catalog after installation or updates. Editing a marketplace checkout does not update an installed plugin cache.

**Migrating from `cc-skill-codex` / `cc-skill-codex-marketplace`**: the repository and marketplace were renamed to `adversarial-peers`, and plugins were unified to `<agent>-peer` (`cc-skill-codex` → `codex-peer`, `codex-skill-claude` → `cc-peer`, plus `agy-peer`). Remove the old plugin and marketplace entry, add `wywwzjj/adversarial-peers`, and install `<plugin>@adversarial-peers` (for direct `--plugin-dir` usage, point to `plugins/<plugin>`).

## Usage

All three agents support the same workflows. These examples use different agents to illustrate selection; each workflow is available with any of them.

| Workflow | Example request |
|----------|-----------------|
| Adversarial / steerable review | “Use Codex to challenge the caching design — focus on races and rollback gaps.” |
| Standard review | “Use Claude to review auth.py for bugs and security issues.” |
| Design analysis | “Ask agy to compare approaches for a multi-tenant rate limiter.” |
| Debug investigation | “Use Codex to trace why the queue deadlocks under concurrency.” |
| Delegated implementation | “Have Claude fix the deadlock and verify the change.” |

Adversarial review is the flagship workflow: name a specific design choice or risk area for the peer to challenge. For a diff-scoped review, name the scope, such as “uncommitted changes” or “the diff against main”; the peer collects the diff itself.

The skill activates only when the user selects the agent, directly invokes the skill, or clearly continues an existing call. A generic review request, a mention of an agent as a topic, or an ambiguous “continue” does not choose a peer.

Give the peer the target, context, constraints, expected output, and whether edits are authorized. Review and investigation requests return findings without changing files. An implementation request authorizes the peer to make the requested changes and verify them. The caller assesses the result and reports material uncertainty.

### Continue a session

A follow-up such as “Continue that review — also check rollback under partial failure” resumes the selected peer's prior session by its captured ID. Sessions persist across host restarts. Keep the ID with the agent identity so another plugin's session is never resumed by mistake.

| Agent | Resume selector | Model / effort on resume | Saved ID |
|-------|-----------------|--------------------------|----------|
| Codex | `resume <SESSION_ID>` | Inherits the session's model and reasoning effort | `events.jsonl`: `thread.started.thread_id` |
| Claude Code | `--resume <SESSION_ID>` | Inherits the model; pass effort explicitly to override | `envelope.json`: `session_id` |
| Antigravity | `--conversation <SESSION_ID>` | Re-pass explicit model / effort overrides | `envelope.json`: `conversation_id` |

Recover a missing ID from the prior call's `DETAILS` directory. Most-recent selectors (`--last` / `--continue`) are last resorts because they can select another concurrent call. The agy wrapper verifies the requested ID in the same process before sending the resumed task; an unknown ID or unsupported handshake fails without sending it.

### Results and permissions

Every wrapper returns the answer, `SESSION_ID=<id>`, and `DETAILS=<dir>` on success. Full output and stderr stay in the details directory for inspection when needed. Failed calls return a nonzero exit status and withhold the answer and session ID; logs remain available. See each skill for its CLI-specific error codes.

Log directories use `/tmp/<peer>-ask.<random>` on POSIX systems and the system temporary directory elsewhere. Each call has its own directory; the wrapper retains it for inspection.

Before launch, stderr announces `DETAILS=<dir>`. Wait through the host execution handle; the peer session ID is for follow-ups. Prefer longer waits over sleep/short-poll loops. For progress or diagnosis, use the announced directory and start with concise metadata. Silence does not establish a hang, and activity does not imply imminent completion. Each skill describes its agent's log format.

The wrappers add no model, effort, sandbox, or tool restrictions. Agents need network access and access to their local state directories, such as `~/.codex`, `~/.claude`, or `~/.gemini`. Use the host's normal permission flow and the user's configured environment.

## Skill paths

All examples use `<skill-dir>` for the absolute directory containing the loaded `SKILL.md`. Resolve any supplied Skill roots alias first and use that installed copy, including versioned cache paths and paths containing spaces. Do not guess from the working directory or depend on a skill-directory environment variable.

Codex v0.169.0 does not supply `CODEX_SKILL_DIR` / `CLAUDE_SKILL_DIR` or substitute them in skill text. Plugin-hook variables are a separate mechanism. Earlier verification used the installed binary and pinned upstream [skill loading](https://github.com/openai/codex/blob/7fb4014d52b5bc5049c389f9df015ff8c7e6e37c/codex-rs/ext/skills/src/host_prompt.rs) and [hook discovery](https://github.com/openai/codex/blob/7fb4014d52b5bc5049c389f9df015ff8c7e6e37c/codex-rs/hooks/src/engine/discovery.rs).

## Repository structure

```text
.claude-plugin/marketplace.json
plugins/
├── codex-peer/
│   ├── .claude-plugin/plugin.json
│   └── skills/codex/
│       ├── SKILL.md
│       └── scripts/codex-ask.py
├── cc-peer/
│   ├── .claude-plugin/plugin.json
│   └── skills/claude/
│       ├── SKILL.md
│       └── scripts/claude-ask.py
└── agy-peer/
    ├── .claude-plugin/plugin.json
    └── skills/agy/
        ├── SKILL.md
        └── scripts/agy-ask.py
tests/
├── test_peer_wrappers.py
└── test_agy_ask.py
```

Each `SKILL.md` is the authoritative workflow reference for that agent's invocation, resume rules, and common errors. The marketplace provides one plugin per agent using the same manifest layout.

## Verification

Run the wrapper regression suite without calling a model:

```bash
python3 -m unittest discover -s tests -v
```

The tests cover the shared success/failure contract, literal and large-payload prompt forwarding, configured defaults and explicit overrides, log preservation, pre- and post-init error envelopes, malformed/empty output, nonzero child exits, and Codex event-error summaries when stderr is empty. agy-specific tests also cover single-file layout, pre-send resume-ID validation, malformed/missing handshakes, argument conflicts, missing-value rejection, inherited stdout with and without a final result, concurrent large input/output, error-result preservation after a broken stdin, closing stdin for pre-init errors, process-group cleanup after result-validation failures, macOS zombie-group EPERM recovery without suppressing genuine permission errors, bounded exit cleanup, SIGINT/SIGTERM process-group cleanup, and proof that a mismatched session receives no task. Tests use fake CLIs and installed-style paths containing spaces, without host path variables.

agy reviews can be slow: a live review with the configured High model completed nine minutes after resuming. Allow long tasks to finish and use background execution when foreground tools have short timeouts. Several minutes without output is not evidence of a hang; the wrapper adds no overall reasoning deadline.

Live checks on 2026-11-17 installed `agy-peer` with Codex v0.169.0 in an isolated `CODEX_HOME`, discovered its cached skill, and verified fresh turns, resumed turns, invalid-ID rejection (Exit 5 before sending the task), and pre-init error classification (Exit 3) against agy v1.5.9 / v1.5.10.

**Previously verified CLIs**: Codex v0.142.2 / v0.145.0 for base commands and JSON events, v0.169.0 for plugin installation/discovery; Claude Code v2.1.206; agy v1.5.5 for original behavior checks and v1.5.9 / v1.5.10 for stream-json invocation and resume. For other versions, consult the CLI's own help.

**Plugin versions**: codex-peer 1.0.2 · cc-peer 1.0.1 · agy-peer 1.0.2

**Runtime dependency**: `python3`

**License**: Apache 2.0
