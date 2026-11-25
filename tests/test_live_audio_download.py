"""Record live audio from a local, growing HLS stream with real yt-dlp and ffmpeg."""
import ast
import glob
import http.server
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yt_dlp

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'DOWN_AND_UP/live_audio_downloader.py'
HAVE_FFMPEG = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))


class Messages:
    def __getattr__(self, name):
        return {
            'LIVE_STREAM_CHUNK_NUMBER_MSG': 'Chunk {chunk}',
            'LIVE_STREAM_ACCUMULATED_DURATION_MSG': 'Duration {duration}',
            'LIVE_STREAM_CHUNKS_DOWNLOADED_MSG': 'Chunks {chunks}',
            'LIVE_STREAM_TOTAL_DURATION_MSG': 'Total {duration}',
            'LIVE_STREAM_CHUNK_TITLE_MSG': 'Chunk {chunk}',
            'LIVE_STREAM_CHUNK_CAPTION_MSG': '{chunk} {duration} {size}',
        }.get(name, name)


def load_module(namespace):
    """Module-level constants and functions, without its bot imports."""
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.Assign))]
    exec(compile(tree, str(SOURCE), 'exec'), namespace)
    return namespace


class LiveHls:
    """A live HLS stream: one-second segments appear over time, then the stream ends."""

    def __init__(self, folder, seconds):
        self.folder, self.seconds = Path(folder), seconds
        subprocess.run(
            ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc=d={seconds}:s=320x240:r=25',
             '-f', 'lavfi', '-i', f'sine=f=440:d={seconds}', '-c:v', 'libx264', '-g', '25',
             '-c:a', 'aac', '-f', 'hls', '-hls_time', '1', '-hls_list_size', '0',
             '-hls_segment_filename', str(self.folder / 's%03d.ts'), str(self.folder / 'full.m3u8')],
            check=True)
        self.segments = sorted(p.name for p in self.folder.glob('s*.ts'))
        stream = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(stream.folder), **kwargs)

            def do_GET(self):
                if self.path.startswith('/live.m3u8'):
                    body = stream.playlist().encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    super().do_GET()

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}/live.m3u8'
        self.started = None

    def playlist(self):
        available = min(len(self.segments), 3 + int(time.monotonic() - self.started))
        first = max(0, available - 4)
        lines = ['#EXTM3U', '#EXT-X-VERSION:3', '#EXT-X-TARGETDURATION:1',
                 f'#EXT-X-MEDIA-SEQUENCE:{first}']
        for name in self.segments[first:available]:
            lines += ['#EXTINF:1.000000,', name]
        if available == len(self.segments):
            lines.append('#EXT-X-ENDLIST')
        return '\n'.join(lines) + '\n'

    def __enter__(self):
        self.started = time.monotonic()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class Harness(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.user_dir = self.dir / 'user'
        self.user_dir.mkdir()
        self.edits = []
        self.sent = []
        self.cancel = threading.Event()
        modules = {}

        def module(name, **attrs):
            obj = ModuleType(name)
            obj.__dict__.update(attrs)
            modules[name] = obj
        module('HELPERS.safe_messeger',
               safe_edit_message_text=lambda _chat, _msg, text: self.edits.append(text))
        module('HELPERS.limitter', humanbytes=lambda n: f'{n}B',
               should_apply_limits_to_admin=lambda **_: True)
        module('HELPERS.filesystem_hlp', sanitize_filename_strict=lambda value: value.replace(' ', '_'))
        module('COMMANDS.args_cmd', get_user_ytdlp_args=lambda *_: {}, merge_user_ytdlp_args=Mock())
        module('HELPERS.proxy_helper', add_proxy_to_ytdl_opts=lambda opts, *_: opts)
        module('HELPERS.pot_helper', add_pot_to_ytdl_opts=lambda opts, *_: opts)
        patcher = patch.dict('sys.modules', modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.unregister = Mock()
        self.limits = SimpleNamespace(MAX_LIVE_STREAM_DURATION=36000, SPLIT_LIVE_STREAM_BY_HOURS=1)
        self.ns = load_module(dict(
            glob=glob, json=json, math=math, os=os, subprocess=subprocess, time=time, yt_dlp=yt_dlp,
            ReplyParameters=lambda **kw: kw, LimitsConfig=self.limits,
            safe_get_messages=lambda _: Messages(), logger=Mock(),
            register_download_cancel_event=lambda _: self.cancel,
            unregister_download_cancel_event=self.unregister,
            timed_upload=lambda fn, **_: fn(), validated_cookie_options=lambda opts: opts,
        ))
        self.app = SimpleNamespace(send_audio=self.send_audio)

    def send_audio(self, **kwargs):
        path = kwargs['audio']
        probe = json.loads(subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,codec_name:format=duration',
             '-of', 'json', path], capture_output=True, text=True, check=True).stdout)
        self.sent.append(dict(kwargs, streams=[s['codec_type'] for s in probe['streams']],
                              codec=probe['streams'][0]['codec_name'],
                              probed=float(probe['format']['duration']), ext=Path(path).suffix))
        return SimpleNamespace(id=len(self.sent))

    def record(self, url, part_seconds, **kwargs):
        self.ns['live_audio_part_seconds'] = lambda: part_seconds
        return self.ns['download_live_audio_chunked'](
            self.app, SimpleNamespace(id=7, chat=SimpleNamespace(id=123)), url, 123,
            str(self.user_dir), dict(title='Radio live', channel='Station'), 1, 'Progress', **kwargs)


@unittest.skipUnless(HAVE_FFMPEG, 'ffmpeg/ffprobe unavailable')
class RealLiveRecordingTests(Harness):
    def test_live_audio_is_cut_into_parts_and_stops_when_the_stream_ends(self):
        with LiveHls(self.dir, 14) as stream:
            self.assertTrue(self.record(stream.url, 4, tags_text='#live'))
        self.assertGreaterEqual(len(self.sent), 2)
        for part in self.sent:
            self.assertEqual(part['streams'], ['audio'])  # the video track was dropped
            self.assertEqual((part['ext'], part['codec']), ('.mp3', 'mp3'))
            self.assertLessEqual(part['probed'], 4.6)
            self.assertTrue(part['caption'].endswith('#live'))
            self.assertEqual(part['performer'], 'Station')
        full = [p for p in self.sent[:-1]]
        self.assertTrue(all(p['probed'] >= 3.5 for p in full))
        self.assertLess(self.sent[-1]['duration'], 4 * 0.9)  # short last part = stream ended
        self.assertIn('LIVE_STREAM_ENDED_MSG', self.edits[-1])
        self.assertEqual(os.listdir(self.user_dir), [])  # parts removed after upload
        self.unregister.assert_called_once_with(123)

    def test_duration_limit_and_user_audio_format(self):
        self.limits.MAX_LIVE_STREAM_DURATION = 5
        with LiveHls(self.dir, 30) as stream:
            self.assertTrue(self.record(stream.url, 3, audio_format='m4a'))
        self.assertEqual([p['ext'] for p in self.sent], ['.m4a', '.m4a'])
        self.assertAlmostEqual(sum(p['probed'] for p in self.sent), 5, delta=0.8)
        self.assertIn('LIVE_STREAM_DOWNLOAD_COMPLETE_MSG', self.edits[-1])


class RecorderLogicTests(Harness):
    def fake_download(self, durations, statuses=None):
        """Each recording writes an 'audio' file whose probed duration comes from the list."""
        queue = list(durations)
        status_queue = list(statuses or [])
        self.recordings = 0

        def factory(opts):
            ydl = Mock()
            ydl.__enter__ = Mock(return_value=ydl)
            ydl.__exit__ = Mock(return_value=False)

            def extract(_url, download):
                self.assertFalse(download)
                value = queue[0] if queue else 60
                if isinstance(value, Exception):
                    queue.pop(0)
                    raise value
                return {'live_status': status_queue.pop(0) if status_queue else 'is_live'}

            def process(_info, download):
                self.assertTrue(download)
                self.recordings += 1
                Path(opts['outtmpl'].replace('%(ext)s', 'mp4')).write_bytes(b'x')
                self.last_duration = queue.pop(0)
            ydl.extract_info.side_effect = extract
            ydl.process_ie_result.side_effect = process
            self.options.append(opts)
            return ydl
        self.options = []
        self.ns['audio_duration'] = lambda _path: self.last_duration
        self.ns['encode_audio'] = lambda src, dst, *_a, **_k: Path(dst).write_bytes(b'mp3')
        return patch.object(yt_dlp, 'YoutubeDL', side_effect=factory)

    def send_audio(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=len(self.sent))

    def test_options_drop_video_and_bound_each_part(self):
        with self.fake_download([60, 60, 10]):
            self.assertTrue(self.record('https://x/live', 60))
        self.assertEqual(len(self.sent), 3)
        opts = self.options[0]
        self.assertEqual(opts['external_downloader_args']['ffmpeg_o'], ['-vn', '-t', '60', '-f', 'mpegts'])
        self.assertEqual(opts['external_downloader'], {'default': 'ffmpeg'})
        self.assertFalse(opts['live_from_start'])
        self.assertTrue(opts['format'].startswith('bestaudio/'))
        self.assertNotEqual(self.options[0]['outtmpl'], self.options[1]['outtmpl'])

    def test_first_part_failure_raises_and_later_failure_keeps_sent_parts(self):
        with self.fake_download([RuntimeError('HTTP Error 403')]):
            with self.assertRaisesRegex(RuntimeError, '403'):
                self.record('https://x/live', 60)
        self.assertEqual(self.sent, [])
        with self.fake_download([60, RuntimeError('network down')]):
            self.assertTrue(self.record('https://x/live', 60))
        self.assertEqual(len(self.sent), 1)
        self.assertIn('LIVE_STREAM_DOWNLOAD_STOPPED_MSG', self.edits[-1])

    def test_clean_command_stops_before_upload(self):
        def cancel_during_download(*_a, **_k):
            self.cancel.set()
        with self.fake_download([60]):
            self.ns['encode_audio'] = lambda src, dst, *_a, **_k: (Path(dst).write_bytes(b'mp3'), cancel_during_download())
            self.assertFalse(self.record('https://x/live', 60))
        self.assertEqual(self.sent, [])

    def test_ended_stream_is_not_recorded_again(self):
        # The stream ends between parts: the URL now serves the recording.
        with self.fake_download([60, 60, 60], statuses=['is_live', 'is_live', 'was_live']):
            self.assertTrue(self.record('https://x/live', 60))
        self.assertEqual((len(self.sent), self.recordings), (2, 2))
        self.assertIn('LIVE_STREAM_ENDED_MSG', self.edits[-1])
        self.assertEqual(os.listdir(self.user_dir), [])
        # Already over before the first part: a clear error, nothing recorded.
        self.sent.clear()
        with self.fake_download([60], statuses=['not_live']):
            with self.assertRaisesRegex(RuntimeError, 'already ended'):
                self.record('https://x/live', 60)
        self.assertEqual((self.sent, self.recordings), ([], 0))

    def test_live_status_reading(self):
        still_live = self.ns['still_live']
        self.assertTrue(still_live({'live_status': 'is_live'}))
        self.assertTrue(still_live({'is_live': True}))
        self.assertTrue(still_live({}))  # extractors that do not report it
        self.assertFalse(still_live({'duration': 14}))  # generic HLS once the playlist ended
        self.assertFalse(still_live({'live_status': 'was_live'}))
        self.assertFalse(still_live({'live_status': 'not_live', 'is_live': False}))
        self.assertFalse(still_live({'entries': [{'live_status': 'was_live'}]}))

    def test_unknown_audio_format_falls_back_to_mp3(self):
        self.assertEqual(self.ns['audio_encoder']('best')[0], 'mp3')
        self.assertEqual(self.ns['audio_encoder']('FLAC')[0], 'flac')

    def test_part_length_comes_from_the_hourly_split_setting(self):
        self.limits.SPLIT_LIVE_STREAM_BY_HOURS = 0.5
        self.assertEqual(self.ns['live_audio_part_seconds'](), 1800)
        self.limits.SPLIT_LIVE_STREAM_BY_HOURS = 0
        self.assertEqual(self.ns['live_audio_part_seconds'](), 3600)


class AudioPathTests(unittest.TestCase):
    def test_audio_path_records_live_streams_instead_of_refusing(self):
        source = (ROOT / 'DOWN_AND_UP/down_and_audio.py').read_text(encoding='utf-8')
        self.assertNotIn('Audio extraction from live streams is not supported', source)
        self.assertIn('download_live_audio_chunked(', source)
        self.assertIn("info_dict.get('_live_stream_uploaded')", source)


class VideoLiveOptionsTests(unittest.TestCase):
    def test_video_chunk_size_limit_reaches_ffmpeg(self):
        """The old CLI-style keys were ignored by YoutubeDL, so -fs never applied."""
        from yt_dlp.downloader.external import FFmpegFD
        source = (ROOT / 'DOWN_AND_UP/live_stream_downloader.py').read_text(encoding='utf-8')
        self.assertNotIn("chunk_opts['downloader_args']", source)
        self.assertNotIn("'downloader': 'ffmpeg',", source)
        opts = {'external_downloader': {'default': 'ffmpeg'}, 'hls_use_mpegts': True, 'quiet': True,
                'external_downloader_args': {'ffmpeg_o': ['-f', 'mpegts', '-fs', '1950M']}}
        ydl = yt_dlp.YoutubeDL(opts)
        self.assertEqual(FFmpegFD(ydl, ydl.params)._configuration_args(('_o1', '_o', '')),
                         ['-f', 'mpegts', '-fs', '1950M'])


if __name__ == '__main__':
    unittest.main()
