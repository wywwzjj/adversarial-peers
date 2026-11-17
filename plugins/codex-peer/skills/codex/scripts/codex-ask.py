#!/usr/bin/env python3
"""Single-file wrapper for `codex exec --json` used by the codex skill.

Prints ONLY: the final agent message, a SESSION_ID=<id> line, and a
DETAILS=<dir> line. Full JSONL events (`events.jsonl`: reasoning summaries,
command executions, token usage) and `stderr.log` are persisted in DETAILS for
on-demand inspection instead of flooding context.

Usage (flags pass through to `codex exec` verbatim):
    python3 codex-ask.py <<'PEER_PROMPT_END'
    multi-line prompt
    PEER_PROMPT_END
    python3 codex-ask.py resume <SESSION_ID> <<'PEER_PROMPT_END' ...

Exit codes:
  0  success
  3  child failure, error event, or no final non-empty agent message
  4  malformed event stream, missing thread ID, or failed CLI launch

Failed calls emit neither an answer nor SESSION_ID.
Requires: codex CLI with `codex exec --json` (verified v0.145.0), Python 3.9+.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def report_failure(details, stderr_path, message, code):
    print(f'DETAILS={details}', flush=True)
    sys.stderr.write(f'{message}\n')
    sys.stderr.write(f'--- stderr tail (full log: {stderr_path}) ---\n')
    try:
        for line in stderr_path.read_text(encoding='utf-8', errors='replace').splitlines()[-40:]:
            sys.stderr.write(f'{line}\n')
    except OSError:
        pass
    return code


def main():
    details = Path(tempfile.mkdtemp(prefix='codex-ask.'))
    events_path = details / 'events.jsonl'
    stderr_path = details / 'stderr.log'

    try:
        with events_path.open('w', encoding='utf-8') as events_out, stderr_path.open('w', encoding='utf-8') as stderr_out:
            rc = subprocess.call(
                ['codex', 'exec', '--json', *sys.argv[1:]],
                stdout=events_out, stderr=stderr_out,
            )
    except OSError as exc:
        return report_failure(details, stderr_path, f'codex exec failed: {exc}', 4)

    child_failure = f'codex exited {rc}; answer withheld (see DETAILS)'
    sid = last = None
    try:
        with events_path.open(encoding='utf-8', errors='replace') as events:
            for line in events:  # Streaming: never accumulate the event stream.
                e = json.loads(line)
                if not isinstance(e, dict):
                    raise ValueError('expected a JSON object')
                if e.get('type') in ('error', 'turn.failed'):
                    summary = 'codex reported an error'
                    if rc != 0:
                        summary += f' (exit {rc})'
                    for value in (e.get('error'), e.get('message')):
                        if isinstance(value, dict):
                            value = value.get('message')
                        if isinstance(value, str) and value.strip():
                            summary += f': {value.strip()}'
                            break
                    return report_failure(
                        details, stderr_path,
                        f'{summary}; answer withheld (see events.jsonl in DETAILS)', 3,
                    )
                if e.get('type') == 'thread.started':
                    sid = e.get('thread_id')
                elif e.get('type') == 'item.completed':
                    item = e.get('item')
                    if not isinstance(item, dict):
                        raise ValueError('expected an item object')
                    if item.get('type') == 'agent_message':
                        last = item.get('text')
    except (OSError, ValueError) as exc:
        if rc != 0:
            return report_failure(details, stderr_path, child_failure, 3)
        return report_failure(details, stderr_path, f'malformed event stream: {exc} (see DETAILS)', 4)

    if rc != 0:
        return report_failure(details, stderr_path, child_failure, 3)
    if not isinstance(last, str) or not last.strip():
        return report_failure(details, stderr_path, 'no final agent message (see DETAILS)', 3)
    if not isinstance(sid, str) or not sid.strip():
        return report_failure(details, stderr_path, 'missing thread ID (see events.jsonl in DETAILS)', 4)

    print(last.rstrip('\n'))
    print()
    print(f'SESSION_ID={sid}')
    print(f'DETAILS={details}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
