"""Evidence-based cookie diagnostics; no bot/config imports and no secret logging."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import socket
import ssl
import tempfile
import time
from urllib.parse import parse_qs, urlparse

MAX_COOKIE_BYTES = 100 * 1024
HEADERS = {'# Netscape HTTP Cookie File', '# HTTP Cookie File'}
AUTH_NAMES = {'LOGIN_INFO', 'SAPISID', '__Secure-1PAPISID', '__Secure-3PAPISID',
              'SID', '__Secure-1PSID', '__Secure-3PSID', 'HSID', 'SSID'}


@dataclass(frozen=True)
class CookieFileReport:
    code: str
    total: int = 0
    active: int = 0
    expired: int = 0
    session: int = 0
    youtube: int = 0
    youtube_active: int = 0
    auth_active: int = 0
    auth_expired: int = 0
    line: int = 0
    # Cookie values may exist only in this internal, non-printable field.
    content: str = field(default='', repr=False, compare=False)


@dataclass(frozen=True)
class CookieCheckResult:
    file: CookieFileReport
    video: str = 'not_tested'
    auth: str = 'not_tested'

    @property
    def usable(self):
        # Metadata or a successful login alone does not establish media access.
        return (self.file.code == 'ready' and self.video == 'accessible'
                and self.auth != 'revoked')

    @property
    def replaceable(self):
        # Network/format/content failures do not justify discarding the file.
        return self.file.code not in {'ready', 'other_service'} or self.auth == 'revoked'


def _youtube_domain(domain):
    host = domain.lower().lstrip('.')
    return host == 'youtube.com' or host.endswith('.youtube.com')


def inspect_cookie_file(path, *, now=None):
    """Validate every Netscape row, HttpOnly entries, and recorded expirations."""
    now = time.time() if now is None else now
    try:
        with Path(path).open('rb') as handle:
            raw = handle.read(MAX_COOKIE_BYTES + 1)
    except FileNotFoundError:
        return CookieFileReport('missing')
    except OSError:
        return CookieFileReport('unreadable')
    if len(raw) > MAX_COOKIE_BYTES:
        return CookieFileReport('too_large')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError:
        return CookieFileReport('encoding')
    lines = text.splitlines()
    if not lines or lines[0].strip() not in HEADERS:
        return CookieFileReport('invalid_header')
    counts = dict(total=0, active=0, expired=0, session=0, youtube=0,
                  youtube_active=0, auth_active=0, auth_expired=0)
    normalised = ['# Netscape HTTP Cookie File']
    for number, raw_line in enumerate(lines[1:], 2):
        if not raw_line.strip():
            continue
        line = raw_line
        if line.startswith('#HttpOnly_'):
            line = line[len('#HttpOnly_'):]
        elif line.startswith('#'):
            continue
        columns = line.split('\t')
        if len(columns) != 7:
            return CookieFileReport('invalid_row', line=number)
        domain, subdomains, cookie_path, secure, expires, name, value = columns
        try:
            expiry = int(expires or '0')
            if expiry < 0:
                raise ValueError
        except ValueError:
            return CookieFileReport('invalid_expiry', line=number)
        if not re.fullmatch(r'\.?[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*', domain):
            return CookieFileReport('invalid_domain', line=number)
        if (subdomains not in {'TRUE', 'FALSE'} or secure not in {'TRUE', 'FALSE'}
                or (subdomains == 'TRUE') != domain.startswith('.')):
            return CookieFileReport('invalid_flags', line=number)
        if not cookie_path.startswith('/'):
            return CookieFileReport('invalid_path', line=number)
        if not name or '\x00' in line:
            return CookieFileReport('invalid_name', line=number)
        # A blank/zero expiration is a session cookie, not an expired cookie.
        active = expiry == 0 or expiry > now
        counts['total'] += 1
        counts['active' if active else 'expired'] += 1
        counts['session'] += expiry == 0
        if _youtube_domain(domain):
            counts['youtube'] += 1
            counts['youtube_active'] += active
            if name in AUTH_NAMES and value:
                counts['auth_active' if active else 'auth_expired'] += 1
        # Normalise BOM/header/session expiry for yt-dlp without editing the source.
        columns[4] = str(expiry)
        normalised.append(('#HttpOnly_' if raw_line.startswith('#HttpOnly_') else '')
                          + '\t'.join(columns))
    code = ('empty' if not counts['total'] else 'expired' if not counts['active']
            else 'youtube_expired' if counts['youtube'] and not counts['youtube_active']
            else 'ready' if counts['youtube'] else 'other_service')
    return CookieFileReport(code, **counts, content='\n'.join(normalised) + '\n')


def classify_error(error):
    """Return a safe reason code; never display arbitrary server/exception text."""
    text = str(error).lower().replace('’', "'")
    current = error
    seen = set()
    for _ in range(6):
        if not isinstance(current, BaseException) or id(current) in seen:
            break
        seen.add(id(current))
        if isinstance(current, (TimeoutError, socket.timeout)):
            text += ' timed out'
        elif isinstance(current, socket.gaierror):
            text += ' name resolution'
        elif isinstance(current, ssl.SSLError):
            text += ' ssl error'
        elif isinstance(current, ConnectionError):
            text += ' connection reset'
        status = getattr(current, 'status', None) or getattr(current, 'code', None)
        if isinstance(status, int):
            text += f' http error {status}'
        cause = getattr(current, 'cause', None) or current.__cause__
        if cause is None:
            exc_info = getattr(current, 'exc_info', None)
            cause = exc_info[1] if isinstance(exc_info, tuple) and len(exc_info) > 1 else None
        current = cause
    # More specific failures must precede generic "sign in" or "cookies" advice.
    groups = (
        ('bot_blocked', ("confirm you're not a bot", 'confirm you are not a bot',
                         'captcha', 'unusual traffic', 'automated queries', 'cloudflare')),
        ('rate_limited', ('http error 429', 'http error 402', 'too many requests', 'rate limit')),
        ('geo_blocked', ('not available in your country', 'blocked in your country',
                         'not made this video available in your country', 'geo-restricted',
                         'geo restricted', 'geographic restriction', 'blocked in your region')),
        ('copyright_restricted', ('copyright', 'dmca')),
        ('private_video', ('private video', 'video is private')),
        ('membership_required', ('members-only', 'members only', 'join this channel',
                                 'premium content', 'subscriber-only', 'subscription required')),
        ('removed_video', ('video has been removed', 'video has been deleted', 'video was deleted')),
        ('content_unavailable', ('video is unavailable', 'video unavailable',
                                'video not available', 'this video is not available')),
        ('proxy_error', ('proxyerror', 'proxy error', 'proxy connection', 'tunnel connection',
                         '407 proxy', 'http error 407', 'socks')),
        ('dns_error', ('name or service not known', 'name resolution', 'getaddrinfo failed',
                       'nodename nor servname', 'failed to resolve')),
        ('timeout', ('timed out', 'timeout')),
        ('tls_error', ('certificate_verify_failed', 'certificate verify failed', 'ssl error',
                       'tls handshake')),
        ('network_error', ('connection refused', 'connection reset', 'network is unreachable',
                           'remote end closed connection', 'connection aborted')),
        ('revoked', ('cookies are no longer valid', 'cookies have been rotated',
                     'cookies are invalid', 'cookies have expired')),
        ('age_restricted', ('age-restricted', 'age restricted', 'confirm your age')),
        ('auth_required', ('login required', 'sign in', 'sign-in', 'log in',
                           'authentication required', 'http error 401', 'unauthorized')),
        ('http_forbidden', ('http error 403', '403 forbidden', 'access denied', 'forbidden')),
        ('server_error', ('http error 500', 'http error 502', 'http error 503', 'http error 504')),
        ('po_token', ('po token', 'po_token', 'potoken')),
        ('js_required', ('javascript runtime', 'js runtime', 'javascript challenge',
                         'n challenge', 'nsig extraction', 'signature extraction')),
        ('formats_unavailable', ('requested format is not available', 'no formats found',
                                 'only images are available', 'no video formats found')),
        ('extractor_error', ('unable to extract', 'unsupported url', 'extractor error')),
    )
    for code, phrases in groups:
        if any(phrase in text for phrase in phrases):
            return code
    return 'unknown_error'


class _DiagnosticLogger:
    def __init__(self):
        self.codes = set()

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def warning(self, message):
        self.codes.add(classify_error(message))

    def error(self, message):
        self.codes.add(classify_error(message))


def source_download_failure(status, error):
    """Describe a cookie source failure separately from the target site's login."""
    codes = {401: 'source_auth_required', 403: 'source_forbidden', 404: 'source_missing',
             429: 'rate_limited', 402: 'rate_limited', 500: 'server_error',
             502: 'server_error', 503: 'server_error', 504: 'server_error'}
    reason = codes.get(status, classify_error(error or ''))
    return CookieCheckResult(CookieFileReport('source_fetch_failed'), reason)


def _login_state(page):
    """Read only explicit boolean server state from parsed ytcfg JSON objects."""
    states = set()
    decoder = json.JSONDecoder()
    for match in re.finditer(r'ytcfg\s*\.\s*set\s*\(\s*', page):
        try:
            data, _ = decoder.raw_decode(page[match.end():])
        except ValueError:
            continue
        if isinstance(data, dict) and type(data.get('LOGGED_IN')) is bool:
            states.add(data['LOGGED_IN'])
    if len(states) != 1:
        return 'unknown'
    return 'authenticated' if states.pop() else 'not_authenticated'


def _probe_login(ydl):
    # Same yt-dlp cookie jar, proxy, TLS settings and network transport as the video.
    try:
        with ydl.urlopen('https://www.youtube.com/') as response:
            host = urlparse(response.url).hostname if getattr(response, 'url', None) else ''
            if host == 'consent.youtube.com' or host == 'consent.google.com':
                return 'consent_required'
            if host and not (host == 'youtube.com' or host.endswith('.youtube.com')):
                return 'unknown'
            page = response.read(2 * 1024 * 1024).decode('utf-8', errors='replace')
        return _login_state(page)
    except Exception as error:
        return classify_error(error)


def valid_youtube_test_url(url):
    """Limit manual probes to canonical YouTube video URLs, without credentials."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {'https', 'http'} or parsed.username or parsed.password or parsed.port:
            return False
        host = (parsed.hostname or '').lower()
        if host in {'youtu.be', 'www.youtu.be'}:
            video_id = parsed.path.strip('/')
        elif host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'}:
            if parsed.path == '/watch':
                video_id = parse_qs(parsed.query).get('v', [''])[0]
            elif re.fullmatch(r'/(shorts|live|embed)/[^/]+/?', parsed.path):
                video_id = parsed.path.strip('/').split('/')[1]
            else:
                return False
        else:
            return False
        return bool(re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id))
    except ValueError:
        return False


def diagnose_cookie_file(path, url, *, options=None, ydl_factory=None, check_login=True):
    local = inspect_cookie_file(path)
    if local.code != 'ready':
        return CookieCheckResult(local)
    if not valid_youtube_test_url(url):
        return CookieCheckResult(local, 'invalid_url')
    if ydl_factory is None:
        from yt_dlp import YoutubeDL
        ydl_factory = YoutubeDL
    diagnostics = _DiagnosticLogger()
    opts = dict(options or {})
    opts.update(quiet=True, no_warnings=True, verbose=False, logger=diagnostics,
                skip_download=True, noplaylist=True, ignore_no_formats_error=True,
                socket_timeout=20, retries=1, extractor_retries=1,
                cachedir=False, writesubtitles=False, writeautomaticsub=False)
    # Do not force the old TV client, whose auth/format support changes over time.
    extractor_args = {key: dict(value) for key, value in opts.get('extractor_args', {}).items()}
    youtube_args = extractor_args.get('youtube', {})
    youtube_args.pop('player_client', None)
    extractor_args['youtube'] = youtube_args
    opts['extractor_args'] = extractor_args
    video, auth = 'unknown_error', 'not_tested'
    with tempfile.TemporaryDirectory(prefix='cookie-check-') as directory:
        cookie_copy = Path(directory) / 'cookies.txt'
        cookie_copy.write_text(local.content, encoding='utf-8')
        opts['cookiefile'] = str(cookie_copy)
        try:
            with ydl_factory(opts) as ydl:
                if check_login:
                    auth = _probe_login(ydl)
                try:
                    info = ydl.extract_info(url, download=False)
                    formats = (info or {}).get('formats') or []
                    playable = [fmt for fmt in formats if fmt.get('url')
                                and not fmt.get('has_drm')
                                and (fmt.get('vcodec') not in (None, 'none')
                                     or fmt.get('acodec') not in (None, 'none'))]
                    video = 'accessible' if playable else 'formats_unavailable'
                    if video != 'accessible':
                        for reason in ('bot_blocked', 'po_token', 'js_required'):
                            if reason in diagnostics.codes:
                                video = reason
                                break
                except Exception as error:
                    video = classify_error(error)
                if 'revoked' in diagnostics.codes:
                    auth = 'revoked'
        except Exception as error:
            video = classify_error(error)
    return CookieCheckResult(local, video, auth)
