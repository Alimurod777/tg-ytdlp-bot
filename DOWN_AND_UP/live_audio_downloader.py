from HELPERS.cookie_file import validated_cookie_options
# Live stream audio: record from the live edge in time-limited parts and send
# each part as an audio file as soon as it is ready.

import glob
import json
import math
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
            'live_from_start': False,  # recording from the start cannot be cut into parts
            'noplaylist': True,
            'quiet': False,
            'no_warnings': False,
        }
        from COMMANDS.args_cmd import get_user_ytdlp_args, merge_user_ytdlp_args
        user_args = get_user_ytdlp_args(user_id, url)
        if user_args:
            merge_user_ytdlp_args(base_opts, user_args, skip={
                'format', 'live_from_start', 'external_downloader', 'external_downloader_args',
                'hls_use_mpegts', 'outtmpl', 'postprocessors', 'download_ranges'})
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

        logger.info(f"Live audio recording: url={url}, part_seconds={part_seconds}, "
                    f"max_duration={max_duration}, format={base_opts['format']}, audio={extension}")
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
            stem = os.path.join(user_dir, f"{safe_title}_live_audio_{part:03d}")
            try:
                with yt_dlp.YoutubeDL(validated_cookie_options(recording_options(base_opts, stem, seconds))) as ydl:
                    live_info = ydl.extract_info(url, download=False)
                    if still_live(live_info):
                        ydl.process_ie_result(live_info, download=True)
                if not still_live(live_info):
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
                raw_path = find_recorded_part(stem)
                duration = audio_duration(raw_path)
                audio_path = f"{stem}.{extension}"
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
            if duration < seconds * ENDED_RATIO:
                logger.info(f"Live audio part {part} lasted {duration:.0f}s of {seconds}s: stream ended")
                report(*summary(messages.LIVE_STREAM_DOWNLOAD_COMPLETE_MSG, messages.LIVE_STREAM_ENDED_MSG))
                break
        return sent_parts > 0
    finally:
        unregister_download_cancel_event(user_id)
