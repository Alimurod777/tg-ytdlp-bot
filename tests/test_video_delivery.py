"""Offline format selection and real MP4 streaming preparation checks."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from HELPERS.streaming import _binary, _streams, has_faststart, prepare_streaming_video
from HELPERS.video_quality import group_quality_key, selected_quality_format


def video(name, width, height, codec='avc1'):
    return dict(format_id=name, width=width, height=height, vcodec=codec,
                acodec='none', ext='mp4', url=f'https://example.com/{name}')


class QualitySelectionTests(unittest.TestCase):
    def select(self, formats, quality='480p', any_codec=False):
        from yt_dlp import YoutubeDL
        formats = formats + [dict(format_id='audio', vcodec='none', acodec='mp4a',
                                  ext='m4a', url='https://example.com/audio')]
        selector = selected_quality_format(quality, 'bv*[vcodec*=avc1]+ba/best',
                                           any_codec=any_codec)
        with YoutubeDL({'quiet': True, 'format': selector}) as ydl:
            result = ydl.process_ie_result(dict(id='test', title='Test', formats=formats),
                                           download=False)
        return result['requested_formats'][0]['format_id']

    def test_resolution_wins_over_codec(self):
        formats = [video('avc360', 640, 360), video('vp9480', 854, 480, 'vp9'),
                   video('avc720', 1280, 720)]
        self.assertEqual(self.select(formats), 'vp9480')

    def test_portrait_uses_smaller_dimension(self):
        formats = [video('avc360', 360, 640), video('vp9480', 480, 854, 'vp9'),
                   video('avc720', 720, 1280)]
        self.assertEqual(self.select(formats), 'vp9480')

    def test_retry_cannot_exceed_requested_resolution(self):
        formats = [video('avc480', 854, 480), video('avc1080', 1920, 1080)]
        self.assertEqual(self.select(formats, any_codec=True), 'avc480')

    def test_lower_resolution_only_when_requested_tier_is_missing(self):
        self.assertEqual(self.select([video('avc360', 640, 360),
                                      video('avc1080', 1920, 1080)]), 'avc360')

    def test_group_cap_and_private_preferences(self):
        for key in (None, 'best', '1080p', '2160p', '137'):
            self.assertEqual(group_quality_key(key, -123), '720p')
            self.assertEqual(group_quality_key(key, 123), key)
        self.assertEqual(group_quality_key('480p', -123), '480p')
        formats = [video('avc720', 1280, 720), video('avc1080', 1920, 1080)]
        self.assertEqual(self.select(formats, group_quality_key('1080p', -123)), 'avc720')

    def test_explicit_format_and_audio_unchanged_in_private(self):
        for key in (None, 'best', 'mp3', '137'):
            self.assertEqual(selected_quality_format(key, '137+140'), '137+140')

    def test_audio_language_preserved(self):
        selector = selected_quality_format('480p', 'bv*[vcodec*=avc1]+ba[language^=uz]')
        self.assertIn('+ba[language^=uz]', selector)


class StreamingPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = _binary('ffmpeg')
            _binary('ffprobe')
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'sample.mp4'

    def make_video(self, pixel_format='yuv420p', audio_codec='aac'):
        subprocess.run(
            [self.ffmpeg, '-nostdin', '-v', 'error', '-y',
             '-f', 'lavfi', '-i', 'testsrc2=size=854x480:rate=10',
             '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '0.4',
             '-c:v', 'libx264', '-pix_fmt', pixel_format, '-c:a', audio_codec,
             str(self.path)], check=True, capture_output=True, timeout=30,
        )

    def test_remux_moves_metadata_before_media_without_reencoding(self):
        self.make_video()
        self.assertFalse(has_faststart(self.path))
        with patch('HELPERS.streaming.subprocess.run', wraps=subprocess.run) as run:
            prepare_streaming_video(self.path)
        ffmpeg_command = next(call.args[0] for call in run.call_args_list
                              if Path(call.args[0][0]).name == 'ffmpeg')
        self.assertNotIn('libx264', ffmpeg_command)
        self.assertTrue(has_faststart(self.path))
        stream = _streams(self.path)[0]
        self.assertEqual((stream['width'], stream['height']), (854, 480))
        # Prepared files are not remuxed a second time.
        with patch('HELPERS.streaming.os.replace') as replace:
            prepare_streaming_video(self.path)
        replace.assert_not_called()

    def test_incompatible_pixel_format_and_audio_are_converted(self):
        self.make_video(pixel_format='yuv444p', audio_codec='libmp3lame')
        prepare_streaming_video(self.path)
        streams = _streams(self.path)
        self.assertEqual((streams[0]['codec_name'], streams[0]['pix_fmt']), ('h264', 'yuv420p'))
        self.assertEqual(streams[1]['codec_name'], 'aac')
        self.assertTrue(has_faststart(self.path))

    def test_failed_conversion_preserves_original_and_removes_candidate(self):
        self.make_video()
        original = self.path.read_bytes()
        run = subprocess.run
        def fail_ffmpeg(command, **kwargs):
            if Path(command[0]).name == 'ffmpeg':
                Path(command[-1]).write_bytes(b'partial output')
                raise subprocess.CalledProcessError(1, command)
            return run(command, **kwargs)
        with patch('HELPERS.streaming.subprocess.run', side_effect=fail_ffmpeg):
            with self.assertRaises(subprocess.CalledProcessError):
                prepare_streaming_video(self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('.streaming-*')), [])

    def test_explicit_alternative_codec_is_not_transcoded(self):
        self.make_video(pixel_format='yuv444p')
        prepare_streaming_video(self.path, ensure_h264=False)
        self.assertEqual(_streams(self.path)[0]['pix_fmt'], 'yuv444p')
        self.assertTrue(has_faststart(self.path))


if __name__ == '__main__':
    unittest.main()
