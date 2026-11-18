#!/usr/bin/env python3
"""Single-file wrapper for `agy` (Google Antigravity CLI) used by the agy skill.

Stdout prints ONLY: the response text, a SESSION_ID=<id> line, and a DETAILS=<dir>
line. Stderr announces DETAILS before agy starts.
The event stream (events.jsonl), final JSON envelope (envelope.json),
agy's debug log (agy.log, including the resolved model), and stderr.log are
persisted in DETAILS for on-demand inspection instead of flooding context.

Both fresh and resumed calls run over stream-json input/output with `--print=`:
    python3 agy-ask.py <<'PROMPT'
    multi-line prompt
    PROMPT
    python3 agy-ask.py --conversation <SESSION_ID> <<'PROMPT' ...

Reading the prompt from STDIN and sending it as NDJSON after `init` avoids:
  - `-p` swallowing the next flag as the prompt or merging piped stdin;
  - bare piped stdin falling back to the interactive TUI when empty;
  - OS argv size limits on large prompts;
  - agy silently starting a fresh conversation when `--conversation <ID>` is
    unknown (this script checks `init.conversation_id` in the same process
    before sending the prompt and exits 5 on mismatch with no task sent).

Exit codes:
  0  success
  2  usage error (empty prompt / conflicting or wrapper-owned flag)
  3  agy reported status != SUCCESS (error text on stderr)
  4  stream or envelope failure (died early, malformed JSON, missing/empty
     fields, or nonzero child exit despite SUCCESS)
  5  requested and actual conversation IDs differ
  130 / 143  interrupted by SIGINT / SIGTERM (DETAILS retained)

Failed calls emit neither an answer nor SESSION_ID.
Requires: agy (stream-json verified on v1.5.9 / v1.5.10), Python 3.9+.
"""
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time


OWNED = {
    'p', 'print', 'prompt', 'output-format', 'log-file',
    'input-format', 'i', 'prompt-interactive',
}
BOOL_FLAGS = {
    'c', 'continue', 'dangerously-skip-permissions', 'disable-slash-commands',
    'new-project', 'remote-agent', 'remote-control', 'sandbox',
}
VALUE_FLAGS = {
    'add-dir', 'agent', 'effort', 'json-schema', 'mode', 'model',
    'print-timeout', 'project', 'settings',
}


class Failure(Exception):
    def __init__(self, message, code=4):
        super().__init__(message)
        self.code = code


def usage_error(msg):
    sys.stderr.write(f'agy-ask.py: {msg}\n')
    sys.exit(2)


def parse_args(argv):
    wanted = ''
    continue_seen = False
    flags = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--':
            usage_error('-- is unsupported; supply only flags and read the prompt from stdin')
        if not arg.startswith('-') or arg == '-':
            usage_error('unexpected positional argument; read the prompt from stdin')
        head, has_eq, inline_val = arg.partition('=')
        name = head[2:] if head.startswith('--') else head[1:]
        if name in OWNED:
            usage_error('prompt/input/output/log and interactive flags are managed by the wrapper')
        if name == 'conversation':
            if wanted:
                usage_error('pass only one conversation ID')
            if has_eq:
                val = inline_val
            else:
                if i + 1 >= len(argv):
                    usage_error('--conversation requires an ID')
                i += 1
                val = argv[i]
            if not val.strip():
                usage_error('conversation ID must not be empty')
            if val.startswith('-'):
                usage_error('conversation ID must not be a flag')
            wanted = val
        elif name in ('c', 'continue'):
            continue_seen = True
            flags.append(arg)
        elif name in VALUE_FLAGS:
            if not has_eq:
                if i + 1 >= len(argv) or argv[i + 1].startswith('-'):
                    usage_error(f'{head} requires a value; use {head}=VALUE for values starting with -')
                i += 1
                inline_val = argv[i]
            if not inline_val.strip():
                usage_error(f'{head} requires a nonempty value')
            flags.append(arg)
            if not has_eq:
                flags.append(inline_val)
        elif name in BOOL_FLAGS:
            flags.append(arg)
        else:
            if not has_eq:
                usage_error(f'unlisted flag {head}: use {head}=VALUE (or =true for a boolean)')
            flags.append(arg)
        i += 1
    if wanted and continue_seen:
        usage_error('do not combine --conversation with --continue')
    return wanted, flags


def stop_process(process):
    """Reap agy and stop its remaining descendants on failure or cancellation."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, signal.SIG_IGN)
    if os.name == 'posix':
        def signal_group(sig):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                # macOS can report EPERM for a group whose only member is an
                # unreaped zombie. Reap and retry, preserving real EPERM errors.
                if process.poll() is None:
                    raise
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass

        signal_group(signal.SIGTERM)
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            pass
        # The leader may have exited while a descendant ignored SIGTERM.
        signal_group(signal.SIGKILL)
    elif process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
    process.wait()


def report_failure(details, stderr_path, log_path, message, code):
    print(f'DETAILS={details}', flush=True)
    sys.stderr.write(f'{message}\n')
    sys.stderr.write(f'--- stderr tail (full log: {stderr_path}; agy debug log: {log_path}) ---\n')
    try:
        for line in stderr_path.read_text(encoding='utf-8', errors='replace').splitlines()[-40:]:
            sys.stderr.write(f'{line}\n')
    except OSError:
        pass
    return code


def read_output(pipe, events):
    """Frame output independently of stdin writes and inherited pipe lifetime."""
    pending = b''
    try:
        while True:
            chunk = pipe.read(65536)
            if not chunk:
                if pending:
                    events.put(('line', pending.decode('utf-8', errors='replace')))
                events.put(('eof', None))
                return
            lines = (pending + chunk).split(b'\n')
            pending = lines.pop()
            for line in lines:
                events.put(('line', (line + b'\n').decode('utf-8', errors='replace')))
    except (OSError, ValueError) as exc:
        events.put(('output_error', str(exc)))


def write_prompt(pipe, payload, events, errors):
    """Keep draining stdout even when agy stops reading a large prompt."""
    try:
        remaining = memoryview(payload)
        while remaining:
            written = pipe.write(remaining)
            if not written:
                raise OSError('stdin write made no progress')
            remaining = remaining[written:]
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        events.put(('input_error', str(exc)))
    finally:
        pipe.close()


def main():
    wanted, flags = parse_args(sys.argv[1:])
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    prompt = sys.stdin.read()
    if not prompt.strip():
        usage_error('empty prompt — pipe it in via heredoc')
    prompt = prompt.rstrip('\n')

    details = Path(tempfile.mkdtemp(prefix='agy-ask.', dir='/tmp' if os.name == 'posix' else None))
    events_path = details / 'events.jsonl'
    envelope_path = details / 'envelope.json'
    stderr_path = details / 'stderr.log'
    log_path = details / 'agy.log'

    def interrupted(signum, _frame):
        raise Failure(f'interrupted by {signal.Signals(signum).name}; see DETAILS to recover the session', 128 + signum)

    previous_handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with events_path.open('w', encoding='utf-8') as events_out, stderr_path.open('w', encoding='utf-8') as stderr_out:
            sys.stderr.write(f'DETAILS={details}\n')
            sys.stderr.flush()
            selectors = [f'--conversation={wanted}'] if wanted else []
            process = subprocess.Popen(
                ['agy', '--print=', '--output-format=stream-json', '--input-format=stream-json',
                 '--log-file', str(log_path), *selectors, *flags],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr_out,
                # Raw pipes can be closed without waiting for a reader's buffered
                # I/O lock when a successful child's descendant retains stdout.
                bufsize=0,
                start_new_session=(os.name == 'posix'),
            )
            completed = False
            reader = None
            writer = None
            try:
                init_id = None
                result = None
                input_errors = []
                events = queue.Queue()
                reader = threading.Thread(target=read_output, args=(process.stdout, events), daemon=True)
                reader.start()
                drain_deadline = None
                while True:
                    # This is an exit/transport grace, never a reasoning timeout.
                    # An exited leader's descendants may keep stdout open forever.
                    if process.poll() is not None and drain_deadline is None:
                        drain_deadline = time.monotonic() + 5
                    if drain_deadline is not None and time.monotonic() >= drain_deadline:
                        raise Failure('agy exited or stdin failed but the stream did not end or return a result within 5s; answer withheld')
                    try:
                        kind, line = events.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if kind == 'input_error':
                        if drain_deadline is None:
                            drain_deadline = time.monotonic() + 5
                        continue
                    if kind == 'output_error':
                        raise Failure(f'could not read agy output: {line}')
                    if kind == 'eof':
                        break
                    events_out.write(line)
                    events_out.flush()
                    try:
                        event = json.loads(line)
                    except ValueError as exc:
                        state = 'after init; task may have been sent' if init_id else 'before init; task was NOT sent'
                        raise Failure(f'invalid stream JSON {state} (see events.jsonl in DETAILS)') from exc
                    if not isinstance(event, dict):
                        raise Failure('stream event must be a JSON object')
                    kind = event.get('event')
                    if kind == 'init':
                        if init_id is not None:
                            raise Failure('duplicate stream init event')
                        actual = event.get('conversation_id')
                        if wanted and actual != wanted:
                            raise Failure(
                                f'conversation {wanted} was not resumed (init ID: {actual!r}); task was NOT sent',
                                5,
                            )
                        if not isinstance(actual, str) or not actual.strip():
                            raise Failure('missing conversation_id in stream init; task was NOT sent')
                        init_id = actual
                        msg = {'event': 'user', 'message': {'role': 'user', 'content': prompt}}
                        payload = (json.dumps(msg) + '\n').encode('utf-8')
                        writer = threading.Thread(target=write_prompt,
                                                  args=(process.stdin, payload, events, input_errors), daemon=True)
                        writer.start()
                    elif kind == 'result':
                        if result is not None or not isinstance(event.get('result'), dict):
                            raise Failure('invalid stream result envelope')
                        env = event['result']
                        if init_id is None and env.get('status') != 'ERROR':
                            raise Failure('result arrived before init; task was NOT sent')
                        result = env
                        envelope_path.write_text(json.dumps(result), encoding='utf-8')
                        # A background descendant can retain stdout after agy exits.
                        # The final result ends this single-turn stream; EOF is unnecessary.
                        break
                # Pre-init errors can wait for input EOF before exiting. A result
                # also means no more prompt bytes should be sent to this process.
                process.stdin.close()
                try:
                    rc = process.wait(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    raise Failure('agy did not exit within 5s after the stream ended or returned a result; answer withheld') from exc
                if result is None:
                    if init_id is None:
                        raise Failure('no stream init event; task was NOT sent (see stderr.log in DETAILS)')
                    raise Failure('stream ended without a result envelope')
                status_val = result.get('status')
                if status_val != 'SUCCESS':
                    raise Failure(str(result.get('error') or f'agy returned status={status_val!r}'), 3)
                if rc != 0:
                    raise Failure(f'agy exited {rc} despite a SUCCESS envelope; answer withheld')
                if writer is not None:
                    writer.join(timeout=1)
                if writer is None or writer.is_alive() or input_errors:
                    raise Failure('agy returned SUCCESS without receiving the complete prompt; answer withheld')
                res, sid = result.get('response'), result.get('conversation_id')
                if not isinstance(res, str) or not res.strip() or not isinstance(sid, str) or not sid.strip():
                    raise Failure('malformed envelope: missing response or conversation_id (see envelope.json in DETAILS)')
                if wanted and sid != wanted:
                    raise Failure(
                        f'result conversation ID ({sid!r}) does not match {wanted}; task was sent; answer withheld',
                        5,
                    )
                if init_id and sid != init_id:
                    raise Failure(f'result conversation ID ({sid!r}) does not match init ID ({init_id!r}); answer withheld')
                completed = True
            finally:
                if not completed:
                    stop_process(process)
                for pipe in (process.stdin, process.stdout):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except OSError:
                            pass
                for worker in (writer, reader):
                    if worker is not None:
                        worker.join(timeout=0.1)

        print(res.rstrip('\n'))
        print()
        print(f'SESSION_ID={sid}')
        print(f'DETAILS={details}')
        return 0
    except Failure as exc:
        return report_failure(details, stderr_path, log_path, str(exc), exc.code)
    except OSError as exc:
        return report_failure(details, stderr_path, log_path, f'agy stream failed: {exc}', 4)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
