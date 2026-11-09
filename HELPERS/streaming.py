"""Prepare local MP4 files for Telegram playback before upload."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile


def _binary(name):
    for candidate in (shutil.which(name), Path(sys.executable).parent / name,
                      Path(__file__).resolve().parents[1] / name):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return str(candidate)
    raise FileNotFoundError(f'{name} executable not found')


def _streams(path):
    result = subprocess.run(
        [_binary('ffprobe'), '-v', 'error', '-show_streams', '-of', 'json', str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)['streams']


def has_faststart(path):
    """Read only top-level atom headers, without loading media into memory."""
    with open(path, 'rb') as source:
        size = os.fstat(source.fileno()).st_size
        while source.tell() + 8 <= size:
            start = source.tell()
            length, kind = struct.unpack('>I4s', source.read(8))
            header = 8
            if length == 1:
                extended = source.read(8)
                if len(extended) != 8:
                    return False
                length = struct.unpack('>Q', extended)[0]
                header = 16
            elif length == 0:
                length = size - start
            if length < header or start + length > size:
                return False
            if kind == b'moov':
                return True
            if kind == b'mdat':
                return False
            source.seek(start + length)
    return False


def prepare_streaming_video(path, *, ensure_h264=True):
    """Atomically prepare MP4; retain dimensions and the source on failure.

    Compatible video/audio streams are copied without quality loss. Explicit
    alternative codec preferences can disable H.264 conversion. MKV and other
    user-selected containers are left intact.
    """
    path = os.path.abspath(path)
    if Path(path).suffix.lower() not in ('.mp4', '.m4v', '.mov'):
        return path
    streams = _streams(path)
    video = next(s for s in streams if s.get('codec_type') == 'video'
                 and not s.get('disposition', {}).get('attached_pic'))
    transcode_video = ensure_h264 and (
        video.get('codec_name') != 'h264' or video.get('pix_fmt') != 'yuv420p')
    transcode_audio = ensure_h264 and any(
        s.get('codec_type') == 'audio' and s.get('codec_name') != 'aac' for s in streams)
    if not transcode_video and not transcode_audio and has_faststart(path):
        return path
    descriptor, candidate = tempfile.mkstemp(prefix='.streaming-', suffix='.mp4',
                                            dir=os.path.dirname(path))
    os.close(descriptor)
    try:
        command = [_binary('ffmpeg'), '-nostdin', '-v', 'error', '-y', '-i', path,
                   '-map', '0', '-c', 'copy']
        if transcode_video:
            # Do not scale: the requested resolution must survive conversion.
            video_index = [s for s in streams if s.get('codec_type') == 'video'].index(video)
            command += [f'-c:v:{video_index}', 'libx264', '-preset', 'fast', '-crf', '18',
                        f'-pix_fmt:v:{video_index}', 'yuv420p']
        if transcode_audio:
            command += ['-c:a', 'aac', '-b:a', '192k']
        command += ['-movflags', '+faststart', candidate]
        subprocess.run(command, check=True, capture_output=True, timeout=3600)
        prepared = next(s for s in _streams(candidate) if s.get('codec_type') == 'video'
                        and not s.get('disposition', {}).get('attached_pic'))
        if (prepared.get('width'), prepared.get('height')) != (video.get('width'), video.get('height')):
            raise ValueError('Streaming preparation changed video dimensions')
        if not has_faststart(candidate):
            raise ValueError('Prepared video has no faststart metadata')
        os.replace(candidate, path)
        return path
    finally:
        if os.path.exists(candidate):
            os.unlink(candidate)
