"""Exercise live downloading offline, without initializing the bot or Telegram."""
import ast
from datetime import datetime
import glob
import math
import os
from pathlib import Path
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yt_dlp


ROOT = Path(__file__).resolve().parents[1]


def load_functions(namespace):
    path = ROOT / 'DOWN_AND_UP/live_stream_downloader.py'
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    exec(compile(tree, str(path), 'exec'), namespace)
    return namespace


class Messages:
    def __getattr__(self, name):
        return {
            'LIVE_STREAM_CHUNK_NUMBER_MSG': 'Chunk {chunk}',
            'LIVE_STREAM_CHUNK_SIZE_MSG': 'Size {size}',
            'LIVE_STREAM_ACCUMULATED_DURATION_MSG': 'Duration {duration}',
            'LIVE_STREAM_CHUNKS_DOWNLOADED_MSG': 'Chunks {chunks}',
            'LIVE_STREAM_TOTAL_DURATION_MSG': 'Total {duration}',
            'LIVE_STREAM_CHUNK_TITLE_MSG': 'Chunk {chunk}',
            'LIVE_STREAM_CHUNK_CAPTION_MSG': '{chunk} {duration} {size}',
        }.get(name, name)


class LiveStreamTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.edits = Mock()
        self.modules = {}
        def module(name, **attrs):
            obj = ModuleType(name)
            obj.__dict__.update(attrs)
            self.modules[name] = obj
        module('HELPERS.safe_messeger', read_flood_wait_remaining=lambda _: (None, None),
               _write_flood_wait_file=Mock(), safe_edit_message_text=self.edits)
        module('HELPERS.limitter', should_apply_limits_to_admin=lambda **_: False,
               humanbytes=str)
        module('HELPERS.filesystem_hlp', sanitize_filename_strict=lambda value: value)
        module('COMMANDS.args_cmd', get_user_ytdlp_args=lambda *_: {}, log_ytdlp_options=Mock())
        module('COMMANDS.format_cmd', get_user_mkv_preference=lambda _: False)
        module('HELPERS.proxy_helper', add_proxy_to_ytdl_opts=lambda opts, *_: opts)
        module('HELPERS.pot_helper', add_pot_to_ytdl_opts=lambda opts, *_: opts)
        self.patcher = patch.dict('sys.modules', self.modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.event = SimpleNamespace(is_set=lambda: False)
        self.send = Mock(return_value=SimpleNamespace(id=1))
        self.ns = load_functions(dict(
            os=os, glob=glob, math=math, datetime=datetime, time=time, yt_dlp=yt_dlp,
            safe_get_messages=lambda _: Messages(), logger=Mock(), FloodWait=type('FloodWait', (Exception,), {}),
            safe_send_message=Mock(return_value=None),
            register_download_cancel_event=lambda _: self.event,
            unregister_download_cancel_event=Mock(),
            LimitsConfig=SimpleNamespace(MAX_LIVE_STREAM_DURATION=36000),
            get_user_split_size=lambda _: 1000000, validated_cookie_options=lambda opts: opts,
            get_video_info_ffprobe=Mock(return_value=(1280, 720, 12.5)),
            get_duration_thumb=Mock(return_value=None), send_videos=self.send,
            get_log_channel=lambda _: 123, safe_forward_messages=Mock(return_value=[SimpleNamespace(id=2)]),
            _save_video_cache_with_logging=Mock(), split_video_2=Mock(),
        ))
        self.options = []

    def run_download(self, action):
        def factory(opts):
            self.options.append(opts.copy())
            ydl = Mock()
            ydl.__enter__ = Mock(return_value=ydl)
            ydl.__exit__ = Mock(return_value=False)
            ydl.download.side_effect = lambda _: action(opts)
            return ydl
        with patch.object(yt_dlp, 'YoutubeDL', side_effect=factory):
            return self.ns['download_live_stream_chunked'](
                Mock(), SimpleNamespace(text='https://example.com/live', caption=None),
                'https://example.com/live', 123, self.directory.name,
                dict(title='Test [live]', channel='Channel', upload_date='20261109'),
                1, 'Progress',
            )

    def create_output(self, opts, extension):
        path = Path(opts['outtmpl'].replace('%(ext)s', extension))
        path.write_bytes(b'fake media')
        return path

    def assert_no_success(self):
        self.send.assert_not_called()
        for call in self.edits.call_args_list:
            self.assertNotIn('LIVE_STREAM_DOWNLOAD_COMPLETE_MSG', call.args[2])
        self.ns['unregister_download_cancel_event'].assert_called_once()

    def test_video_selected_instead_of_thumbnail_and_sidecars(self):
        def action(opts):
            for extension in ('webp', 'info.json', 'mp4.part', 'f137.mp4', 'mp4'):
                self.create_output(opts, extension)
            return 0
        self.assertTrue(self.run_download(action))
        self.assertTrue(self.send.call_args.args[1].endswith('_001.mp4'))
        self.assertEqual(self.send.call_count, 1)
        self.ns['_save_video_cache_with_logging'].assert_called_once()

    def test_thumbnail_only_fails_without_success(self):
        with self.assertRaisesRegex(RuntimeError, 'no completed media'):
            self.run_download(lambda opts: (self.create_output(opts, 'webp'), 0)[1])
        self.assert_no_success()

    def test_missing_media_stops_instead_of_looping(self):
        with self.assertRaisesRegex(RuntimeError, 'no completed media'):
            self.run_download(lambda _: 0)
        self.assertEqual(len(self.options), 1)
        self.assert_no_success()

    def test_invalid_duration_is_never_uploaded(self):
        for duration in (0, -1, float('nan'), float('inf')):
            with self.subTest(duration=duration):
                self.ns['get_video_info_ffprobe'].return_value = (1280, 720, duration)
                with self.assertRaisesRegex(RuntimeError, 'no playable video'):
                    self.run_download(lambda opts: (self.create_output(opts, 'mp4'), 0)[1])
        self.send.assert_not_called()

    def test_original_fragment_error_preserved(self):
        def action(_):
            raise yt_dlp.utils.DownloadError('fragment 1 not found, unable to continue')
        with self.assertRaisesRegex(yt_dlp.utils.DownloadError, 'fragment 1 not found'):
            self.run_download(action)
        self.assert_no_success()

    def test_403_before_first_chunk_does_not_report_stream_complete(self):
        def action(_):
            raise yt_dlp.utils.DownloadError('HTTP Error 403')
        with self.assertRaisesRegex(yt_dlp.utils.DownloadError, '403'):
            self.run_download(action)
        self.assert_no_success()

    def test_nonzero_downloader_exit_is_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'exit code 1'):
            self.run_download(lambda opts: (self.create_output(opts, 'mp4'), 1)[1])
        self.assert_no_success()

    def test_live_from_start_retry_uses_validation_and_cache(self):
        def action(opts):
            if opts['live_from_start']:
                raise yt_dlp.utils.DownloadError(
                    '--live-from-start is passed, but there are no formats that can be downloaded from the start')
            self.create_output(opts, 'webp')
            self.create_output(opts, 'mp4')
            return 0
        self.assertTrue(self.run_download(action))
        self.assertEqual([opts['live_from_start'] for opts in self.options], [True, False])
        self.send.assert_called_once()
        self.ns['_save_video_cache_with_logging'].assert_called_once()

    def test_upload_failure_does_not_report_success(self):
        self.send.return_value = None
        with self.assertRaisesRegex(RuntimeError, 'Failed to upload'):
            self.run_download(lambda opts: (self.create_output(opts, 'mp4'), 0)[1])
        self.assertFalse(any('LIVE_STREAM_DOWNLOAD_COMPLETE_MSG' in call.args[2]
                             for call in self.edits.call_args_list))


class LiveCallerTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / 'DOWN_AND_UP/down_and_up.py'
        tree = ast.parse(path.read_text())
        branch = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.If)
                      and "info_dict.get('is_live', False)" in ast.unparse(node.test))
        # Execute the production live dispatch branch with only its dependencies mocked.
        wrapper = ast.parse('def dispatch(): pass').body[0]
        wrapper.body = [branch]
        module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
        self.download = Mock(return_value=True)
        self.info = dict(is_live=True, title='Live')
        self.ns = dict(LimitsConfig=SimpleNamespace(ENABLE_LIVE_STREAM_BLOCKING=False),
                       logger=Mock(), info_dict=self.info)
        for name in ('app', 'message', 'url', 'user_id', 'user_dir_name', 'proc_msg_id',
                     'current_total_process', 'tags_text', 'cookies_already_checked',
                     'use_proxy', 'format_override', 'quality_key'):
            self.ns[name] = None
        exec(compile(module, str(path), 'exec'), self.ns)
        live = ModuleType('DOWN_AND_UP.live_stream_downloader')
        live.download_live_stream_chunked = self.download
        patcher = patch.dict('sys.modules', {'DOWN_AND_UP.live_stream_downloader': live})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.terminals = next(ast.literal_eval(node.value) for node in tree.body
                              if isinstance(node, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == '_TERMINAL_ATTEMPT_RESULTS'
                                      for t in node.targets))

    def test_success_marks_media_as_already_uploaded(self):
        self.assertEqual(self.ns['dispatch'](), {**self.info, '_live_stream_uploaded': True})
        self.download.assert_called_once()

    def test_failure_preserves_error_and_stops_format_retries(self):
        self.download.side_effect = RuntimeError('fragment 1 not found')
        self.assertEqual(self.ns['dispatch'](), 'LIVE_STREAM_FAILED')
        self.assertIn('LIVE_STREAM_FAILED', self.terminals)
        self.ns['logger'].error.assert_called_once_with('[live_stream] fragment 1 not found')


if __name__ == '__main__':
    unittest.main()
