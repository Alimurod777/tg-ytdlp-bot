"""Check PO provider options against the installed yt-dlp plugin, offline."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class PotOptionsTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / 'HELPERS/pot_helper.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'add_pot_to_ytdl_opts')
        self.config = SimpleNamespace(YOUTUBE_POT_ENABLED=True,
                                     YOUTUBE_POT_BASE_URL='http://127.0.0.1:14416',
                                     YOUTUBE_POT_DISABLE_INNERTUBE=True)
        ns = dict(Config=self.config, logger=Mock(),
                  check_pot_provider_availability=lambda _: True,
                  is_youtube_url=lambda _: True, add_pot_debug_hook=lambda opts: opts,
                  safe_get_messages=lambda: Mock())
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<pot-helper>', 'exec'), ns)
        self.configure = ns['add_pot_to_ytdl_opts']

    def test_installed_plugin_receives_nondefault_server_url(self):
        import yt_dlp
        from yt_dlp.extractor.youtube.pot._director import initialize_pot_director
        opts = self.configure({'extractor_args': {'youtube': {'player_client': ['mweb']}}},
                              'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(opts['extractor_args']['youtube']['player_client'], ['mweb'])
        self.assertNotIn('disable_innertube', opts['extractor_args']['youtubepot-bgutilhttp'])
        opts.update(quiet=True, verbose=False)
        with yt_dlp.YoutubeDL(opts) as ydl:
            director = initialize_pot_director(ydl.get_info_extractor('Youtube'))
            try:
                provider = next(p for p in director.providers.values()
                                if p.PROVIDER_NAME == 'bgutil:http')
                self.assertEqual(provider._base_url, self.config.YOUTUBE_POT_BASE_URL)
            finally:
                director.close()

    def test_disabled_provider_does_not_change_options(self):
        self.config.YOUTUBE_POT_ENABLED = False
        opts = {'format': 'best'}
        self.assertIs(self.configure(opts, 'https://youtube.com/'), opts)
        self.assertEqual(opts, {'format': 'best'})


if __name__ == '__main__':
    unittest.main()
