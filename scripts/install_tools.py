#!/usr/bin/env python3
"""Install the programs the bot needs on hosts without root access.

Usage: install_tools.py [ffmpeg] [mediainfo] [node] [fonts] [--force]

With no names, every missing tool is installed. Files go to .tools/ inside the
project; scripts/start_alwaysdata.sh puts .tools/bin first on PATH. A tool that
the server already provides is left alone unless --force is given.

  ffmpeg     ffmpeg + ffprobe (merging, conversion, thumbnails, splitting)
  mediainfo  the /mediainfo command
  node       Node.js 22 (YouTube JavaScript challenges, PO token provider)
  fonts      Noto fonts for burned-in subtitles in non-Latin scripts
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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(os.environ.get('BOT_TOOLS_DIR') or ROOT / '.tools')
ARCHITECTURES = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}

FFMPEG_URL = 'https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{arch}-static.tar.xz'
MEDIAINFO_VERSION = '26.10'
MEDIAINFO_URL = ('https://mediaarea.net/download/binary/mediainfo/{version}/'
                 'MediaInfo_CLI_{version}_Lambda_{arch}.zip')
# The publisher offers no checksum file, so the reviewed archives are pinned here.
MEDIAINFO_SHA256 = {
    'amd64': '3087b8d4906a47d876a66a500b8a6af63de8e9da9992d295a702b4ca79fb3fdd',
    'arm64': '8b67e2a2bd22c1fc596f1f774231dd8255f38b79c9fd0a6323d46da1e4c1cb11',
}
NODE_MAJOR = 22
NODE_BASE_URL = f'https://nodejs.org/dist/latest-v{NODE_MAJOR}.x/'
NOTO = 'https://github.com/notofonts/notofonts.github.io/raw/main/fonts/'
FONT_URLS = (
    NOTO + 'NotoSans/hinted/ttf/NotoSans-Regular.ttf',
    NOTO + 'NotoSans/hinted/ttf/NotoSans-Bold.ttf',
    NOTO + 'NotoSansArabic/hinted/ttf/NotoSansArabic-Regular.ttf',
    NOTO + 'NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-Regular.ttf',
    'https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf',
)
FONT_MAGIC = (b'\x00\x01\x00\x00', b'OTTO', b'ttcf')
# Adds the project fonts to whatever the server already has.
FONTS_CONF = '''<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>{fonts_dir}</dir>
  <cachedir>{cache_dir}</cachedir>
</fontconfig>
'''


def log(text):
    print(f'[tools-install] {text}', file=sys.stderr, flush=True)


def machine_arch(machine=None):
    arch = ARCHITECTURES.get((machine or platform.machine()).lower())
    if not arch or sys.platform != 'linux':
        raise RuntimeError('automatic install supports only Linux on x86_64 or arm64')
    return arch


def bin_dir(root=None):
    return Path(root or TOOLS_ROOT) / 'bin'


def works(path, flag='-version'):
    try:
        return subprocess.run([str(path), flag], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def have(name, root=None, flag='-version'):
    """The program is on PATH or already installed in the tools directory."""
    return bool(shutil.which(name)) or works(bin_dir(root) / name, flag)


def node_major(path):
    try:
        output = subprocess.run([str(path), '-p', 'process.versions.node.split(".")[0]'],
                                capture_output=True, text=True, timeout=30).stdout
        return int(output.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def read_url(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def download(url, target):
    with urllib.request.urlopen(url, timeout=60) as response, open(target, 'wb') as output:
        shutil.copyfileobj(response, output, 1024 * 1024)


def digest_of(path, algorithm):
    digest = hashlib.new(algorithm)
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def place(source, target):
    """Move a finished file into place so a half-written program is never left behind."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.partial')
    with open(partial, 'wb') as output:
        shutil.copyfileobj(source, output, 1024 * 1024)
    partial.chmod(0o755)
    partial.replace(target)


def workdir(root):
    root = Path(root or TOOLS_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


# ---- ffmpeg -----------------------------------------------------------------

def ffmpeg_present(root=None):
    return have('ffmpeg', root) and have('ffprobe', root)


def install_ffmpeg(root=None, url=None):
    url = url or FFMPEG_URL.format(arch=machine_arch())
    log(f'ffmpeg: downloading {url} (about 40 MB, 160 MB unpacked)')
    with workdir(root) as tmp:
        archive = Path(tmp) / 'ffmpeg.tar.xz'
        download(url, archive)
        expected = read_url(url + '.md5').decode().split()[0].lower()
        if digest_of(archive, 'md5') != expected:
            raise RuntimeError('ffmpeg: download is corrupted (checksum mismatch); try again')
        found = []
        with tarfile.open(archive, 'r:xz') as tar:
            for member in tar:
                name = Path(member.name).name
                if name in ('ffmpeg', 'ffprobe') and member.isfile():
                    with tar.extractfile(member) as source:
                        place(source, bin_dir(root) / name)
                    found.append(name)
    broken = [n for n in ('ffmpeg', 'ffprobe') if n not in found or not works(bin_dir(root) / n)]
    if broken:
        raise RuntimeError(f'ffmpeg: installed files do not run on this server: {", ".join(broken)}')


# ---- mediainfo --------------------------------------------------------------

def mediainfo_present(root=None):
    return have('mediainfo', root, '--Version')


def install_mediainfo(root=None, url=None, sha256=None):
    arch = machine_arch()
    url = url or MEDIAINFO_URL.format(version=MEDIAINFO_VERSION,
                                      arch='x86_64' if arch == 'amd64' else 'arm64')
    log(f'mediainfo: downloading {url} (about 6 MB)')
    with workdir(root) as tmp:
        archive = Path(tmp) / 'mediainfo.zip'
        download(url, archive)
        if digest_of(archive, 'sha256') != (sha256 or MEDIAINFO_SHA256[arch]):
            raise RuntimeError('mediainfo: download does not match the pinned checksum')
        with zipfile.ZipFile(archive) as bundle:
            member = next((n for n in bundle.namelist() if Path(n).name == 'mediainfo'), None)
            if member is None:
                raise RuntimeError('mediainfo: the archive does not contain the program')
            with bundle.open(member) as source:
                place(source, bin_dir(root) / 'mediainfo')
    if not works(bin_dir(root) / 'mediainfo', '--Version'):
        raise RuntimeError('mediainfo: the installed program does not run on this server')


# ---- node -------------------------------------------------------------------

def node_present(root=None):
    system = shutil.which('node')
    if system and node_major(system) >= NODE_MAJOR:
        return True
    return node_major(bin_dir(root) / 'node') >= NODE_MAJOR


def pick_node_archive(shasums, arch):
    """Return (file name, sha256) of the Linux tarball listed in SHASUMS256.txt."""
    suffix = f'-linux-{"x64" if arch == "amd64" else "arm64"}.tar.xz'
    for line in shasums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].endswith(suffix):
            return parts[1], parts[0].lower()
    raise RuntimeError('node: no Linux build is listed for this architecture')


def install_node(root=None, base_url=None):
    root = Path(root or TOOLS_ROOT)
    base_url = base_url or NODE_BASE_URL
    name, expected = pick_node_archive(read_url(base_url + 'SHASUMS256.txt').decode(), machine_arch())
    log(f'node: downloading {base_url}{name} (about 30 MB, 190 MB unpacked)')
    with workdir(root) as tmp:
        archive = Path(tmp) / name
        download(base_url + name, archive)
        if digest_of(archive, 'sha256') != expected:
            raise RuntimeError('node: download is corrupted (checksum mismatch); try again')
        unpacked = Path(tmp) / 'unpacked'
        with tarfile.open(archive, 'r:xz') as tar:
            try:
                tar.extractall(unpacked, filter='data')
            except TypeError:  # Python without extraction filters
                tar.extractall(unpacked)
        tree = next(p for p in unpacked.iterdir() if p.is_dir())
        target = root / 'node'
        shutil.rmtree(target, ignore_errors=True)
        tree.rename(target)
    bin_dir(root).mkdir(parents=True, exist_ok=True)
    for program in ('node', 'npm', 'npx'):
        link = bin_dir(root) / program
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(Path('..') / 'node' / 'bin' / program)
    if node_major(bin_dir(root) / 'node') < NODE_MAJOR:
        raise RuntimeError('node: the installed program does not run on this server')


# ---- fonts ------------------------------------------------------------------

def fonts_dir(root=None):
    return Path(root or TOOLS_ROOT) / 'fonts'


def fonts_present(root=None):
    directory = fonts_dir(root)
    return (directory / 'fonts.conf').is_file() and all(
        (directory / url.rsplit('/', 1)[1]).is_file() for url in FONT_URLS)


def install_fonts(root=None, urls=None):
    directory = fonts_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    log('fonts: downloading Noto fonts (about 18 MB)')
    for url in urls or FONT_URLS:
        data = read_url(url)
        if data[:4] not in FONT_MAGIC:
            raise RuntimeError(f'fonts: {url.rsplit("/", 1)[1]} is not a font file')
        target = directory / url.rsplit('/', 1)[1]
        partial = target.with_name(target.name + '.partial')
        partial.write_bytes(data)
        partial.replace(target)
    (directory / 'fonts.conf').write_text(FONTS_CONF.format(
        fonts_dir=directory, cache_dir=directory / 'cache'), encoding='utf-8')


TOOLS = {
    'ffmpeg': (ffmpeg_present, install_ffmpeg),
    'mediainfo': (mediainfo_present, install_mediainfo),
    'node': (node_present, install_node),
    'fonts': (fonts_present, install_fonts),
}


def status(root=None):
    """One line per tool: where it comes from, or that it is missing."""
    lines = []
    for name, (present, _) in TOOLS.items():
        if not present(root):
            state = 'MISSING'
        elif name == 'fonts':
            state = f'installed in {fonts_dir(root)}'
        else:
            local = bin_dir(root) / name
            state = (f'installed in {bin_dir(root)}' if local.exists()
                     else f'provided by the server ({shutil.which(name)})')
        lines.append(f'{name:<10} {state}')
    return lines


def main(argv, root=None):
    names = [a for a in argv if not a.startswith('-')]
    flags = {a for a in argv if a.startswith('-')}
    unknown = [n for n in names if n not in TOOLS] + sorted(flags - {'--force', '--status'})
    if unknown:
        log(f'unknown argument: {", ".join(unknown)}')
        print(__doc__, file=sys.stderr)
        return 2
    if '--status' in flags:
        print('\n'.join(status(root)))
        return 0
    failed = []
    for name in names or list(TOOLS):
        present, install = TOOLS[name]
        if '--force' not in flags and present(root):
            log(f'{name}: already available')
            continue
        try:
            install(root)
            log(f'{name}: installed')
        except (RuntimeError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
            log(str(error) if isinstance(error, RuntimeError) else f'{name}: {error}')
            failed.append(name)
    if failed:
        log(f'failed: {", ".join(failed)}')
        return 1
    log('done; restart the bot')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
