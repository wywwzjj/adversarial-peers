"""Exercise the single-file agy wrapper's stream-json boundary without network or model calls."""
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from unittest import mock


WRAPPER = Path(__file__).resolve().parents[1] / 'plugins/agy-peer/skills/agy/scripts/agy-ask.py'


class AgyAskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agy wrapper tests ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake = self.bin / 'agy'
        fake.write_text('''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
capture = pathlib.Path(os.environ['AGY_TEST_CAPTURE'])
call = {'argv': sys.argv[1:], 'stdin': '', 'cwd': os.getcwd(), 'pid': os.getpid()}
def record():
    capture.write_text(json.dumps(call))
record()
raw = os.environ['AGY_TEST_OUTPUT']
mode = os.environ.get('AGY_TEST_STREAM_MODE', 'normal')
if mode == 'raw':
    sys.stdout.write(raw)
elif mode == 'unsupported':
    sys.stderr.write('unknown input format\\n')
    sys.exit(2)
elif mode == 'bad_init':
    print('not json', flush=True)
else:
    result = json.loads(raw)
    if mode in ('no_init', 'no_init_wait_eof'):
        if isinstance(result, dict):
            print(json.dumps({'event': 'result', 'result': result}), flush=True)
        else:
            print(json.dumps(result), flush=True)
        if mode == 'no_init_wait_eof':
            call['stdin'] = sys.stdin.read()
            record()
    else:
        default_sid = result.get('conversation_id', 'session-1') if isinstance(result, dict) else 'session-1'
        sid = os.environ.get('AGY_TEST_INIT_ID', default_sid)
        if mode == 'closed_stdin_error':
            os.close(sys.stdin.fileno())
        print(json.dumps({'event': 'init', 'conversation_id': sid}), flush=True)
        if mode == 'closed_stdin_error':
            print(json.dumps({'event': 'result', 'result': result}), flush=True)
            sys.exit(1)
        if mode == 'stdout_before_read':
            print(json.dumps({'event': 'step_update', 'padding': 'x' * 1048576}), flush=True)
        call['stdin'] = sys.stdin.read()
        record()
        if call['stdin']:
            # Only receiving a real task may cause a delegated file write.
            pathlib.Path(os.environ['AGY_TEST_SIDE_EFFECT']).write_text('task executed')
            if mode in ('held_stdout', 'held_stdout_no_result', 'held_stdout_partial_result', 'interrupt_wait'):
                child_code = """
import fcntl, os, pathlib, signal, time
lock = open(os.environ['AGY_TEST_CHILD_LOCK'], 'w')
fcntl.flock(lock, fcntl.LOCK_EX)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path(os.environ['AGY_TEST_CHILD_READY']).write_text(str(os.getpid()))
time.sleep(60)
"""
                subprocess.Popen([sys.executable, '-c', child_code], stdin=subprocess.DEVNULL)
                while not pathlib.Path(os.environ['AGY_TEST_CHILD_READY']).exists():
                    time.sleep(0.01)
                record()
            if mode == 'interrupt_wait':
                time.sleep(60)
            if mode == 'duplicate_init':
                print(json.dumps({'event': 'init', 'conversation_id': sid}), flush=True)
            if mode == 'bad_result':
                print(json.dumps({'event': 'result', 'result': result}), flush=True)
            elif mode == 'held_stdout_partial_result':
                sys.stdout.write('{"event": "result"')
                sys.stdout.flush()
            elif mode not in ('no_result', 'held_stdout_no_result'):
                print(json.dumps({'event': 'result', 'result': result}), flush=True)
                if mode == 'result_hang':
                    time.sleep(60)
sys.stderr.write('fake agy diagnostic\\n')
sys.exit(int(os.environ.get('AGY_TEST_EXIT', '0')))
''')
        fake.chmod(0o755)
        # Mimic a versioned install cache with spaces, outside the working directory.
        self.script = self.root / 'cache/agy-peer/1.0.0/skills/agy/scripts/agy-ask.py'
        shutil.copytree(WRAPPER.parent, self.script.parent,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        self.workdir = self.root / 'project'
        self.workdir.mkdir()
        self.capture = self.root / 'capture.json'
        self.side_effect = self.workdir / 'task-ran.txt'
        self.child_ready = self.root / 'child.ready'
        self.child_lock = self.root / 'child.lock'
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        TMPDIR=str(self.root), AGY_TEST_CAPTURE=str(self.capture),
                        AGY_TEST_SIDE_EFFECT=str(self.side_effect),
                        AGY_TEST_CHILD_READY=str(self.child_ready), AGY_TEST_CHILD_LOCK=str(self.child_lock))
        self.addCleanup(self.stop_test_child)
        for key in ('CLAUDE_SKILL_DIR', 'CODEX_SKILL_DIR', 'CLAUDE_PLUGIN_ROOT', 'CODEX_PLUGIN_ROOT'):
            self.env.pop(key, None)

    def stop_test_child(self):
        if self.child_ready.exists():
            try:
                os.kill(int(self.child_ready.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def run_wrapper(self, *args, output=None, exit_code=0, prompt='Review this.\n', timeout=10):
        if output is None:
            output = {'status': 'SUCCESS', 'response': 'ANSWER', 'conversation_id': 'session-1'}
        env = dict(self.env,
                   AGY_TEST_OUTPUT=output if isinstance(output, str) else json.dumps(output),
                   AGY_TEST_EXIT=str(exit_code))
        result = subprocess.run(['python3', str(self.script), *args], input=prompt, text=True,
                                capture_output=True, env=env, cwd=self.workdir, timeout=timeout)
        if 'DETAILS=' in result.stdout:
            self.details(result)
        return result

    def details(self, result):
        path = Path(result.stdout.split('DETAILS=', 1)[1].strip())
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path

    def assert_failure(self, result, code):
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertNotIn('SESSION_ID=', result.stdout)
        self.assertNotIn('ANSWER', result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('DETAILS=', result.stdout)

    def assert_child_stopped(self):
        import fcntl
        self.assertTrue(self.child_ready.exists(), 'child never became ready')
        deadline = time.monotonic() + 2
        with self.child_lock.open('a') as lock:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        self.fail('background child still holds its lock after wrapper failure')
                    time.sleep(0.01)

    def test_single_file_layout(self):
        self.assertEqual([p.name for p in self.script.parent.iterdir()], ['agy-ask.py'])

    def test_prompt_forwarding_and_logs_without_host_variables(self):
        prompt = '--looks-like-a-flag\nEOF\n"quotes" $HOME `literal`\nsecond line\n'
        r = self.run_wrapper('--effort', 'high', prompt=prompt)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith('ANSWER\n\nSESSION_ID=session-1\nDETAILS='))
        call = json.loads(self.capture.read_text())
        messages = [json.loads(line) for line in call['stdin'].splitlines()]
        self.assertEqual(messages, [{'event': 'user', 'message': {
            'role': 'user', 'content': prompt.rstrip('\n'),
        }}])
        self.assertEqual(Path(call['cwd']).resolve(), self.workdir.resolve())
        self.assertIn('--print=', call['argv'])
        self.assertFalse(any('second line' in arg for arg in call['argv']))
        self.assertIn('--effort', call['argv'])
        self.assertEqual(call['argv'][call['argv'].index('--effort') + 1], 'high')
        details = self.details(r)
        self.assertEqual(json.loads((details / 'envelope.json').read_text())['status'], 'SUCCESS')
        events = [json.loads(line) for line in (details / 'events.jsonl').read_text().splitlines()]
        self.assertEqual([e['event'] for e in events], ['init', 'result'])
        self.assertIn('fake agy diagnostic', (details / 'stderr.log').read_text())
        self.assertEqual(call['argv'][call['argv'].index('--log-file') + 1], str(details / 'agy.log'))

    def test_large_prompt_avoids_bash_quadratic_trap_and_argv_limits(self):
        # 200 KB exceeds Linux MAX_ARG_STRLEN (128 KB) and hangs macOS /bin/bash 3.2 ${PROMPT//[[:space:]]/}.
        prompt = ('line with spaces and unicode 中文\n' * 6000)
        r = self.run_wrapper(prompt=prompt, timeout=5)
        self.assertEqual(r.returncode, 0, r.stderr)
        call = json.loads(self.capture.read_text())
        self.assertEqual(json.loads(call['stdin'])['message']['content'], prompt.rstrip('\n'))
        self.assertIn('--print=', call['argv'])

    def test_resume_by_id_both_flag_forms(self):
        for args in (('--conversation', 'session-1'), ('--conversation=session-1',),
                     ('-conversation', 'session-1'), ('-conversation=session-1',)):
            with self.subTest(args=args):
                r = self.run_wrapper(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('SESSION_ID=session-1', r.stdout)
                self.assertIn('--conversation=session-1', json.loads(self.capture.read_text())['argv'])

    def test_wrong_resume_withholds_answer_both_flag_forms(self):
        for args in (('--conversation', 'missing'), ('--conversation=missing',),
                     ('-conversation', 'missing'), ('-conversation=missing',)):
            with self.subTest(args=args):
                self.assert_failure(self.run_wrapper(*args), 5)
                self.assertEqual(json.loads(self.capture.read_text())['stdin'], '')
                self.assertFalse(self.side_effect.exists())

    @unittest.skipUnless(os.name == 'posix' and hasattr(os, 'WNOWAIT'),
                         'requires POSIX waitid without reaping')
    def test_wrong_resume_preserves_exit_code_when_cleanup_hits_zombie_group(self):
        # Force the child to exit after the stream loop's last poll. Emulate
        # macOS EPERM for its unreaped process group on other POSIX hosts too.
        driver = '''
import errno, os, runpy, sys
namespace = runpy.run_path(sys.argv.pop(1))
main = namespace['main']
stop_process = namespace['stop_process']
killpg = os.killpg
def stop_after_exit(process):
    process.kill()
    os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
    def zombie_killpg(pgid, sig):
        if pgid == process.pid and process.returncode is None:
            raise PermissionError(errno.EPERM, 'unreaped process group')
        return killpg(pgid, sig)
    os.killpg = zombie_killpg
    try:
        stop_process(process)
        assert process.returncode is not None, 'child was not reaped'
    finally:
        os.killpg = killpg
        process.wait()
main.__globals__['stop_process'] = stop_after_exit
sys.exit(main())
'''
        env = dict(self.env, AGY_TEST_OUTPUT=json.dumps({
            'status': 'SUCCESS', 'response': 'ANSWER', 'conversation_id': 'session-1',
        }))
        r = subprocess.run(['python3', '-c', driver, str(self.script), '--conversation=missing'],
                           input='Review this.\n', text=True, capture_output=True,
                           env=env, cwd=self.workdir, timeout=5)
        self.assert_failure(r, 5)
        self.assertIn('was not resumed', r.stderr)
        self.assertEqual(json.loads(self.capture.read_text())['stdin'], '')
        self.assertFalse(self.side_effect.exists())
        self.assertTrue((self.details(r) / 'events.jsonl').exists())

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX process groups')
    def test_cleanup_does_not_suppress_genuine_permission_errors(self):
        stop_process = runpy.run_path(str(self.script))['stop_process']
        # A live leader must not hide EPERM; nor may an exited leader hide
        # EPERM that persists after reaping (e.g. from surviving descendants).
        for returncode in (None, 0):
            with self.subTest(returncode=returncode):
                process = mock.Mock(pid=12345)
                process.poll.return_value = returncode
                handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
                try:
                    with mock.patch.object(os, 'killpg', side_effect=PermissionError('denied')):
                        with self.assertRaises(PermissionError):
                            stop_process(process)
                finally:
                    for sig, handler in handlers.items():
                        signal.signal(sig, handler)

    @unittest.skipUnless(os.name == 'posix' and hasattr(os, 'WNOWAIT'),
                         'requires POSIX waitid without reaping')
    def test_cleanup_reaps_exited_process_with_native_signals(self):
        stop_process = runpy.run_path(str(self.script))['stop_process']
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        process = subprocess.Popen(['python3', '-c', 'pass'], start_new_session=True)
        try:
            os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
            stop_process(process)
            self.assertEqual(process.returncode, 0)
        finally:
            process.wait()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)

    def test_pre_init_error_envelope_exits_3_and_saves_envelope(self):
        # Real agy emits a result ERROR event without init on invalid --model / --effort.
        self.env['AGY_TEST_STREAM_MODE'] = 'no_init'
        err_env = {'status': 'ERROR', 'error': 'invalid model selection', 'conversation_id': ''}
        for args in ((), ('--conversation=session-1', '--effort=bogus')):
            with self.subTest(args=args):
                r = self.run_wrapper(*args, output=err_env, exit_code=1)
                self.assert_failure(r, 3)
                self.assertIn('invalid model selection', r.stderr)
                self.assertEqual(json.loads(self.capture.read_text())['stdin'], '')
                self.assertFalse(self.side_effect.exists())
                saved = json.loads((self.details(r) / 'envelope.json').read_text())
                self.assertEqual(saved['status'], 'ERROR')

    def test_post_init_error_envelope(self):
        r = self.run_wrapper(output={'status': 'ERROR', 'error': 'bad model'}, exit_code=1)
        self.assert_failure(r, 3)
        self.assertIn('bad model', r.stderr)
        self.assertEqual(json.loads((self.details(r) / 'envelope.json').read_text())['status'], 'ERROR')

    def test_pre_init_error_closes_stdin_before_waiting_for_exit(self):
        self.env['AGY_TEST_STREAM_MODE'] = 'no_init_wait_eof'
        output = {'status': 'ERROR', 'error': 'invalid model selection', 'conversation_id': ''}
        r = self.run_wrapper(output=output, exit_code=1, timeout=8)
        self.assert_failure(r, 3)
        self.assertIn('invalid model selection', r.stderr)
        self.assertEqual(json.loads((self.details(r) / 'envelope.json').read_text()), output)
        self.assertEqual(json.loads(self.capture.read_text())['stdin'], '')

    def test_broken_stdin_preserves_error_envelope(self):
        self.env['AGY_TEST_STREAM_MODE'] = 'closed_stdin_error'
        output = {'status': 'ERROR', 'error': 'input rejected', 'conversation_id': 'session-1'}
        r = self.run_wrapper(output=output, prompt='x' * 1048576)
        self.assert_failure(r, 3)
        self.assertIn('input rejected', r.stderr)
        self.assertEqual(json.loads((self.details(r) / 'envelope.json').read_text()), output)

    def test_large_prompt_and_output_do_not_deadlock(self):
        self.env['AGY_TEST_STREAM_MODE'] = 'stdout_before_read'
        prompt = '输入中文\n' * 100000
        r = self.run_wrapper(prompt=prompt, timeout=8)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('SESSION_ID=session-1', r.stdout)
        self.assertEqual(json.loads(json.loads(self.capture.read_text())['stdin'])['message']['content'],
                         prompt.rstrip('\n'))

    def test_no_json(self):
        self.env['AGY_TEST_STREAM_MODE'] = 'raw'
        r = self.run_wrapper(output='usage: agy\n', exit_code=2)
        self.assert_failure(r, 4)
        self.assertIn('task was NOT sent', r.stderr)
        self.assertNotIn('envelope.json', r.stderr)

    def test_non_object_json(self):
        for output in ('[]', 'null', '123', '"text"'):
            with self.subTest(output=output, stage='stream'):
                self.env['AGY_TEST_STREAM_MODE'] = 'raw'
                self.assert_failure(self.run_wrapper(output=output + '\n'), 4)
            with self.subTest(output=output, stage='result'):
                self.env['AGY_TEST_STREAM_MODE'] = 'bad_result'
                self.assert_failure(self.run_wrapper(output=output), 4)

    def test_missing_or_empty_result_fields(self):
        for output in ({'status': 'SUCCESS', 'response': 'ANSWER'},
                       {'status': 'SUCCESS', 'response': 123, 'conversation_id': 'session-1'},
                       {'status': 'SUCCESS', 'response': '', 'conversation_id': 'session-1'},
                       {'status': 'SUCCESS', 'response': '  \n', 'conversation_id': 'session-1'}):
            with self.subTest(output=output):
                self.assert_failure(self.run_wrapper(output=output), 4)

    def test_nonzero_exit_with_success_json_withholds_answer(self):
        self.assert_failure(self.run_wrapper(exit_code=9), 4)

    def test_rejects_empty_prompt_before_call(self):
        r = self.run_wrapper(prompt=' \n\t')
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.capture.exists())

    def test_rejects_prompt_flags_before_call(self):
        for flag in ('-p', '--p', '-print', '--print', '-prompt', '--prompt',
                     '-p=hello', '--p=hello', '-print=hello', '--print=hello', '-prompt=hello', '--prompt=hello'):
            with self.subTest(flag=flag):
                r = self.run_wrapper(flag)
                self.assertEqual(r.returncode, 2)
                self.assertFalse(self.capture.exists())

    def test_rejects_output_and_log_overrides_before_call(self):
        for flag in ('--output-format', '--output-format=text', '--log-file', '--log-file=elsewhere',
                     '-output-format', '-output-format=text', '-log-file', '-log-file=elsewhere',
                     '-input-format=stream-json', '--input-format', '-i', '--i', '--prompt-interactive'):
            with self.subTest(flag=flag):
                r = self.run_wrapper(flag)
                self.assertEqual(r.returncode, 2)
                self.assertFalse(self.capture.exists())

    def test_resume_sends_one_task_after_confirmed_init(self):
        prompt = 'Continue "the fix".\n$HOME `literal`\n'
        r = self.run_wrapper('--conversation', 'session-1', prompt=prompt)
        self.assertEqual(r.returncode, 0, r.stderr)
        call = json.loads(self.capture.read_text())
        messages = [json.loads(line) for line in call['stdin'].splitlines()]
        self.assertEqual(messages, [{'event': 'user', 'message': {
            'role': 'user', 'content': prompt.rstrip('\n')}}])
        self.assertIn('--print=', call['argv'])
        self.assertIn('--conversation=session-1', call['argv'])
        self.assertFalse(any('Continue' in arg for arg in call['argv']))
        self.assertTrue(self.side_effect.exists())
        details = self.details(r)
        events = [json.loads(line) for line in (details / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(events[0]['event'], 'init')
        self.assertEqual(events[-1]['event'], 'result')

    def test_fails_closed_without_usable_init(self):
        for mode in ('no_init', 'bad_init', 'unsupported'):
            with self.subTest(mode=mode):
                self.env['AGY_TEST_STREAM_MODE'] = mode
                self.assert_failure(self.run_wrapper('--conversation=session-1'), 4)
                self.assertEqual(json.loads(self.capture.read_text())['stdin'], '')
                self.assertFalse(self.side_effect.exists())

    def test_resume_missing_init_id_withholds_task(self):
        self.env['AGY_TEST_INIT_ID'] = ''
        self.assert_failure(self.run_wrapper('--conversation=session-1'), 5)
        self.assertEqual(json.loads(self.capture.read_text())['stdin'], '')
        self.assertFalse(self.side_effect.exists())

    def test_missing_result_withholds_answer(self):
        self.env['AGY_TEST_STREAM_MODE'] = 'no_result'
        self.assert_failure(self.run_wrapper('--conversation=session-1'), 4)

    def test_duplicate_init_withholds_answer(self):
        self.env['AGY_TEST_STREAM_MODE'] = 'duplicate_init'
        self.assert_failure(self.run_wrapper('--conversation=session-1'), 4)

    def test_result_id_mismatch_is_rejected(self):
        self.env['AGY_TEST_INIT_ID'] = 'session-1'
        result = {'status': 'SUCCESS', 'response': 'ANSWER', 'conversation_id': 'other-session'}
        mismatch = self.run_wrapper('--conversation=session-1', output=result)
        self.assert_failure(mismatch, 5)
        self.assertIn('task was sent', mismatch.stderr)
        self.assert_failure(self.run_wrapper(output=result), 4)

    def test_resume_nonzero_child_exit_withholds_answer(self):
        self.assert_failure(self.run_wrapper('--conversation=session-1', exit_code=9), 4)

    def test_rejects_empty_missing_and_duplicate_conversation_ids(self):
        for args in (('--conversation',), ('--conversation', ''), ('-conversation=',),
                     ('--conversation=',), ('-conversation', '  \t'),
                     ('--conversation', '--model', 'model'),
                     ('--conversation=a', '--conversation=b')):
            with self.subTest(args=args):
                r = self.run_wrapper(*args)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(self.capture.exists())

    def test_rejects_conflicting_resume_selectors(self):
        for option in ('-c', '--c', '-continue', '--continue', '--continue=true'):
            with self.subTest(option=option):
                r = self.run_wrapper('--conversation=session-1', option)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(self.capture.exists())

    def test_continue_flag_is_forwarded(self):
        for option in ('-c', '--continue'):
            with self.subTest(option=option):
                r = self.run_wrapper(option)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(option, json.loads(self.capture.read_text())['argv'])

    def test_rejects_option_terminator_and_positional_prompt(self):
        for args in (('--', '-output-format=text'), ('stray prompt',), ('-c', 'stray prompt')):
            with self.subTest(args=args):
                r = self.run_wrapper(*args)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(self.capture.exists())

    def test_option_values_and_unlisted_flags_pass_through(self):
        r = self.run_wrapper('--model=--conversation=not-a-selector',
                             '--future-flag=val', '--future-bool=true', '--effort=high')
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = json.loads(self.capture.read_text())['argv']
        self.assertIn('--model=--conversation=not-a-selector', argv)
        self.assertIn('--future-flag=val', argv)
        self.assertIn('--future-bool=true', argv)

    def test_rejects_missing_values_before_starting_agy(self):
        for flag in ('--add-dir', '--project', '--model', '--effort', '--settings', '--print-timeout'):
            for args in ((flag,), (flag, '--conversation=session-1'),
                         (flag, '--continue'), (flag, ''), (flag + '=',)):
                with self.subTest(args=args):
                    r = self.run_wrapper(*args)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertFalse(self.capture.exists())

    def test_unlisted_flags_require_explicit_inline_values(self):
        for args in (('--future-value',), ('--future-value', '--conversation=session-1'),
                     ('--future-bool', 'stray', '--conversation=session-1')):
            with self.subTest(args=args):
                r = self.run_wrapper(*args)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(self.capture.exists())

    def test_wrapper_flags_and_resume_selector_precede_passthrough(self):
        r = self.run_wrapper('--future-flag=value', '--conversation=session-1', '--project', 'project')
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = json.loads(self.capture.read_text())['argv']
        self.assertLess(argv.index('--print='), argv.index('--future-flag=value'))
        self.assertLess(argv.index('--conversation=session-1'), argv.index('--future-flag=value'))
        self.assertEqual(argv[argv.index('--project') + 1], 'project')

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX process groups')
    def test_result_returns_when_background_child_keeps_stdout_open(self):
        self.env['AGY_TEST_STREAM_MODE'] = 'held_stdout'
        r = self.run_wrapper(timeout=5)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('ANSWER', r.stdout)
        self.assertTrue(self.child_ready.exists())

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX process groups and flock')
    def test_invalid_result_cleans_background_child_after_zero_exit(self):
        self.env['AGY_TEST_STREAM_MODE'] = 'held_stdout'
        for output, code in (
            ({'status': 'SUCCESS', 'conversation_id': 'session-1'}, 4),
            ({'status': 'ERROR', 'error': 'failed', 'conversation_id': 'session-1'}, 3),
            ({'status': 'SUCCESS', 'response': 'ANSWER', 'conversation_id': 'other-session'}, 5),
        ):
            with self.subTest(output=output):
                self.child_ready.unlink(missing_ok=True)
                self.env['AGY_TEST_INIT_ID'] = 'session-1'
                try:
                    r = self.run_wrapper('--conversation=session-1', output=output)
                    self.assert_failure(r, code)
                    self.assert_child_stopped()
                finally:
                    self.stop_test_child()

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX process groups and flock')
    def test_exited_agy_does_not_wait_forever_for_inherited_stdout(self):
        for mode in ('held_stdout_no_result', 'held_stdout_partial_result'):
            with self.subTest(mode=mode):
                self.child_ready.unlink(missing_ok=True)
                self.env['AGY_TEST_STREAM_MODE'] = mode
                try:
                    r = self.run_wrapper(exit_code=7, timeout=9)
                    self.assert_failure(r, 4)
                    self.assert_child_stopped()
                finally:
                    self.stop_test_child()

    def test_result_exit_cleanup_has_a_deadline(self):
        self.env['AGY_TEST_STREAM_MODE'] = 'result_hang'
        r = self.run_wrapper(timeout=12)
        self.assert_failure(r, 4)
        self.assertIn('did not exit', r.stderr)
        self.assertEqual(json.loads((self.details(r) / 'envelope.json').read_text())['status'], 'SUCCESS')

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX signals and flock')
    def test_interrupt_cleans_process_group_and_reports_recovery_details(self):
        import fcntl
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                self.child_ready.unlink(missing_ok=True)
                env = dict(self.env, AGY_TEST_STREAM_MODE='interrupt_wait',
                           AGY_TEST_OUTPUT=json.dumps({'status': 'SUCCESS', 'response': 'ANSWER',
                                                       'conversation_id': 'session-1'}))
                process = subprocess.Popen(['python3', str(self.script)], stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, cwd=self.workdir, env=env)
                try:
                    process.stdin.write('Review this.\n')
                    process.stdin.close()
                    process.stdin = None
                    deadline = time.monotonic() + 5
                    while not self.child_ready.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(self.child_ready.exists(), 'child never became ready')
                    process.send_signal(sig)
                    stdout, stderr = process.communicate(timeout=10)
                    r = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
                    self.assert_failure(r, 128 + sig)
                    self.assertIn(signal.Signals(sig).name, stderr)
                    events = [json.loads(line) for line in (self.details(r) / 'events.jsonl').read_text().splitlines()]
                    self.assertEqual(events[0]['conversation_id'], 'session-1')
                    with self.child_lock.open('a') as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    for pipe in (process.stdin, process.stdout, process.stderr):
                        if pipe is not None:
                            pipe.close()
                    self.stop_test_child()


if __name__ == '__main__':
    unittest.main()
