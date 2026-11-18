#!/usr/bin/env python3
"""Single-file wrapper for `codex exec --json` used by the codex skill.

Stdout prints ONLY: the final agent message, a SESSION_ID=<id> line, and a
DETAILS=<dir> line. Full JSONL events (`events.jsonl`: reasoning summaries,
command executions, token usage) and `stderr.log` are persisted in DETAILS for
on-demand inspection instead of flooding context. Stderr announces DETAILS before launch.

Usage:
    python3 codex-ask.py <<'PROMPT'
    multi-line prompt
    PROMPT
    python3 codex-ask.py resume <SESSION_ID> <<'PROMPT' ...

Flags pass through to `codex exec` verbatim.

Exit codes:
  0  success
  3  child failure, failed turn, unresolved error, or no final non-empty agent message
  4  malformed event stream, missing thread ID, or failed CLI launch

Failed calls emit neither an answer nor SESSION_ID.
Requires: codex CLI with `codex exec --json` (verified v0.145.0), Python 3.9+.
"""
import json
import os
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
    details = Path(tempfile.mkdtemp(prefix='codex-ask.', dir='/tmp' if os.name == 'posix' else None))
    events_path = details / 'events.jsonl'
    stderr_path = details / 'stderr.log'

    try:
        with events_path.open('w', encoding='utf-8') as events_out, stderr_path.open('w', encoding='utf-8') as stderr_out:
            sys.stderr.write(f'DETAILS={details}\n')
            sys.stderr.flush()
            rc = subprocess.call(
                ['codex', 'exec', '--json', *sys.argv[1:]],
                stdout=events_out, stderr=stderr_out,
            )
    except OSError as exc:
        return report_failure(details, stderr_path, f'codex exec failed: {exc}', 4)

    child_failure = f'codex exited {rc}; answer withheld (see DETAILS)'
    sid = last = None
    pending_error = None
    turn_failure = None
    stream_error = None
    try:
        with events_path.open(encoding='utf-8', errors='replace') as events:
            for line in events:  # Streaming: never accumulate the event stream.
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError('expected a JSON object')
                event_type = event.get('type')
                if event_type == 'turn.failed':
                    turn_failure = event
                elif event_type == 'error':
                    pending_error = event
                elif event_type == 'turn.completed':
                    # The CLI emits recoverable reconnect notices as error events.
                    pending_error = None
                elif event_type == 'thread.started':
                    sid = event.get('thread_id')
                elif event_type == 'item.completed':
                    item = event.get('item')
                    if not isinstance(item, dict):
                        raise ValueError('expected an item object')
                    if item.get('type') == 'agent_message':
                        last = item.get('text')
    except (OSError, ValueError) as exc:
        stream_error = exc

    reported_error = turn_failure or pending_error
    if reported_error is not None:
        summary = 'codex reported an error'
        if rc != 0:
            summary += f' (exit {rc})'
        for value in (reported_error.get('error'), reported_error.get('message')):
            if isinstance(value, dict):
                value = value.get('message')
            if isinstance(value, str) and value.strip():
                summary += f': {value.strip()}'
                break
        return report_failure(
            details, stderr_path,
            f'{summary}; answer withheld (see events.jsonl in DETAILS)', 3,
        )
    if rc != 0:
        return report_failure(details, stderr_path, child_failure, 3)
    if stream_error is not None:
        return report_failure(details, stderr_path, f'malformed event stream: {stream_error} (see DETAILS)', 4)
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
