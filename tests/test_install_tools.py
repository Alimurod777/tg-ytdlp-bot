"""Root-less tool installer and the missing-ffmpeg error message, without network."""
import ast
import hashlib
import html
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from CONFIG.LANGUAGES.messages_EN import Messages

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_tools', ROOT / 'scripts/install_tools.py')
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)

MISSING = ('ERROR: You have requested merging of multiple formats but ffmpeg is not installed. '
           'Aborting due to --abort-on-error')
SCRIPT = b'#!/bin/sh\nexit 0\n'
FAKE_NODE = b'#!/bin/sh\necho 22\n'


def add_file(tar, name, data, mode=0o755):
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), mode
    tar.addfile(info, io.BytesIO(data))


class Network:
    """Serves prepared bytes instead of the internet."""

    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def read_url(self, url):
        self.requested.append(url)
        return self.pages[url]

    def download(self, url, target):
        Path(target).write_bytes(self.read_url(url))

    def patched(self, system_has=()):
        which = lambda name: f'/usr/bin/{name}' if name in system_has else None
        return (patch.object(tools, 'read_url', self.read_url),
                patch.object(tools, 'download', self.download),
                patch.object(tools.shutil, 'which', which),
                patch.object(tools, 'machine_arch', lambda machine=None: 'amd64'))


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'tools'

    def run_with(self, network, action, system_has=()):
        patches = network.patched(system_has)
        for item in patches:
            item.start()
        try:
            return action()
        finally:
            for item in patches:
                item.stop()


class FfmpegTests(Case):
    URL = 'https://example.invalid/ffmpeg.tar.xz'

    def archive(self, names=('ffmpeg', 'ffprobe')):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:xz') as tar:
            for name in (*names, 'manpages/ffmpeg.txt'):
                add_file(tar, f'ffmpeg-9.9-amd64-static/{name}', SCRIPT)
        return buffer.getvalue()

    def network(self, data, checksum=None):
        checksum = checksum or hashlib.md5(data).hexdigest()
        return Network({self.URL: data, self.URL + '.md5': f'{checksum}  a.tar.xz\n'.encode()})

    def test_installs_only_the_two_programs_and_they_run(self):
        self.run_with(self.network(self.archive()), lambda: tools.install_ffmpeg(self.root, self.URL))
        self.assertEqual(sorted(p.name for p in (self.root / 'bin').iterdir()), ['ffmpeg', 'ffprobe'])
        with patch.object(tools.shutil, 'which', return_value=None):
            self.assertTrue(tools.ffmpeg_present(self.root))

    def test_corrupted_or_incomplete_download_installs_nothing_usable(self):
        with self.assertRaises(RuntimeError):
            self.run_with(self.network(self.archive(), '0' * 32),
                          lambda: tools.install_ffmpeg(self.root, self.URL))
        self.assertFalse((self.root / 'bin').exists())
        with self.assertRaises(RuntimeError):
            self.run_with(self.network(self.archive(('ffmpeg',))),
                          lambda: tools.install_ffmpeg(self.root, self.URL))


class MediainfoTests(Case):
    URL = 'https://example.invalid/mediainfo.zip'

    def bundle(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('LICENSE', 'text')
            archive.writestr('bin/mediainfo', SCRIPT)
        return buffer.getvalue()

    def test_checksum_is_enforced_and_program_is_installed(self):
        data = self.bundle()
        network = Network({self.URL: data})
        with self.assertRaises(RuntimeError):
            self.run_with(network, lambda: tools.install_mediainfo(self.root, self.URL, 'f' * 64))
        self.assertFalse((self.root / 'bin' / 'mediainfo').exists())
        good = hashlib.sha256(data).hexdigest()
        self.run_with(network, lambda: tools.install_mediainfo(self.root, self.URL, good))
        self.assertTrue(tools.works(self.root / 'bin' / 'mediainfo', '--Version'))

    def test_pinned_checksums_cover_both_architectures(self):
        self.assertEqual(sorted(tools.MEDIAINFO_SHA256), ['amd64', 'arm64'])
        self.assertTrue(all(len(v) == 64 for v in tools.MEDIAINFO_SHA256.values()))


class NodeTests(Case):
    BASE = 'https://example.invalid/node/'
    NAME = 'node-v22.1.0-linux-x64.tar.xz'

    def archive(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:xz') as tar:
            add_file(tar, 'node-v22.1.0-linux-x64/bin/node', FAKE_NODE)
            add_file(tar, 'node-v22.1.0-linux-x64/lib/node_modules/npm/bin/npm-cli.js', SCRIPT)
            for name in ('npm', 'npx'):
                link = tarfile.TarInfo(f'node-v22.1.0-linux-x64/bin/{name}')
                link.type, link.linkname = tarfile.SYMTYPE, '../lib/node_modules/npm/bin/npm-cli.js'
                tar.addfile(link)
        return buffer.getvalue()

    def network(self, data, checksum=None):
        checksum = checksum or hashlib.sha256(data).hexdigest()
        sums = f'aaaa  node-v22.1.0-darwin-x64.tar.xz\n{checksum}  {self.NAME}\nbbbb  node-v22.1.0-linux-arm64.tar.xz\n'
        return Network({self.BASE + 'SHASUMS256.txt': sums.encode(), self.BASE + self.NAME: data})

    def test_installs_node_with_working_npm_links(self):
        self.run_with(self.network(self.archive()), lambda: tools.install_node(self.root, self.BASE))
        self.assertEqual(tools.node_major(self.root / 'bin' / 'node'), 22)
        for name in ('npm', 'npx'):
            self.assertTrue((self.root / 'bin' / name).resolve().is_file())
        with patch.object(tools.shutil, 'which', return_value=None):
            self.assertTrue(tools.node_present(self.root))

    def test_checksum_mismatch_installs_nothing(self):
        with self.assertRaises(RuntimeError):
            self.run_with(self.network(self.archive(), 'c' * 64),
                          lambda: tools.install_node(self.root, self.BASE))
        self.assertFalse((self.root / 'node').exists())

    def test_too_old_system_node_does_not_count(self):
        with patch.object(tools.shutil, 'which', return_value='/usr/bin/node'), \
                patch.object(tools, 'node_major', return_value=18):
            self.assertFalse(tools.node_present(self.root))

    def test_archive_choice_follows_architecture(self):
        sums = 'a1  node-v22.1.0-linux-x64.tar.xz\nb2  node-v22.1.0-linux-arm64.tar.xz\n'
        self.assertEqual(tools.pick_node_archive(sums, 'arm64'), ('node-v22.1.0-linux-arm64.tar.xz', 'b2'))
        with self.assertRaises(RuntimeError):
            tools.pick_node_archive('a1  node-v22.1.0-win-x64.zip\n', 'amd64')


class FontTests(Case):
    def test_fonts_and_config_are_written_and_non_fonts_refused(self):
        urls = ('https://example.invalid/A.ttf', 'https://example.invalid/B.otf')
        network = Network({urls[0]: b'\x00\x01\x00\x00data', urls[1]: b'OTTOdata'})
        self.run_with(network, lambda: tools.install_fonts(self.root, urls))
        conf = (self.root / 'fonts' / 'fonts.conf').read_text()
        self.assertIn(f'<dir>{self.root / "fonts"}</dir>', conf)
        self.assertIn('/etc/fonts/fonts.conf', conf)
        self.assertTrue((self.root / 'fonts' / 'A.ttf').is_file())
        bad = Network({urls[0]: b'<html>not found</html>'})
        with self.assertRaises(RuntimeError):
            self.run_with(bad, lambda: tools.install_fonts(self.root, urls[:1]))


class CommandTests(Case):
    def test_present_tools_are_skipped_and_failures_reported(self):
        calls = []
        table = {'ffmpeg': (lambda root=None: True, lambda root=None: calls.append('ffmpeg')),
                 'node': (lambda root=None: False, lambda root=None: calls.append('node')),
                 'fonts': (lambda root=None: False, Mock(side_effect=RuntimeError('fonts: offline')))}
        with patch.object(tools, 'TOOLS', table):
            self.assertEqual(tools.main(['ffmpeg', 'node'], self.root), 0)
            self.assertEqual(calls, ['node'])
            self.assertEqual(tools.main(['ffmpeg', '--force'], self.root), 0)
            self.assertEqual(calls, ['node', 'ffmpeg'])
            self.assertEqual(tools.main([], self.root), 1)  # fonts failed, node still installed
            self.assertEqual(calls, ['node', 'ffmpeg', 'node'])
            self.assertEqual(tools.main(['bogus'], self.root), 2)

    def test_start_script_exposes_the_installer(self):
        script = (ROOT / 'scripts/start_alwaysdata.sh').read_text(encoding='utf-8')
        self.assertIn('export PATH="$task_root/.tools/bin:', script)
        self.assertIn('scripts/install_tools.py "$@"', script)
        self.assertIn('scripts/install_tools.py ffmpeg "$@"', script)
        self.assertIn('FONTCONFIG_FILE', script)

    def test_architecture_mapping(self):
        with patch.object(tools.sys, 'platform', 'linux'):
            self.assertEqual(tools.machine_arch('x86_64'), 'amd64')
            self.assertEqual(tools.machine_arch('aarch64'), 'arm64')
            with self.assertRaises(RuntimeError):
                tools.machine_arch('riscv64')


class MissingFfmpegMessageTests(unittest.TestCase):
    def test_video_error_names_the_real_cause_without_cookie_advice(self):
        path = ROOT / 'DOWN_AND_UP/down_and_up.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        block = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                     and ast.unparse(n.test) == 'not error_message_sent'
                     and any(isinstance(c, ast.Assign) and any(
                         isinstance(t, ast.Name) and t.id == 'ffmpeg_missing'
                         for t in c.targets) for c in n.body))
        env = dict(error_message=MISSING, error_message_sent=False, is_youtube_url=lambda _: True,
                   url='https://www.youtube.com/watch?v=abcdefghijk', message=SimpleNamespace(id=1),
                   user_id=123, safe_get_messages=lambda _: Messages,
                   send_error_to_user=Mock(), safe_send_message=Mock(),
                   is_cookie_error=lambda _: True, logger=Mock(), html=html,
                   ReplyParameters=lambda **kw: kw,
                   enums=SimpleNamespace(ParseMode=SimpleNamespace(HTML='html')))
        exec(compile(ast.Module(body=[block], type_ignores=[]), str(path), 'exec'), env)
        output = env['send_error_to_user'].call_args.args[1]
        self.assertIn('FFMPEG_NOT_INSTALLED', output)
        self.assertIn('ffmpeg-install', output)
        self.assertNotIn('UNKNOWN_ERROR', output)
        self.assertNotIn(Messages.ERROR_COOKIE_INSTRUCTIONS_MSG, output)
        env['safe_send_message'].assert_not_called()

    def test_audio_path_has_the_same_classification(self):
        source = (ROOT / 'DOWN_AND_UP/down_and_audio.py').read_text(encoding='utf-8')
        self.assertIn('FFMPEG_NOT_INSTALLED', source)
        self.assertIn('if youtube_media_forbidden or ffmpeg_missing else send_error_to_user', source)


if __name__ == '__main__':
    unittest.main()
