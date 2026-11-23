"""Static ffmpeg installer and the missing-ffmpeg error message, without network."""
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

from CONFIG.LANGUAGES.messages_EN import Messages

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_ffmpeg', ROOT / 'scripts/install_ffmpeg.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

MISSING = ('ERROR: You have requested merging of multiple formats but ffmpeg is not installed. '
           'Aborting due to --abort-on-error')


def make_archive(path, names=('ffmpeg', 'ffprobe')):
    """A release-shaped archive whose "programs" are tiny shell scripts."""
    with tarfile.open(path, 'w:xz') as tar:
        for name in (*names, 'manpages/ffmpeg.txt'):
            data = b'#!/bin/sh\nexit 0\n'
            info = tarfile.TarInfo(f'ffmpeg-9.9-amd64-static/{name}')
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


class InstallerTests(unittest.TestCase):
    def run_install(self, tools, archive, checksum=None, **kwargs):
        checksum = checksum or hashlib.md5(archive.read_bytes()).hexdigest()
        response = Mock()
        response.__enter__ = Mock(return_value=io.BytesIO(f'{checksum}  x.tar.xz\n'.encode()))
        response.__exit__ = Mock(return_value=False)
        with patch.object(installer, 'download', lambda url, target: Path(target).write_bytes(archive.read_bytes())), \
                patch.object(installer.urllib.request, 'urlopen', return_value=response), \
                patch.object(installer.shutil, 'which', return_value=None):
            return installer.install(tools, url='https://example.invalid/a.tar.xz', **kwargs)

    def test_installs_only_the_two_programs_and_they_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, tools = Path(tmp) / 'a.tar.xz', Path(tmp) / 'tools/bin'
            make_archive(archive)
            self.assertEqual(self.run_install(tools, archive), 0)
            self.assertEqual(sorted(p.name for p in tools.iterdir()), ['ffmpeg', 'ffprobe'])
            self.assertTrue(all(installer.works(tools / name) for name in installer.PROGRAMS))
            # Second run finds them and downloads nothing
            with patch.object(installer.shutil, 'which', return_value=None), \
                    patch.object(installer, 'download') as download:
                self.assertEqual(installer.install(tools), 0)
            download.assert_not_called()

    def test_corrupted_or_incomplete_download_fails_without_installing(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, tools = Path(tmp) / 'a.tar.xz', Path(tmp) / 'tools/bin'
            make_archive(archive)
            self.assertEqual(self.run_install(tools, archive, checksum='0' * 32), 1)
            self.assertFalse(tools.exists())
            make_archive(archive, names=('ffmpeg',))
            self.assertEqual(self.run_install(tools, archive), 1)

    def test_existing_system_programs_skip_the_download(self):
        with patch.object(installer.shutil, 'which', return_value='/usr/bin/x'), \
                patch.object(installer, 'download') as download:
            self.assertEqual(installer.install('/nonexistent/tools'), 0)
        download.assert_not_called()

    def test_architecture_mapping(self):
        with patch.object(installer.sys, 'platform', 'linux'):
            self.assertIn('amd64', installer.archive_url('x86_64'))
            self.assertIn('arm64', installer.archive_url('aarch64'))
            with self.assertRaises(RuntimeError):
                installer.archive_url('riscv64')


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
