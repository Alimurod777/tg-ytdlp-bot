"""Exercise the Docker-free PO token provider runner with stand-in processes."""
import http.server
import importlib.util
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pot_provider', ROOT / 'scripts/pot_provider.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

# Answers /ping like the real server; run as the "provider" process.
FAKE_PROVIDER = textwrap.dedent('''
    import http.server, sys
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"version": "fake"}')
        def log_message(self, *a): pass
    http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
''')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class SettingsTests(unittest.TestCase):
    def test_only_an_enabled_local_url_is_managed(self):
        self.assertEqual(runner.parse_settings(True, 'http://localhost:4416'), (True, 'localhost', 4416))
        self.assertEqual(runner.parse_settings(True, 'http://127.0.0.1:5000'), (True, '127.0.0.1', 5000))
        self.assertFalse(runner.parse_settings(False, 'http://127.0.0.1:4416')[0])
        self.assertFalse(runner.parse_settings(True, 'http://bgutil-provider:4416')[0])
        self.assertFalse(runner.parse_settings(True, '')[0])

    def test_port_defaults_and_bad_port_does_not_raise(self):
        self.assertEqual(runner.parse_settings(True, 'http://127.0.0.1')[2], 4416)
        self.assertEqual(runner.parse_settings(True, 'http://127.0.0.1:99999999')[2], 4416)

    def test_localhost_binds_both_families(self):
        command = runner.provider_command('localhost', 4416, '/x')
        self.assertEqual(command[:2], ['node', '/x/server/build/main.js'])
        self.assertEqual(command[2:], ['--port', '4416', '--host', '127.0.0.1,::1'])
        self.assertEqual(runner.provider_command('127.0.0.1', 1, '/x')[-1], '127.0.0.1')

    def test_ping_reports_none_when_nothing_listens(self):
        self.assertIsNone(runner.ping(free_port()))


class InstallTests(unittest.TestCase):
    def test_matching_installed_version_is_not_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp) / 'server/build'
            build.mkdir(parents=True)
            (build / 'main.js').write_text('')
            (Path(tmp) / '.version').write_text('9.9.9\n')
            completed = subprocess.CompletedProcess([], 0, stdout='24\n')
            with patch.object(runner, 'plugin_version', return_value='9.9.9'), \
                    patch.object(runner.shutil, 'which', return_value='/bin/x'), \
                    patch.object(runner.subprocess, 'run', return_value=completed) as run:
                self.assertEqual(runner.install(tmp), 0)
            self.assertEqual(run.call_count, 1)  # only the Node version probe

    def test_old_node_or_missing_tools_refuse_to_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(runner.shutil, 'which', return_value=None):
                self.assertEqual(runner.install(tmp), 1)
            completed = subprocess.CompletedProcess([], 0, stdout='18\n')
            with patch.object(runner.shutil, 'which', return_value='/bin/x'), \
                    patch.object(runner.subprocess, 'run', return_value=completed):
                self.assertEqual(runner.install(tmp), 1)


class SupervisorTests(unittest.TestCase):
    def test_crashed_provider_is_restarted_and_stop_ends_it(self):
        port = free_port()
        supervisor = runner.ProviderSupervisor([sys.executable, '-c', FAKE_PROVIDER, str(port)])
        with patch.object(runner, 'RESTART_DELAY', 0.2):
            supervisor.start()
            self.assertTrue(runner.wait_ready(port, 15))
            first = supervisor.process
            first.kill()
            first.wait()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and (supervisor.process is first or not runner.ping(port)):
                time.sleep(0.2)
            self.assertIsNot(supervisor.process, first)
            self.assertEqual(runner.ping(port), 'fake')
            supervisor.stop()
        self.assertIsNotNone(supervisor.process.poll())
        self.assertIsNone(runner.ping(port))
        self.assertFalse(supervisor.thread.is_alive())


class BotLifecycleTests(unittest.TestCase):
    """Run the real `bot` command in a child process with stand-in bot and provider."""

    DRIVER = textwrap.dedent('''
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location("pot_provider", sys.argv[1])
        runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        port, mode, root = int(sys.argv[2]), sys.argv[3], sys.argv[4]
        runner.read_settings = lambda root=None: (mode != "disabled", "127.0.0.1", port)
        runner.main_js = lambda provider_dir=None: __import__("pathlib").Path(sys.argv[1])
        runner.plugin_version = runner.installed_version = lambda *a: "same"
        runner.shutil.which = lambda name: "/bin/x"
        runner.provider_command = lambda host, port: [sys.executable, "-c", sys.argv[5], str(port)]
        bot = {"sleep": "import time; print('bot up', flush=True); time.sleep(600)",
               "exit7": "import sys; sys.exit(7)",
               "disabled": "import sys; print('plain bot', flush=True); sys.exit(3)"}[mode]
        sys.exit(runner.run_bot([sys.executable, "-c", bot], root))
    ''')

    def launch(self, mode, port):
        return subprocess.Popen(
            [sys.executable, '-c', self.DRIVER, str(ROOT / 'scripts/pot_provider.py'),
             str(port), mode, tempfile.gettempdir(), FAKE_PROVIDER],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_stopping_the_service_stops_bot_and_provider(self):
        port = free_port()
        service = self.launch('sleep', port)
        try:
            self.assertTrue(runner.wait_ready(port, 20))
            self.assertEqual(service.stdout.readline().strip(), 'bot up')
            service.send_signal(signal.SIGTERM)
            self.assertEqual(service.wait(timeout=30), 128 + signal.SIGTERM)
        finally:
            if service.poll() is None:
                service.kill()
            service.communicate()
        self.assertIsNone(runner.ping(port))

    def test_bot_exit_code_is_kept_and_provider_is_stopped(self):
        port = free_port()
        service = self.launch('exit7', port)
        _, errors = service.communicate(timeout=60)
        self.assertEqual(service.returncode, 7)
        self.assertIn('ready on port', errors)
        self.assertIsNone(runner.ping(port))

    def test_disabled_provider_runs_only_the_bot(self):
        port = free_port()
        service = self.launch('disabled', port)
        output, errors = service.communicate(timeout=60)
        self.assertEqual((service.returncode, output.strip()), (3, 'plain bot'))
        self.assertNotIn('pot-provider', errors)


if __name__ == '__main__':
    unittest.main()
