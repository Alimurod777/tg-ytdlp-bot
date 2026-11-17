#!/usr/bin/env python3
"""Run the bgutil PO token provider with Node next to the bot, without Docker.

Commands: install | status | provider | bot
  install   build the provider version matching the installed yt-dlp plugin
  status    print whether the provider is enabled, installed and answering
  provider  run only the provider in the foreground
  bot       run the bot; the provider starts and stops together with it
"""
import ctypes
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = Path(os.environ.get('POT_PROVIDER_DIR') or ROOT / '.pot-provider')
REPO_URL = 'https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git'
LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1')
RESTART_DELAY = 15
READY_TIMEOUT = 20

_SETTINGS_CODE = (
    'import json\n'
    'from CONFIG.config import Config\n'
    'print("POTCFG" + json.dumps([bool(getattr(Config, "YOUTUBE_POT_ENABLED", False)),'
    ' str(getattr(Config, "YOUTUBE_POT_BASE_URL", "") or "")]))\n'
)


def log(text):
    print(f'[pot-provider] {text}', file=sys.stderr, flush=True)


def main_js(provider_dir=None):
    return Path(provider_dir or PROVIDER_DIR) / 'server' / 'build' / 'main.js'


def installed_version(provider_dir=None):
    try:
        return (Path(provider_dir or PROVIDER_DIR) / '.version').read_text().strip()
    except OSError:
        return ''


def plugin_version():
    """The server must match the yt-dlp plugin installed next to the bot."""
    from importlib.metadata import version
    return version('bgutil-ytdlp-pot-provider')


def parse_settings(enabled, base_url):
    """Return (should_run_locally, host, port) for the configured provider URL."""
    parsed = urlparse(str(base_url or ''))
    host = (parsed.hostname or '').lower()
    try:
        port = parsed.port or 4416
    except ValueError:
        port = 4416
    return bool(enabled) and host in LOCAL_HOSTS, host or '127.0.0.1', port


def read_settings(root=ROOT):
    """Effective settings (environment, .env, config), read the way the bot reads them.

    A separate process keeps a broken configuration from stopping the bot here:
    the bot then reports that error itself.
    """
    try:
        result = subprocess.run([sys.executable, '-c', _SETTINGS_CODE], cwd=root,
                                capture_output=True, text=True, timeout=60)
        line = next(l for l in reversed(result.stdout.splitlines()) if l.startswith('POTCFG'))
        return parse_settings(*json.loads(line[len('POTCFG'):]))
    except Exception:
        return False, '127.0.0.1', 4416


def ping(port):
    """Provider version when it answers on this machine, else None."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{port}/ping', timeout=3) as response:
            return str(json.loads(response.read().decode()).get('version') or 'unknown')
    except Exception:
        return None


def provider_command(host, port, provider_dir=None):
    # "localhost" may resolve to either family; a missing one is skipped by the server
    hosts = '127.0.0.1,::1' if host == 'localhost' else host
    return ['node', str(main_js(provider_dir)), '--port', str(port), '--host', hosts]


def install(provider_dir=None):
    provider_dir = Path(provider_dir or PROVIDER_DIR)
    for binary in ('git', 'node', 'npm', 'npx'):
        if not shutil.which(binary):
            log(f'missing executable on PATH: {binary}')
            return 1
    node_major = int(subprocess.run(['node', '-p', 'process.versions.node.split(".")[0]'],
                                    capture_output=True, text=True, check=True).stdout.strip())
    if node_major < 22:
        log(f'Node.js 22+ is required (found {node_major})')
        return 1
    version = plugin_version()
    if main_js(provider_dir).is_file() and installed_version(provider_dir) == version:
        log(f'version {version} is already installed in {provider_dir}')
        return 0
    log(f'installing version {version} into {provider_dir}')
    tmp_dir = provider_dir.with_name(provider_dir.name + '.tmp')
    shutil.rmtree(tmp_dir, ignore_errors=True)
    subprocess.run(['git', 'clone', '--quiet', '--depth', '1', '--branch', version,
                    REPO_URL, str(tmp_dir)], check=True)
    subprocess.run(['npm', 'ci', '--no-audit', '--no-fund'], cwd=tmp_dir / 'server', check=True)
    subprocess.run(['npx', 'tsc'], cwd=tmp_dir / 'server', check=True)
    (tmp_dir / '.version').write_text(version + '\n')
    shutil.rmtree(provider_dir, ignore_errors=True)
    tmp_dir.rename(provider_dir)
    log('installed; set YOUTUBE_POT_ENABLED=true in .env and restart the bot')
    return 0


def status(root=ROOT):
    enabled, _, port = read_settings(root)
    version = ping(port) if enabled else None
    if not enabled:
        print('PO token provider: not managed here (disabled, or the URL is not local)')
    elif version:
        print(f'PO token provider: running on port {port} (version {version})')
    elif main_js().is_file():
        print('PO token provider: installed, starts together with the bot')
    else:
        print('PO token provider: enabled but not installed; '
              'run: bash scripts/start_alwaysdata.sh pot-install')
    return 0


def _die_with_parent():
    """Linux: stop the child if this supervisor is killed, so no stray bot or provider survives."""
    try:
        ctypes.CDLL('libc.so.6').prctl(1, signal.SIGTERM)
    except Exception:
        pass


class ProviderSupervisor:
    """Keeps one provider process alive until stop() is called."""

    def __init__(self, command):
        self.command = command
        self.stopping = threading.Event()
        self.process = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        while not self.stopping.is_set():
            try:
                self.process = subprocess.Popen(self.command, stdin=subprocess.DEVNULL,
                                                preexec_fn=_die_with_parent)
                self.process.wait()
            except OSError as error:
                log(f'cannot start the provider: {error}')
            if self.stopping.is_set():
                break
            log(f'provider stopped; restarting in {RESTART_DELAY}s')
            self.stopping.wait(RESTART_DELAY)

    def stop(self):
        self.stopping.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        self.thread.join(timeout=15)


def wait_ready(port, timeout=READY_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ping(port):
            return True
        time.sleep(1)
    return False


def start_provider(root=ROOT):
    """Start the provider when it is enabled, local, installed and not already up."""
    enabled, host, port = read_settings(root)
    if not enabled:
        return None
    if ping(port):
        log(f'already running on port {port}')
        return None
    if not main_js().is_file() or not shutil.which('node'):
        log('YOUTUBE_POT_ENABLED is on but the provider is not installed')
        log('run once: bash scripts/start_alwaysdata.sh pot-install')
        return None
    try:
        if installed_version() != plugin_version():
            log('installed provider differs from the yt-dlp plugin; run pot-install to update')
    except Exception:
        pass
    supervisor = ProviderSupervisor(provider_command(host, port))
    supervisor.start()
    if wait_ready(port):
        log(f'ready on port {port}')
    else:
        log('not answering yet; the bot starts anyway and uses it once it is up')
    return supervisor


def run_bot(bot_command=None, root=ROOT):
    bot_command = bot_command or [sys.executable, '-u', 'magic.py']
    os.chdir(root)
    supervisor = start_provider(root)
    if supervisor is None:
        os.execv(bot_command[0], bot_command)
    # One service, two processes: the provider lives and dies with the bot.
    bot = subprocess.Popen(bot_command, preexec_fn=_die_with_parent)

    def forward(signum, _frame):
        supervisor.stopping.set()
        if bot.poll() is None:
            bot.send_signal(signum)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    try:
        code = bot.wait()
    finally:
        supervisor.stop()
    return code if code >= 0 else 128 - code


def run_provider(root=ROOT):
    _, host, port = read_settings(root)
    if not main_js().is_file():
        log('run first: bash scripts/start_alwaysdata.sh pot-install')
        return 1
    command = provider_command(host, port)
    os.execvp(command[0], command)


def main(argv):
    actions = {'install': install, 'status': status, 'provider': run_provider, 'bot': run_bot}
    if len(argv) != 2 or argv[1] not in actions:
        print(__doc__, file=sys.stderr)
        return 2
    return actions[argv[1]]()


if __name__ == '__main__':
    sys.exit(main(sys.argv))
