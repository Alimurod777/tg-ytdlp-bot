from HELPERS.cookie_file import validated_cookie_options
# Live stream audio: record from the live edge in time-limited parts and send
# each part as an audio file as soon as it is ready.

import glob
import json
import math
import itertools
import os
import subprocess
import time

import yt_dlp
from pyrogram.types import ReplyParameters

from CONFIG.limits import LimitsConfig
from CONFIG.messages import safe_get_messages
from HELPERS.download_status import register_download_cancel_event, unregister_download_cancel_event
from HELPERS.logger import logger
from HELPERS.upload_guard import timed_upload

# Audio-only formats first; YouTube offers only muxed HLS while live, so fall
# back to a small video variant (its video track is dropped while recording).
LIVE_AUDIO_FORMAT = 'bestaudio/best[height<=480]/best'
# From the start YouTube serves separate DASH audio, so no video is fetched at all.
FROM_START_FORMAT = 'bestaudio'
RAW_EXTENSIONS = ('.ts', '.mpegts', '.mp4', '.m4a', '.mka', '.mkv', '.webm', '.aac', '.opus', '.ogg', '.mp3')
AUDIO_ENCODERS = {
    'mp3': ('mp3', ['-c:a', 'libmp3lame', '-b:a', '192k']),
    'm4a': ('m4a', ['-c:a', 'aac', '-b:a', '192k']),
    'aac': ('m4a', ['-c:a', 'aac', '-b:a', '192k']),
    'opus': ('opus', ['-c:a', 'libopus', '-b:a', '128k']),
    'ogg': ('ogg', ['-c:a', 'libvorbis', '-q:a', '5']),
    'vorbis': ('ogg', ['-c:a', 'libvorbis', '-q:a', '5']),
    'flac': ('flac', ['-c:a', 'flac']),
    'wav': ('wav', ['-c:a', 'pcm_s16le']),
}
# A part this much shorter than requested means the stream ended (or stopped sending).
ENDED_RATIO = 0.9


def live_audio_part_seconds():
    hours = getattr(LimitsConfig, 'SPLIT_LIVE_STREAM_BY_HOURS', 1) or 1
    return max(60, int(float(hours) * 3600))


def audio_encoder(audio_format):
    return AUDIO_ENCODERS.get(str(audio_format or 'mp3').lower(), AUDIO_ENCODERS['mp3'])


def find_recorded_part(stem):
    """The finished recording for this part; ignores .part files and sidecars."""
    for path in sorted(glob.glob(glob.escape(stem) + '.*')):
        if (os.path.splitext(path)[0] == stem
                and os.path.splitext(path)[1].lower() in RAW_EXTENSIONS
                and os.path.getsize(path) > 0):
            return path
    raise RuntimeError("Live stream recording produced no audio file")


def audio_duration(path):
    """Seconds of audio in the file; refuses files without a playable audio track."""
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type:format=duration',
         '-of', 'json', path],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    try:
        data = json.loads(result.stdout or '{}')
        has_audio = any(s.get('codec_type') == 'audio' for s in data.get('streams') or [])
        duration = float((data.get('format') or {}).get('duration') or 0)
    except (ValueError, TypeError):
        has_audio, duration = False, 0
    if result.returncode != 0 or not has_audio or not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("Live stream recording contains no playable audio")
    return duration


def encode_audio(source, target, codec_args, timeout):
    result = subprocess.run(
        ['ffmpeg', '-y', '-v', 'error', '-i', source, '-map', '0:a:0', '-vn', *codec_args, target],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if result.returncode != 0 or not os.path.isfile(target) or os.path.getsize(target) == 0:
        raise RuntimeError(f"Audio conversion failed: {(result.stderr or '').strip()[-300:]}")


def still_live(info):
    """False once the URL serves a finished recording instead of the live stream."""
    if isinstance(info, dict) and info.get('entries'):
        info = next((e for e in info['entries'] if isinstance(e, dict)), info)
    if not isinstance(info, dict):
        return True
    if info.get('is_live') is True or info.get('live_status') in ('is_live', 'post_live'):
        return True
    if info.get('live_status') is not None or info.get('is_live') is not None:
        return False
    # Extractors that do not report the status (generic HLS) give finished
    # recordings a duration; a running stream has none.
    return not info.get('duration')


def recording_options(base_opts, stem, seconds):
    opts = dict(base_opts)
    opts['outtmpl'] = stem + '.%(ext)s'
    # Output options of yt-dlp's ffmpeg downloader: drop video, stop after `seconds`.
    opts['external_downloader_args'] = {'ffmpeg_o': ['-vn', '-t', str(int(seconds)), '-f', 'mpegts']}
    return opts


class StreamEnded(Exception):
    """The URL no longer serves a live stream."""


def fragment_slice(fragments, first, count, taken):
    """Fragments first..first+count-1 of a live-from-start fragment generator.

    YouTube numbers live segments from the start of the stream, so a part is a
    fixed range of segment numbers. The generator waits at the live edge for
    segments that do not exist yet and stops when the stream ends; `taken`
    counts what was actually handed to the downloader.
    """
    def generate(ctx):
        source = fragments(ctx) if callable(fragments) else fragments
        for fragment in itertools.islice(source, first, first + count):
            taken[0] += 1
            yield fragment
    return generate


class LiveEdgeRecorder:
    """Records from the current moment; each part is cut by ffmpeg after `seconds`."""

    from_start = False

    def __init__(self, base_opts, url):
        self.base_opts, self.url = base_opts, url

    def record(self, stem, seconds, part):
        with yt_dlp.YoutubeDL(validated_cookie_options(recording_options(self.base_opts, stem, seconds))) as ydl:
            live_info = ydl.extract_info(self.url, download=False)
            if not still_live(live_info):
                raise StreamEnded()
            ydl.process_ie_result(live_info, download=True)
        return find_recorded_part(stem), None  # end is judged by the part's length

    def close(self):
        pass


class FromStartRecorder:
    """Records from the first second of the stream using YouTube's numbered segments.

    yt-dlp downloads the segments natively and repairs the joined file (it
    normally does this for --live-from-start), so parts play like its output.
    """

    from_start = True

    def __init__(self, ydl, info):
        self.ydl, self.info = ydl, info
        self.fragment_seconds = float(info['target_duration'])
        self.next_fragment = 0

    @classmethod
    def open(cls, base_opts, url, file_prefix):
        """The recorder, or None when the stream cannot be read from its start."""
        opts = dict(base_opts)
        opts.pop('external_downloader', None)
        opts.update(live_from_start=True, format=FROM_START_FORMAT,
                    outtmpl=file_prefix.replace('%', '%%') + '_%(live_part)03d.%(ext)s')
        ydl = yt_dlp.YoutubeDL(validated_cookie_options(opts))
        try:
            info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as error:
            ydl.close()
            if 'no formats that can be downloaded from the start' in str(error):
                return None  # the stream keeps no history (DVR disabled)
            raise
        usable = (still_live(info) and info.get('protocol') == 'http_dash_segments_generator'
                  and callable(info.get('fragments')) and (info.get('target_duration') or 0) > 0
                  and not info.get('requested_formats'))
        if not usable:
            ydl.close()
            return None
        return cls(ydl, info)

    def record(self, stem, seconds, part):
        count = max(1, round(seconds / self.fragment_seconds))
        taken = [0]
        part_info = dict(self.info, live_part=part,
                         fragments=fragment_slice(self.info['fragments'], self.next_fragment, count, taken))
        for key in ('__postprocessors', '__pending_error', 'requested_downloads', 'filepath'):
            part_info.pop(key, None)
        self.next_fragment += count
        try:
            self.ydl.process_info(part_info)
        except Exception:
            if taken[0]:
                raise
        if taken[0] == 0:
            # The stream ended exactly at the previous part's boundary
            for leftover in glob.glob(glob.escape(stem) + '.*'):
                os.remove(leftover)
            raise StreamEnded()
        return find_recorded_part(stem), taken[0] < count

    def close(self):
        self.ydl.close()


def open_recorder(base_opts, url, file_prefix):
    try:
        recorder = FromStartRecorder.open(base_opts, url, file_prefix)
    except Exception as error:
        logger.warning(f"Live audio: recording from the start is unavailable ({error}); using the live edge")
        recorder = None
    return recorder or LiveEdgeRecorder(base_opts, url)


def download_live_audio_chunked(app, message, url, user_id, user_dir, info_dict,
                                proc_msg_id, current_total_process, tags_text="",
                                use_proxy=False, format_override=None, audio_format='mp3'):
    """Record a live stream's audio in parts of SPLIT_LIVE_STREAM_BY_HOURS and send each.

    Returns True when at least one part was sent. Raises when the first part fails;
    after a part was sent, later failures stop the recording instead.
    """
    from HELPERS.safe_messeger import safe_edit_message_text
    from HELPERS.limitter import humanbytes, should_apply_limits_to_admin
    from HELPERS.filesystem_hlp import sanitize_filename_strict

    cancel_event = register_download_cancel_event(user_id)
    messages = safe_get_messages(user_id)
    sent_parts = 0
    recorder = None
    recorded = 0.0

    def report(*lines):
        try:
            safe_edit_message_text(user_id, proc_msg_id, "\n".join([current_total_process, *lines]))
        except Exception as error:
            logger.error(f"Error updating live audio progress: {error}")

    def summary(head, *extra):
        return (head, messages.LIVE_STREAM_CHUNKS_DOWNLOADED_MSG.format(chunks=sent_parts),
                messages.LIVE_STREAM_TOTAL_DURATION_MSG.format(duration=int(recorded)), *extra)

    try:
        max_duration = (LimitsConfig.MAX_LIVE_STREAM_DURATION
                        if should_apply_limits_to_admin(user_id=user_id, message=message) else None)
        part_seconds = live_audio_part_seconds()
        title = info_dict.get('title') or f"{info_dict.get('channel') or 'live'}_live_stream"
        performer = info_dict.get('channel') or info_dict.get('uploader') or None
        safe_title = sanitize_filename_strict(title)
        extension, codec_args = audio_encoder(audio_format)

        base_opts = {
            'format': format_override or LIVE_AUDIO_FORMAT,
            'retries': 20,
            'fragment_retries': 10,
            'socket_timeout': 60,
            'hls_use_mpegts': True,
            'external_downloader': {'default': 'ffmpeg'},
            'live_from_start': False,
            'noplaylist': True,
            'quiet': False,
            'no_warnings': False,
        }
        from COMMANDS.args_cmd import get_user_ytdlp_args, merge_user_ytdlp_args
        user_args = get_user_ytdlp_args(user_id, url)
        if user_args:
            merge_user_ytdlp_args(base_opts, user_args, skip={
                'format', 'live_from_start', 'external_downloader', 'external_downloader_args',
                'hls_use_mpegts', 'outtmpl', 'postprocessors', 'download_ranges', 'fixup'})
        cookie_path = os.path.join("users", str(user_id), "cookie.txt")
        if os.path.exists(cookie_path):
            base_opts['cookiefile'] = cookie_path
        if use_proxy:
            from COMMANDS.proxy_cmd import build_proxy_url, get_proxy_config
            proxy_config = get_proxy_config()
            if proxy_config and all(k in proxy_config for k in ('type', 'ip', 'port')):
                proxy_url = build_proxy_url(proxy_config)
                if proxy_url:
                    base_opts['proxy'] = proxy_url
        else:
            from HELPERS.proxy_helper import add_proxy_to_ytdl_opts
            base_opts = add_proxy_to_ytdl_opts(base_opts, url, user_id)
        from HELPERS.pot_helper import add_pot_to_ytdl_opts
        base_opts = add_pot_to_ytdl_opts(base_opts, url)

        file_prefix = os.path.join(user_dir, f"{safe_title}_live_audio")
        # A format chosen by the user only applies to the live-edge recorder
        recorder = LiveEdgeRecorder(base_opts, url) if format_override else open_recorder(base_opts, url, file_prefix)
        logger.info(f"Live audio recording: url={url}, from_start={recorder.from_start}, "
                    f"part_seconds={part_seconds}, max_duration={max_duration}, audio={extension}")
        part = 0
        while True:
            if cancel_event.is_set():
                report(*summary(messages.LIVE_STREAM_DOWNLOAD_STOPPED_MSG, "⏹ Cancelled by user (/clean)"))
                break
            if max_duration is not None and recorded >= max_duration - 1:
                logger.info(f"Live audio reached the duration limit ({max_duration}s)")
                report(*summary(messages.LIVE_STREAM_DOWNLOAD_COMPLETE_MSG))
                break
            if not os.path.isdir(user_dir):
                report(messages.LIVE_STREAM_DOWNLOAD_STOPPED_MSG, messages.LIVE_STREAM_USER_DIRECTORY_DELETED_MSG)
                break
            part += 1
            seconds = part_seconds if max_duration is None else min(part_seconds, int(max_duration - recorded))
            report(f"🎧 {messages.LIVE_STREAM_DOWNLOAD_PROGRESS_MSG}",
                   messages.LIVE_STREAM_CHUNK_NUMBER_MSG.format(chunk=part),
                   messages.LIVE_STREAM_ACCUMULATED_DURATION_MSG.format(duration=int(recorded)))
            stem = f"{file_prefix}_{part:03d}"
            try:
                try:
                    raw_path, ended = recorder.record(stem, seconds, part)
                except StreamEnded:
                    # After the stream ends the URL serves the recording; re-cutting it
                    # would repeat old audio forever.
                    for leftover in glob.glob(glob.escape(stem) + '.*'):
                        os.remove(leftover)
                    if sent_parts == 0:
                        raise RuntimeError("The live stream has already ended; send the link again "
                                           "to download the recording")
                    logger.info(f"Live audio: {url} is no longer live, stopping after {sent_parts} part(s)")
                    report(*summary(messages.LIVE_STREAM_DOWNLOAD_COMPLETE_MSG, messages.LIVE_STREAM_ENDED_MSG))
                    break
                duration = audio_duration(raw_path)
                audio_path = f"{stem}.{extension}"
                if os.path.abspath(raw_path) == os.path.abspath(audio_path):
                    # ffmpeg cannot read and write the same file
                    source_path = f"{stem}.source{os.path.splitext(raw_path)[1]}"
                    os.replace(raw_path, source_path)
                    raw_path = source_path
                encode_audio(raw_path, audio_path, codec_args, timeout=max(600, seconds))
                os.remove(raw_path)
                if cancel_event.is_set() or not os.path.exists(audio_path):
                    report(*summary(messages.LIVE_STREAM_DOWNLOAD_STOPPED_MSG, messages.LIVE_STREAM_FILE_DELETED_MSG))
                    break
                caption = messages.LIVE_STREAM_CHUNK_CAPTION_MSG.format(
                    chunk=part, duration=int(duration), size=humanbytes(os.path.getsize(audio_path)))
                if tags_text:
                    caption += f"\n{tags_text}"
                part_title = f"{title} - {messages.LIVE_STREAM_CHUNK_TITLE_MSG.format(chunk=part)}"
                sent = timed_upload(lambda: app.send_audio(
                    chat_id=user_id, audio=audio_path, caption=caption, duration=int(duration),
                    title=part_title, performer=performer,
                    reply_parameters=ReplyParameters(message_id=message.id),
                ), file_path=audio_path)
                if not sent:
                    raise RuntimeError(f"Failed to upload live stream audio part {part}")
            except Exception as error:
                if sent_parts == 0:
                    raise
                logger.error(f"Live audio part {part} failed after {sent_parts} sent part(s): {error}")
                report(*summary(messages.LIVE_STREAM_DOWNLOAD_STOPPED_MSG))
                break
            sent_parts += 1
            recorded += duration
            try:
                os.remove(audio_path)
            except OSError:
                pass
            if ended is None:
                ended = duration < seconds * ENDED_RATIO
            if ended:
                logger.info(f"Live audio part {part} lasted {duration:.0f}s of {seconds}s: stream ended")
                report(*summary(messages.LIVE_STREAM_DOWNLOAD_COMPLETE_MSG, messages.LIVE_STREAM_ENDED_MSG))
                break
        return sent_parts > 0
    finally:
        if recorder is not None:
            recorder.close()
        unregister_download_cancel_event(user_id)
