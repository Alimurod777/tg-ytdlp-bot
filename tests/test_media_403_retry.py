"""Exercise the media HTTP 403 retries without a bot session or network."""
import ast
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
MEDIA_403 = 'ERROR: unable to download video data: HTTP Error 403: Forbidden'
PAGE_403 = 'ERROR: [generic] Unable to download webpage: HTTP Error 403: Forbidden'


def load_helpers(available=True):
    """Compile the real helpers with a stubbed yt-dlp."""
    tree = ast.parse((ROOT / 'HELPERS/proxy_helper.py').read_text(encoding='utf-8'))
    names = ('is_media_403_error', 'media_403_impersonate_target')
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ydl = Mock()
    ydl.__enter__ = Mock(return_value=ydl)
    ydl.__exit__ = Mock(return_value=False)
    ydl._impersonate_target_available = Mock(return_value=available)
    env = dict(yt_dlp=SimpleNamespace(YoutubeDL=Mock(return_value=ydl)), logger=Mock())
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<proxy-helper>', 'exec'), env)
    return env


def find_branch(path, marker):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                and marker in ast.unparse(n.test))


def helper_module(helpers):
    module = ModuleType('HELPERS.proxy_helper')
    module.is_media_403_error = helpers['is_media_403_error']
    module.media_403_impersonate_target = helpers['media_403_impersonate_target']
    return module


class HelperTests(unittest.TestCase):
    def test_only_refused_media_counts(self):
        is_media_403 = load_helpers()['is_media_403_error']
        self.assertTrue(is_media_403(MEDIA_403))
        self.assertFalse(is_media_403(PAGE_403))
        self.assertFalse(is_media_403('unable to download video data: HTTP Error 404'))
        self.assertFalse(is_media_403(None))

    def test_target_is_a_real_ytdlp_target(self):
        from yt_dlp.networking.impersonate import ImpersonateTarget
        target = load_helpers()['media_403_impersonate_target']({'format': 'best'})
        self.assertEqual(target, ImpersonateTarget('chrome'))

    def test_no_target_when_already_impersonating_or_unavailable(self):
        self.assertIsNone(load_helpers()['media_403_impersonate_target']({'impersonate': 'x'}))
        self.assertIsNone(load_helpers(available=False)['media_403_impersonate_target']({}))

    def test_broken_ytdlp_api_never_raises(self):
        helpers = load_helpers()
        helpers['yt_dlp'].YoutubeDL.side_effect = RuntimeError('boom')
        self.assertIsNone(helpers['media_403_impersonate_target']({}))


class VideoRetryTests(unittest.TestCase):
    def dispatch(self, error, *, youtube=False, available=True):
        branch = find_branch('DOWN_AND_UP/down_and_up.py', 'not did_media_impersonate_retry')
        wrapper = ast.parse('def retry():\n global did_media_impersonate_retry\n').body[0]
        wrapper.body.append(branch)
        options = {'format': 'bv*+ba', 'cookiefile': 'private.txt', 'proxy': 'http://localhost:1080'}
        env = dict(did_media_impersonate_retry=False, error_message=error,
                   is_youtube_url=lambda _: youtube, url='https://example.com/v/1',
                   attempt_opts=options, logger=Mock())
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
                     '<video-retry>', 'exec'), env)
        env['try_download'] = Mock(side_effect=lambda *_: env['retry']())
        with patch.dict(sys.modules, {'HELPERS.proxy_helper': helper_module(load_helpers(available))}):
            env['retry']()
        return env

    def test_refused_media_retries_once_and_keeps_other_options(self):
        env = self.dispatch(MEDIA_403)
        env['try_download'].assert_called_once()
        retry_opts = env['try_download'].call_args.args[1]
        self.assertTrue(retry_opts['impersonate'])
        self.assertEqual({k: v for k, v in retry_opts.items() if k != 'impersonate'}, env['attempt_opts'])
        self.assertNotIn('impersonate', env['attempt_opts'])
        self.assertTrue(env['did_media_impersonate_retry'])

    def test_youtube_other_errors_and_missing_curl_cffi_do_not_retry(self):
        for kwargs in (dict(error=MEDIA_403, youtube=True), dict(error=PAGE_403),
                       dict(error=MEDIA_403, available=False)):
            with self.subTest(**kwargs):
                self.dispatch(**kwargs)['try_download'].assert_not_called()


class AudioRetryTests(unittest.TestCase):
    def dispatch(self, error, *, youtube=False, available=True, pot=True):
        branch = find_branch('DOWN_AND_UP/down_and_audio.py', 'not did_media_403_retry')
        wrapper = ast.parse('def retry():\n global did_media_403_retry\n').body[0]
        wrapper.body.append(branch)
        helpers = load_helpers(available)
        env = dict(did_media_403_retry=False, error_text=error, current_index=0,
                   is_youtube_url=lambda _: youtube, url='https://example.com/a/1', logger=Mock(),
                   is_media_403_error=helpers['is_media_403_error'],
                   media_403_impersonate_target=helpers['media_403_impersonate_target'])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
                     '<audio-retry>', 'exec'), env)
        env['try_download_audio'] = Mock(side_effect=lambda *_: env['retry']())
        pot_module = ModuleType('HELPERS.pot_helper')
        pot_module.is_pot_enabled = lambda: pot
        pot_module.is_pot_provider_available = lambda: pot
        thread = threading.current_thread()
        thread.media_403_retry_for_audio = None
        with patch.dict(sys.modules, {'HELPERS.pot_helper': pot_module}):
            env['retry']()
        env['kind'] = thread.media_403_retry_for_audio
        thread.media_403_retry_for_audio = None
        return env

    def test_other_sites_retry_once_as_a_browser(self):
        env = self.dispatch(MEDIA_403)
        env['try_download_audio'].assert_called_once_with(env['url'], 0)
        self.assertEqual(env['kind'], 'impersonate')

    def test_youtube_retries_once_with_studio_client(self):
        env = self.dispatch(MEDIA_403, youtube=True)
        env['try_download_audio'].assert_called_once()
        self.assertEqual(env['kind'], 'youtube')

    def test_no_retry_without_a_usable_strategy_or_for_other_errors(self):
        for kwargs in (dict(error=MEDIA_403, youtube=True, pot=False),
                       dict(error=MEDIA_403, available=False), dict(error=PAGE_403)):
            with self.subTest(**kwargs):
                env = self.dispatch(**kwargs)
                env['try_download_audio'].assert_not_called()
                self.assertIsNone(env['kind'])

    def test_retry_flag_is_applied_to_the_next_attempt_only(self):
        source = (ROOT / 'DOWN_AND_UP/down_and_audio.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        read = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                    and ast.unparse(n.targets[0]) == 'media_403_retry_kind'
                    and 'getattr' in ast.unparse(n.value))
        apply = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                     and ast.unparse(n.test) == "media_403_retry_kind == 'youtube'")
        reset = ast.parse('threading.current_thread().media_403_retry_for_audio = None').body[0]
        code = compile(ast.fix_missing_locations(ast.Module(body=[read, reset, apply], type_ignores=[])),
                       '<audio-opts>', 'exec')
        thread = threading.current_thread()
        for kind, check in (('youtube', lambda o: o['extractor_args']['youtube']['player_client'] == ['web_creator']),
                            ('impersonate', lambda o: bool(o.get('impersonate')))):
            with self.subTest(kind=kind):
                thread.media_403_retry_for_audio = kind
                opts = {'extractor_args': {'youtube': {'po_token': ['keep']}}}
                with patch.dict(sys.modules, {'HELPERS.proxy_helper': helper_module(load_helpers())}):
                    exec(code, dict(threading=threading, ytdl_opts=opts, logger=Mock()))
                self.assertTrue(check(opts))
                self.assertEqual(opts['extractor_args']['youtube']['po_token'], ['keep'])
                self.assertIsNone(thread.media_403_retry_for_audio)


class MessageTests(unittest.TestCase):
    def test_video_message_explains_refused_media_on_other_sites(self):
        source = (ROOT / 'DOWN_AND_UP/down_and_up.py').read_text(encoding='utf-8')
        self.assertIn('refused the media file', source)

    def test_audio_message_matches_video_for_youtube(self):
        source = (ROOT / 'DOWN_AND_UP/down_and_audio.py').read_text(encoding='utf-8')
        self.assertIn('PO token provider', source)
        self.assertIn('if youtube_media_forbidden else send_error_to_user', source)


if __name__ == '__main__':
    unittest.main()
