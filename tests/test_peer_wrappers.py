"""Check the shared peer result contract without invoking a real agent."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGINS = {'codex': 'codex-peer', 'claude': 'cc-peer', 'agy': 'agy-peer'}


def success(peer):
    if peer == 'codex':
        return '\n'.join(json.dumps(event) for event in (
            {'type': 'thread.started', 'thread_id': 'session-1'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'ANSWER'}},
            {'type': 'turn.completed', 'usage': {}},
        )) + '\n'
    if peer == 'claude':
        return json.dumps({'is_error': False, 'result': 'ANSWER', 'session_id': 'session-1'})
    return '\n'.join(json.dumps(event) for event in (
        {'event': 'init', 'conversation_id': 'session-1'},
        {'event': 'result', 'result': {'status': 'SUCCESS', 'response': 'ANSWER', 'conversation_id': 'session-1'}},
    )) + '\n'


class PeerWrapperTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='peer wrapper tests ')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.workdir = self.root / 'project'
        self.workdir.mkdir()
        self.capture = self.root / 'capture.json'
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        TMPDIR=str(self.root), PEER_TEST_CAPTURE=str(self.capture))
        for key in ('CLAUDE_SKILL_DIR', 'CODEX_SKILL_DIR', 'CLAUDE_PLUGIN_ROOT', 'CODEX_PLUGIN_ROOT'):
            self.env.pop(key, None)
        self.scripts = {}
        for peer, plugin in PLUGINS.items():
            fake = self.bin / peer
            fake.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, time
out = os.environ['PEER_TEST_OUTPUT']
if '--input-format=stream-json' in sys.argv:
    lines = out.splitlines(keepends=True)
    if lines and '"init"' in lines[0]:
        sys.stdout.write(lines[0])
        sys.stdout.flush()
        stdin = sys.stdin.read()
        rest = ''.join(lines[1:])
    else:
        stdin = ''
        rest = out
else:
    stdin = sys.stdin.read()
    rest = out
pathlib.Path(os.environ['PEER_TEST_CAPTURE']).write_text(json.dumps({
    'argv': sys.argv[1:], 'stdin': stdin, 'cwd': os.getcwd(),
}))
gate = os.environ.get('PEER_TEST_WAIT_FILE')
while gate and not pathlib.Path(gate).exists():
    time.sleep(0.01)
sys.stdout.write(rest)
sys.stderr.write(os.environ.get('PEER_TEST_STDERR', 'fake peer diagnostic\\n'))
sys.exit(int(os.environ['PEER_TEST_EXIT']))
''')
            fake.chmod(0o755)
            source = ROOT / 'plugins' / plugin / 'skills' / peer / 'scripts'
            dest = self.root / 'cache' / plugin / 'skills' / peer / 'scripts'
            shutil.copytree(source, dest)
            self.scripts[peer] = dest / (peer + '-ask.py')

    def run_peer(self, peer, *args, output=None, status=0, prompt='Review this.\n'):
        env = dict(self.env, PEER_TEST_OUTPUT=success(peer) if output is None else output,
                   PEER_TEST_EXIT=str(status))
        result = subprocess.run(['python3', str(self.scripts[peer]), *args], input=prompt,
                                text=True, capture_output=True, env=env, cwd=self.workdir, timeout=10)
        if 'DETAILS=' in result.stdout:
            self.details(result)
        return result

    def details(self, result):
        path = Path(result.stdout.split('DETAILS=', 1)[1].strip())
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path

    def assert_failure(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('ANSWER', result.stdout)
        self.assertNotIn('SESSION_ID=', result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('DETAILS=', result.stdout)
        self.assertIn('fake peer diagnostic', result.stderr)

    def test_success_contract_prompt_scope_and_saved_logs(self):
        prompt = '--looks-like-a-flag\nEOF\n"quotes" $HOME `literal`\nsecond line\n'
        for peer in PLUGINS:
            with self.subTest(peer=peer):
                result = self.run_peer(peer, prompt=prompt)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.startswith('ANSWER\n\nSESSION_ID=session-1\nDETAILS='))
                details = self.details(result)
                if os.name == 'posix':
                    self.assertEqual(details.parent, Path('/tmp'))
                self.assertEqual(result.stderr, f'DETAILS={details}\n')
                log = 'envelope.json' if peer == 'claude' else 'events.jsonl'
                self.assertEqual((details / log).read_text(), success(peer))
                self.assertIn('fake peer diagnostic', (details / 'stderr.log').read_text())
                call = json.loads(self.capture.read_text())
                self.assertEqual(Path(call['cwd']).resolve(), self.workdir.resolve())
                if peer == 'agy':
                    self.assertEqual(json.loads(call['stdin']), {
                        'event': 'user',
                        'message': {'role': 'user', 'content': prompt.rstrip('\n')},
                    })
                    self.assertIn('--print=', call['argv'])
                    self.assertEqual(json.loads((details / 'envelope.json').read_text())['status'], 'SUCCESS')
                else:
                    self.assertEqual(call['stdin'], prompt)
                self.assertNotIn('--model', call['argv'])
                self.assertNotIn('--effort', call['argv'])
                self.assertNotIn('--sandbox', call['argv'])

    def test_log_directory_is_available_before_peer_finishes(self):
        for peer in PLUGINS:
            with self.subTest(peer=peer):
                gate = self.root / (peer + '.release')
                env = dict(self.env, PEER_TEST_OUTPUT=success(peer), PEER_TEST_EXIT='0',
                           PEER_TEST_WAIT_FILE=str(gate))
                process = subprocess.Popen(['python3', str(self.scripts[peer])],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True, cwd=self.workdir, env=env)
                process.stdin.write('Review this.\n')
                process.stdin.close()
                process.stdin = None
                announcements = queue.Queue()

                def read_announcement():
                    announcements.put(process.stderr.readline())

                reader = threading.Thread(target=read_announcement, daemon=True)
                reader.start()
                try:
                    try:
                        line = announcements.get(timeout=3)
                    except queue.Empty:
                        self.fail('wrapper did not announce its directory while the peer was running')
                    self.assertIsNone(process.poll())
                    self.assertTrue(line.startswith('DETAILS='), line)
                    directory = Path(line.strip().removeprefix('DETAILS='))
                    self.assertTrue(directory.is_dir())
                    gate.write_text('finish')
                    reader.join(timeout=3)
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 0, stderr)
                    self.assertTrue(stdout.startswith('ANSWER\n\nSESSION_ID=session-1\nDETAILS='))
                    self.assertEqual(self.details(subprocess.CompletedProcess([], 0, stdout, stderr)), directory)
                finally:
                    gate.write_text('finish')
                    reader.join(timeout=3)
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=5)

    def test_explicit_model_override_is_forwarded(self):
        for peer in PLUGINS:
            with self.subTest(peer=peer):
                result = self.run_peer(peer, '--model', 'user-selected')
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads(self.capture.read_text())['argv']
                self.assertEqual(args[args.index('--model') + 1], 'user-selected')

    def test_nonzero_exit_with_success_output_withholds_answer_and_id(self):
        for peer in PLUGINS:
            with self.subTest(peer=peer):
                result = self.run_peer(peer, status=9)
                self.assert_failure(result)
                log = 'envelope.json' if peer == 'claude' else 'events.jsonl'
                self.assertEqual((self.details(result) / log).read_text(), success(peer))

    def test_non_object_json_is_a_clean_failure(self):
        for peer in PLUGINS:
            for output in ('[]', 'null', '123', '"text"'):
                with self.subTest(peer=peer, output=output):
                    self.assert_failure(self.run_peer(peer, output=output))

    def test_missing_or_invalid_session_id_withholds_answer(self):
        for peer in PLUGINS:
            for sid in (None, 123, '', ' \t'):
                with self.subTest(peer=peer, sid=sid):
                    if peer == 'codex':
                        events = [json.loads(line) for line in success(peer).splitlines()]
                        events[0]['thread_id'] = sid
                        output = '\n'.join(map(json.dumps, events))
                    elif peer == 'agy':
                        events = [json.loads(line) for line in success(peer).splitlines()]
                        events[1]['result']['conversation_id'] = sid
                        output = '\n'.join(map(json.dumps, events)) + '\n'
                    else:
                        data = json.loads(success(peer))
                        data['session_id'] = sid
                        output = json.dumps(data)
                    self.assert_failure(self.run_peer(peer, output=output))

    def test_empty_or_whitespace_answer_withholds_session_id(self):
        for peer in PLUGINS:
            for blank in ('', ' \t\n'):
                with self.subTest(peer=peer, blank=blank):
                    if peer == 'codex':
                        events = [json.loads(line) for line in success(peer).splitlines()]
                        events[1]['item']['text'] = blank
                        output = '\n'.join(map(json.dumps, events))
                    elif peer == 'agy':
                        events = [json.loads(line) for line in success(peer).splitlines()]
                        events[1]['result']['response'] = blank
                        output = '\n'.join(map(json.dumps, events)) + '\n'
                    else:
                        data = json.loads(success(peer))
                        data['result'] = blank
                        output = json.dumps(data)
                    self.assert_failure(self.run_peer(peer, output=output))

    def test_invalid_json_withholds_answer(self):
        for peer in PLUGINS:
            with self.subTest(peer=peer):
                self.assert_failure(self.run_peer(peer, output='not JSON'))

    def test_reported_error_withholds_answer_even_with_zero_exit(self):
        outputs = {
            'codex': success('codex') + json.dumps({'type': 'turn.failed', 'error': {'message': 'failed'}}),
            'claude': json.dumps({'is_error': True, 'result': 'failed', 'session_id': 'session-1'}),
            'agy': json.dumps({'event': 'result', 'result': {
                'status': 'ERROR', 'error': 'failed', 'conversation_id': 'session-1',
            }}) + '\n',
        }
        for peer, output in outputs.items():
            with self.subTest(peer=peer):
                self.assert_failure(self.run_peer(peer, output=output))

    def test_codex_returns_answer_after_successful_reconnect(self):
        events = success('codex').splitlines(keepends=True)
        notice = json.dumps({'type': 'error', 'message': 'Reconnecting... 1/5 (stream disconnected)'})
        events.insert(1, notice + '\n')
        output = ''.join(events)

        result = self.run_peer('codex', output=output)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith('ANSWER\n\nSESSION_ID=session-1\nDETAILS='))
        self.assertEqual((self.details(result) / 'events.jsonl').read_text(), output)

    def test_codex_reports_terminal_failure_after_reconnect(self):
        output = '\n'.join(map(json.dumps, (
            {'type': 'thread.started', 'thread_id': 'session-1'},
            {'type': 'error', 'message': 'Reconnecting... 1/5 (stream disconnected)'},
            {'type': 'turn.failed', 'error': {'message': 'request rejected'}},
        ))) + '\n'

        result = self.run_peer('codex', output=output, status=1)

        self.assert_failure(result)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn('request rejected', result.stderr)

    def test_codex_uses_final_message_without_exposing_tool_output(self):
        output = '\n'.join(map(json.dumps, (
            {'type': 'thread.started', 'thread_id': 'session-1'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'intermediate'}},
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'aggregated_output': 'PRIVATE'}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'ANSWER'}},
        )))
        result = self.run_peer('codex', output=output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('PRIVATE', result.stdout)
        self.assertNotIn('intermediate', result.stdout)
        self.assertIn('PRIVATE', (self.details(result) / 'events.jsonl').read_text())

    def test_codex_missing_or_malformed_final_message(self):
        for item in (None, [], {'type': 'agent_message', 'text': 123}):
            with self.subTest(item=item):
                output = json.dumps({'type': 'thread.started', 'thread_id': 'session-1'}) + '\n'
                output += json.dumps({'type': 'item.completed', 'item': item})
                self.assert_failure(self.run_peer('codex', output=output))

    def test_codex_reports_event_error_when_child_stderr_is_empty(self):
        self.env['PEER_TEST_STDERR'] = ''
        cases = (
            ({'type': 'error', 'message': 'connection failed'}, 'connection failed'),
            ({'type': 'turn.failed', 'error': {'message': 'quota exceeded'}}, 'quota exceeded'),
            ({'type': 'turn.failed', 'error': 'request rejected'}, 'request rejected'),
            ({'type': 'turn.failed', 'error': {'code': 123}}, 'codex reported an error'),
        )
        for status in (0, 7):
            for event, message in cases:
                with self.subTest(status=status, event=event):
                    output = success('codex') + json.dumps(event) + '\n'
                    result = self.run_peer('codex', status=status, output=output)
                    self.assertEqual(result.returncode, 3, result.stderr)
                    self.assertIn(message, result.stderr)
                    self.assertNotIn('ANSWER', result.stdout)
                    self.assertNotIn('SESSION_ID=', result.stdout)
                    self.assertNotIn('Traceback', result.stderr)
                    details = self.details(result)
                    self.assertEqual((details / 'stderr.log').read_text(), '')
                    self.assertEqual((details / 'events.jsonl').read_text(), output)

    def test_codex_nonzero_exit_keeps_child_failure_code_for_unusable_stream(self):
        for output in ('not JSON', '[]', success('codex') + '{"type":"turn.failed"'):
            with self.subTest(output=output):
                result = self.run_peer('codex', status=7, output=output)
                self.assert_failure(result)
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn('codex exited 7', result.stderr)
                self.assertEqual((self.details(result) / 'events.jsonl').read_text(), output)

    def test_claude_structured_error_does_not_traceback(self):
        output = json.dumps({'is_error': True, 'result': {'message': 'failed'}})
        self.assert_failure(self.run_peer('claude', output=output))


if __name__ == '__main__':
    unittest.main()
