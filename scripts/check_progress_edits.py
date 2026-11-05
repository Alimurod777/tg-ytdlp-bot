"""Offline regression checks; no Telegram connection or credentials required."""
import ast
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class FloodWait(Exception):
    def __init__(self, value):
        self.value = value


class ProgressEditTests(unittest.TestCase):
    def setUp(self):
        # Load the actual helper without importing the application's config/client.
        tree = ast.parse((ROOT / 'HELPERS/safe_messeger.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == 'safe_edit_progress')
        self.app = Mock()
        self.clock = Mock()
        self.clock.monotonic.return_value = 1000
        self.env = dict(threading=threading, time=self.clock, FloodWait=FloodWait,
                        logger=Mock(), get_app_safe=lambda: self.app,
                        run_pyrogram_client_coroutine=lambda app, result: result,
                        _progress_edit_lock=threading.Lock(),
                        _progress_edit_busy=set(), _progress_edit_next={})
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<helper>', 'exec'), self.env)
        self.edit = self.env['safe_edit_progress']

    def test_flood_wait_does_not_sleep_or_mark_message_deleted(self):
        self.app.edit_message_text.side_effect = FloodWait(21)
        self.assertIs(self.edit(-1, 1, 'first'), True)
        self.clock.sleep.assert_not_called()
        self.clock.monotonic.return_value = 1015
        self.assertIs(self.edit(-1, 1, 'second'), True)
        self.assertEqual(self.app.edit_message_text.call_count, 1)
        self.clock.monotonic.return_value = 1023
        self.app.edit_message_text.side_effect = None
        self.edit(-1, 1, 'latest')
        self.assertEqual(self.app.edit_message_text.call_count, 2)

    def test_parallel_callbacks_skip_busy_chat_but_allow_other_chat(self):
        self.env['_progress_edit_busy'].add(-1)
        self.assertIs(self.edit(-1, 1, 'busy'), True)
        self.app.edit_message_text.assert_not_called()
        self.edit(-2, 2, 'other')
        self.app.edit_message_text.assert_called_once()

    def test_transient_error_recovers_and_deleted_message_stops(self):
        self.app.edit_message_text.side_effect = TimeoutError()
        self.assertIs(self.edit(-1, 1, 'retry later'), True)
        self.clock.monotonic.return_value = 1011
        self.app.edit_message_text.side_effect = Exception('MESSAGE_ID_INVALID')
        self.assertIsNone(self.edit(-1, 1, 'deleted'))
        self.assertNotIn(-1, self.env['_progress_edit_busy'])

    def test_throttle_is_shared_between_messages_in_chat(self):
        self.edit(-1, 1, 'one')
        self.edit(-1, 2, 'two')
        self.assertEqual(self.app.edit_message_text.call_count, 1)
        self.clock.monotonic.return_value = 1011
        self.edit(-1, 2, 'latest')
        self.assertEqual(self.app.edit_message_text.call_count, 2)


class ArchivedHlsTests(unittest.TestCase):
    def test_only_complete_unproxied_untrimmed_archives_use_parallel_fragments(self):
        tree = ast.parse((ROOT / 'DOWN_AND_UP/down_and_up.py').read_text())
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                    and "info_dict.get('live_status') == 'was_live'" in ast.unparse(n.test))
        code = compile(ast.Module(body=[node], type_ignores=[]), '<replay>', 'exec')
        cases = [
            ('was_live', False, None, None, True, True),
            ('post_live', False, None, None, True, False),
            ('is_live', True, None, None, True, False),
            ('was_live', False, 'proxy', None, True, False),
            ('was_live', False, None, 'trim', True, False),
            ('was_live', False, None, None, False, False),
        ]
        for status, live, proxy, trim, hls, expected in cases:
            with self.subTest(status=status, proxy=proxy, trim=trim, hls=hls):
                opts = {'downloader': 'ffmpeg', 'concurrent_fragment_downloads': 1}
                if proxy:
                    opts['proxy'] = proxy
                env = dict(is_hls=hls, info_dict={'live_status': status, 'is_live': live},
                           download_sections=trim, ytdl_opts=opts, logger=Mock())
                exec(code, env)
                self.assertEqual(opts['concurrent_fragment_downloads'], 4 if expected else 1)
                self.assertEqual(opts['downloader'], 'native' if expected else 'ffmpeg')


if __name__ == '__main__':
    unittest.main()
