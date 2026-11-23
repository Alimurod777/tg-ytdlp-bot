#!/usr/bin/env python3
"""Install static ffmpeg and ffprobe into .tools/bin for hosts without root access.

scripts/start_alwaysdata.sh adds that directory to PATH. Nothing is downloaded
when both programs are already available, unless --force is given.
"""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(os.environ.get('BOT_TOOLS_DIR') or ROOT / '.tools' / 'bin')
BASE_URL = 'https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{arch}-static.tar.xz'
ARCHITECTURES = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}
PROGRAMS = ('ffmpeg', 'ffprobe')


def log(text):
    print(f'[ffmpeg-install] {text}', file=sys.stderr, flush=True)


def archive_url(machine=None):
    arch = ARCHITECTURES.get((machine or platform.machine()).lower())
    if not arch or sys.platform != 'linux':
        raise RuntimeError('automatic install supports only Linux on x86_64 or arm64')
    return BASE_URL.format(arch=arch)


def works(path):
    try:
        return subprocess.run([str(path), '-version'], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def missing_programs(tools_dir=None):
    """Programs found neither on PATH nor in the tools directory."""
    tools_dir = Path(tools_dir or TOOLS_DIR)
    return [name for name in PROGRAMS
            if not shutil.which(name) and not works(tools_dir / name)]


def download(url, target):
    with urllib.request.urlopen(url, timeout=60) as response, open(target, 'wb') as output:
        shutil.copyfileobj(response, output, 1024 * 1024)


def md5_of(path):
    digest = hashlib.md5()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def extract_programs(archive, tools_dir):
    """Copy only the two programs out of the archive, ignoring its directory layout."""
    tools_dir.mkdir(parents=True, exist_ok=True)
    found = []
    with tarfile.open(archive, 'r:xz') as tar:
        for member in tar:
            name = Path(member.name).name
            if name in PROGRAMS and member.isfile():
                target = tools_dir / name
                partial = tools_dir / (name + '.partial')
                with tar.extractfile(member) as source, open(partial, 'wb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                partial.chmod(0o755)
                partial.replace(target)
                found.append(name)
    return found


def install(tools_dir=None, force=False, url=None):
    tools_dir = Path(tools_dir or TOOLS_DIR)
    if not force and not missing_programs(tools_dir):
        log('ffmpeg and ffprobe are already available')
        return 0
    try:
        url = url or archive_url()
    except RuntimeError as error:
        log(str(error))
        return 1
    log(f'downloading {url} (about 40 MB, 160 MB unpacked)')
    with tempfile.TemporaryDirectory(dir=tools_dir.parent if tools_dir.parent.is_dir() else None) as tmp:
        archive = Path(tmp) / 'ffmpeg.tar.xz'
        try:
            download(url, archive)
            with urllib.request.urlopen(url + '.md5', timeout=60) as response:
                expected = response.read().decode().split()[0].lower()
        except OSError as error:
            log(f'download failed: {error}')
            return 1
        if md5_of(archive) != expected:
            log('download is corrupted (checksum mismatch); try again')
            return 1
        found = extract_programs(archive, tools_dir)
    broken = [name for name in PROGRAMS if name not in found or not works(tools_dir / name)]
    if broken:
        log(f'installed files do not run on this server: {", ".join(broken)}')
        return 1
    log(f'installed into {tools_dir}; restart the bot')
    return 0


if __name__ == '__main__':
    sys.exit(install(force='--force' in sys.argv[1:]))
