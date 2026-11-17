"""Exercise the production media-403 fallback without Telegram or network."""
import ast
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class YouTubeMediaRetryTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / 'HELPERS/pot_helper.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'media_403_retry_options')
        self.provider = Mock(return_value=True)
        self.enabled = Mock(return_value=True)
        env = dict(is_pot_enabled=self.enabled, is_pot_provider_available=self.provider)
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<pot-helper>', 'exec'), env)
        self.options = env['media_403_retry_options']
        self.original = {'format': 'bv*[height<=720]+ba', 'cookiefile': 'private.txt',
                         'proxy': 'http://localhost:1080', 'extractor_args': {
                             'youtube': {'player_client': ['default'], 'po_token': ['keep']}}}

    def test_preserves_quality_credentials_and_proxy_without_mutating_original(self):
        opts = self.options(self.original)
        self.assertEqual(opts['format'], self.original['format'])
        self.assertEqual(opts['cookiefile'], self.original['cookiefile'])
        self.assertEqual(opts['proxy'], self.original['proxy'])
        self.assertEqual(opts['extractor_args']['youtube']['player_client'], ['web_creator'])
        self.assertEqual(opts['extractor_args']['youtube']['po_token'], ['keep'])
        self.assertEqual(self.original['extractor_args']['youtube']['player_client'], ['default'])

    def test_missing_provider_or_disabled_feature_skips_retry(self):
        self.provider.return_value = False
        self.assertIsNone(self.options(self.original))
        self.enabled.return_value = False
        self.provider.reset_mock()
        self.assertIsNone(self.options(self.original))
        self.provider.assert_not_called()

    def test_already_using_creator_skips_retry(self):
        self.original['extractor_args']['youtube']['player_client'] = ['web_creator']
        self.assertIsNone(self.options(self.original))

    def dispatch(self, error, *, youtube=True):
        tree = ast.parse((ROOT / 'DOWN_AND_UP/down_and_up.py').read_text())
        branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                      and 'not did_youtube_media_retry' in ast.unparse(n.test))
        wrapper = ast.parse('def retry():\n global did_youtube_media_retry\n').body[0]
        wrapper.body.append(branch)
        env = dict(did_youtube_media_retry=False, error_message=error,
                   is_youtube_url=lambda _: youtube, url='https://youtube.com/watch?v=test',
                   attempt_opts=self.original, logger=Mock())
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
                     '<video-retry>', 'exec'), env)
        env['try_download'] = Mock(side_effect=lambda *_: env['retry']())
        module = ModuleType('HELPERS.pot_helper')
        module.media_403_retry_options = self.options
        with patch.dict(sys.modules, {'HELPERS.pot_helper': module}):
            env['retry']()
        return env

    def test_second_media_403_cannot_recurse(self):
        env = self.dispatch('unable to download video data: HTTP Error 403: Forbidden')
        env['try_download'].assert_called_once()
        self.assertTrue(env['did_youtube_media_retry'])

    def test_other_errors_sites_and_unavailable_provider_do_not_retry(self):
        for error in ('HTTP Error 403', 'unable to download video data: HTTP Error 500'):
            self.dispatch(error)['try_download'].assert_not_called()
        error = 'unable to download video data: HTTP Error 403'
        self.dispatch(error, youtube=False)['try_download'].assert_not_called()
        self.provider.return_value = False
        self.dispatch(error)['try_download'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
