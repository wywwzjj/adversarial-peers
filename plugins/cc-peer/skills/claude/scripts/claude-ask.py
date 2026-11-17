#!/usr/bin/env python3
"""Single-file wrapper for `claude -p --output-format json` used by the claude skill.

Stdout prints ONLY: the result text, a SESSION_ID=<id> line, and a DETAILS=<dir>
line. The full JSON envelope (`envelope.json`: usage, cost, permission_denials,
model info) and `stderr.log` are persisted in DETAILS for on-demand inspection
instead of flooding context. Stderr announces DETAILS before launch.

Usage (flags pass through to `claude -p` verbatim):
    python3 claude-ask.py <<'PEER_PROMPT_END'
    multi-line prompt
    PEER_PROMPT_END
    python3 claude-ask.py --resume <SESSION_ID> <<'PEER_PROMPT_END' ...

Exit codes:
  0  success
  3  claude reported is_error=true (error text on stderr)
  4  no usable JSON envelope (died early, malformed JSON, missing/empty
     result or session_id, or nonzero child exit despite success JSON)

Failed calls emit neither an answer nor SESSION_ID.
Requires: Claude Code CLI (verified v2.1.206), Python 3.9+.
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
    details = Path(tempfile.mkdtemp(prefix='claude-ask.', dir='/tmp' if os.name == 'posix' else None))
    envelope_path = details / 'envelope.json'
    stderr_path = details / 'stderr.log'

    try:
        with envelope_path.open('w', encoding='utf-8') as envelope_out, stderr_path.open('w', encoding='utf-8') as stderr_out:
            sys.stderr.write(f'DETAILS={details}\n')
            sys.stderr.flush()
            rc = subprocess.call(
                ['claude', '-p', '--output-format', 'json', *sys.argv[1:]],
                stdout=envelope_out, stderr=stderr_out,
            )
    except OSError as exc:
        return report_failure(details, stderr_path, f'claude -p failed: {exc}', 4)

    try:
        with envelope_path.open(encoding='utf-8', errors='replace') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return report_failure(details, stderr_path, 'no usable JSON envelope (see envelope.json in DETAILS)', 4)

    if not isinstance(d, dict):
        return report_failure(details, stderr_path, 'malformed envelope: expected a JSON object', 4)
    if d.get('is_error'):
        return report_failure(details, stderr_path, str(d.get('result') or 'claude returned is_error=true'), 3)
    if rc != 0:
        return report_failure(details, stderr_path, f'claude exited {rc} despite a success envelope; answer withheld', 4)

    res, sid = d.get('result'), d.get('session_id')
    if not isinstance(res, str) or not res.strip() or not isinstance(sid, str) or not sid.strip():
        return report_failure(details, stderr_path, 'malformed envelope: missing result or session_id (see envelope.json in DETAILS)', 4)

    print(res.rstrip('\n'))
    print()
    print(f'SESSION_ID={sid}')
    print(f'DETAILS={details}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
