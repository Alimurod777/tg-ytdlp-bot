"""Offline regression checks: python scripts/check_cookie_diagnostics.py -v."""

import ast
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import socket
import ssl
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from HELPERS.cookie_validator import (
    CookieCheckResult, CookieFileReport, _login_state, classify_error,
    diagnose_cookie_file, inspect_cookie_file, valid_youtube_test_url, source_download_failure,
)
from HELPERS.cookie_diagnostics_text import TEXT, render_cookie_report

URL = 'https://www.youtube.com/watch?v=abcdefghijk'
SECRET = 'PRIVATE_COOKIE_VALUE_MUST_NOT_LEAK'
HEADER = '# Netscape HTTP Cookie File\n'
ROW = f'.youtube.com\tTRUE\t/\tTRUE\t0\tSID\t{SECRET}\n'
PLAYABLE = {'title': 'Hi', 'formats': [{'url': 'https://media.example/video', 'vcodec': 'avc1'}]}


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def command_function(name, namespace):
    source = ast.parse((ROOT / 'COMMANDS/cookies_cmd.py').read_text())
    node = next(item for item in source.body if isinstance(item, ast.FunctionDef) and item.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<cookie-command>', 'exec'), namespace)
    return namespace[name]


class CookieDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'cookie.txt'
        self.path.write_text(HEADER + ROW)

    def local(self, text):
        self.path.write_text(text)
        return inspect_cookie_file(self.path, now=1000)

    def probe(self, *, info=PLAYABLE, error=None, login='true', login_error=None,
              warnings=(), mutate_copy=False, check_login=True):
        captured = {}
        class FakeResponse:
            url = 'https://www.youtube.com/'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return ('ytcfg.set({"LOGGED_IN":' + login + '});').encode()
        class FakeYDL:
            def __init__(self, options):
                self.options = options
                captured.update(options)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def urlopen(self, url):
                if login_error:
                    raise login_error
                return FakeResponse()
            def extract_info(self, url, download):
                assert download is False
                for warning in warnings:
                    self.options['logger'].warning(warning)
                if mutate_copy:
                    Path(self.options['cookiefile']).write_text('changed by yt-dlp')
                if error:
                    raise error
                return info
        result = diagnose_cookie_file(self.path, URL, ydl_factory=FakeYDL, check_login=check_login)
        return result, captured

    def test_file_rows_http_only_and_session_expiration(self):
        result = self.local(HEADER + '#HttpOnly_' + ROW + ROW.replace('\t0\t', '\t999\t'))
        self.assertEqual((result.code, result.active, result.expired, result.session), ('ready', 1, 1, 1))
        self.assertEqual(result.auth_expired, 1)
        self.assertNotIn(SECRET, repr(result))

    def test_all_expired_or_empty_files(self):
        self.assertEqual(self.local(HEADER + ROW.replace('\t0\t', '\t999\t')).code, 'expired')
        self.assertEqual(self.local(HEADER).code, 'empty')
        self.assertEqual(self.local('not a cookie').code, 'invalid_header')
        self.assertEqual(self.local('# HTTP Cookie File\n' + ROW).code, 'ready')
        self.assertEqual(self.local('\ufeff' + HEADER + ROW).code, 'ready')
        self.assertEqual(self.local(HEADER + ROW.replace('\t0\t', '\t\t')).session, 1)

    def test_validates_rows_past_first_four_kilobytes(self):
        result = self.local(HEADER + '# comment\n' * 500 + ROW + 'broken row\n')
        self.assertEqual(result.code, 'invalid_row')
        self.assertEqual(result.line, 503)

    def test_malformed_expiry_and_flags(self):
        for text, code in ((ROW.replace('\t0\t', '\t-1\t'), 'invalid_expiry'),
                           (ROW.replace('\t0\t', '\tbad\t'), 'invalid_expiry'),
                           (ROW.replace('TRUE', 'MAYBE', 1), 'invalid_flags'),
                           (ROW.replace('\t/\t', '\tbad\t'), 'invalid_path'),
                           (ROW.replace('.youtube.com', 'evil/youtube.com'), 'invalid_domain')):
            with self.subTest(code=code, text=text.replace(SECRET, '<redacted>')):
                self.assertEqual(self.local(HEADER + text).code, code)

    def test_other_site_and_expired_youtube_not_confused(self):
        other = ROW.replace('.youtube.com', '.instagram.com')
        self.assertEqual(self.local(HEADER + other).code, 'other_service')
        result = self.local(HEADER + other + ROW.replace('\t0\t', '\t999\t'))
        self.assertEqual(result.code, 'youtube_expired')
        self.assertEqual(self.local(HEADER + ROW.replace('.youtube.com', '.notyoutube.com')).code,
                         'other_service')

    def test_file_size_encoding_and_unreadable_errors(self):
        self.path.write_bytes(b'X' * (100 * 1024 + 1))
        self.assertEqual(inspect_cookie_file(self.path).code, 'too_large')
        self.path.write_bytes(b'\xff\xfe')
        self.assertEqual(inspect_cookie_file(self.path).code, 'encoding')
        self.assertEqual(inspect_cookie_file(self.path.with_name('missing')).code, 'missing')
        with patch('pathlib.Path.open', side_effect=PermissionError()):
            self.assertEqual(inspect_cookie_file(self.path).code, 'unreadable')

    def test_title_only_or_drm_is_not_a_success(self):
        for info in ({'title': 'Video'}, {'title': 'Video', 'formats': []},
                     {'formats': [{'url': 'https://example/image', 'vcodec': 'none', 'acodec': 'none'}]},
                     {'formats': [{'url': 'https://example/video', 'vcodec': 'avc1', 'has_drm': True}]}):
            result, _ = self.probe(info=info)
            self.assertFalse(result.usable)
            self.assertEqual(result.video, 'formats_unavailable')
            self.assertFalse(result.replaceable)

    def test_media_access_and_session_are_separate(self):
        result, _ = self.probe(login='false')
        self.assertTrue(result.usable)
        self.assertEqual(result.auth, 'not_authenticated')
        text = render_cookie_report(result, 'uz')
        self.assertIn('LOGGED_IN=false', text)
        self.assertIn('yolg\'iz o\'zi isbotlamaydi', text)
        self.assertNotIn(SECRET, text)
        authenticated, _ = self.probe(login='true')
        self.assertEqual(authenticated.auth, 'authenticated')

    def test_ambiguous_login_response_stays_unknown(self):
        for page in ('<html>blocked</html>', '{"LOGGED_IN":true}',
                     'ytcfg.set({"LOGGED_IN":"true"});',
                     'ytcfg.set({"LOGGED_IN":true});ytcfg.set({"LOGGED_IN":false});'):
            self.assertEqual(_login_state(page), 'unknown')
        self.assertEqual(_login_state('ytcfg.set({"LOGGED_IN":true,"nested":{"a":1}});'),
                         'authenticated')

    def test_errors_report_cause_without_declaring_cookie_expired(self):
        cases = {
            "Sign in to confirm you're not a bot. Use --cookies": 'bot_blocked',
            'HTTP Error 429: Too Many Requests': 'rate_limited',
            'HTTP Error 403: Forbidden': 'http_forbidden',
            'HTTP Error 503: Unavailable': 'server_error',
            'private video. Sign in to access': 'private_video',
            'Video unavailable. Not available in your country': 'geo_blocked',
            'Login required': 'auth_required',
            'Members-only video': 'membership_required',
            'Video has been removed': 'removed_video',
            'Unavailable due to copyright': 'copyright_restricted',
            'Video unavailable': 'content_unavailable',
            'Sign in to confirm your age': 'age_restricted',
            'Requested format is not available': 'formats_unavailable',
            'Unable to extract initial player response': 'extractor_error',
            'PO Token required': 'po_token',
            'No supported JavaScript runtime': 'js_required',
            'proxy connection failed': 'proxy_error',
        }
        for message, code in cases.items():
            with self.subTest(code=code):
                result, _ = self.probe(error=RuntimeError(message + SECRET))
                self.assertEqual(result.video, code)
                self.assertFalse(result.replaceable)
                for language in ('uz', 'en', 'de'):
                    self.assertNotIn(SECRET, render_cookie_report(result, language))

    def test_source_http_errors_do_not_blame_the_target_session(self):
        for status, reason in ((401, 'source_auth_required'), (403, 'source_forbidden'),
                               (404, 'source_missing'), (429, 'rate_limited')):
            result = source_download_failure(status, SECRET)
            self.assertEqual(result.video, reason)
            self.assertNotIn(SECRET, render_cookie_report(result, 'uz'))
            self.assertIn('Cookie manbasi', render_cookie_report(result, 'uz'))

    def test_typed_transport_errors_and_nested_http_status(self):
        for error, code in ((TimeoutError(), 'timeout'), (socket.gaierror(), 'dns_error'),
                            (ssl.SSLError(), 'tls_error'), (ConnectionError(), 'network_error')):
            self.assertEqual(classify_error(error), code)
        outer = RuntimeError('failed')
        outer.cause = RuntimeError('HTTP response')
        outer.cause.status = 429
        self.assertEqual(classify_error(outer), 'rate_limited')

    def test_warning_revoked_and_no_format_diagnostics(self):
        result, _ = self.probe(warnings=['The provided YouTube account cookies are no longer valid. ' + SECRET])
        self.assertEqual(result.auth, 'revoked')
        self.assertFalse(result.usable)
        self.assertTrue(result.replaceable)
        for warning, reason in [('PO Token required', 'po_token'),
                                ('nsig extraction failed', 'js_required')]:
            result, _ = self.probe(info={'title': 'Video'}, warnings=[warning])
            self.assertEqual(result.video, reason)
        self.assertNotIn(SECRET, repr(result))

    def test_login_probe_network_failure_does_not_hide_media_result(self):
        result, _ = self.probe(login_error=TimeoutError())
        self.assertEqual(result.auth, 'timeout')
        self.assertEqual(result.video, 'accessible')
        self.assertTrue(result.usable)

    def test_checks_copy_and_does_not_force_tv_client(self):
        original = self.path.read_bytes()
        result, options = self.probe(mutate_copy=True)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertNotEqual(options['cookiefile'], str(self.path))
        self.assertFalse(Path(options['cookiefile']).exists())
        self.assertNotIn('player_client', options['extractor_args']['youtube'])
        self.assertFalse(options['verbose'])
        self.assertTrue(result.usable)

    def test_local_errors_and_invalid_url_skip_network(self):
        factory = Mock(side_effect=AssertionError('network should not run'))
        self.path.write_text(HEADER + ROW.replace('\t0\t', '\t1\t'))
        self.assertEqual(diagnose_cookie_file(self.path, URL, ydl_factory=factory).file.code, 'expired')
        self.path.write_text(HEADER + ROW)
        result = diagnose_cookie_file(self.path, 'https://127.0.0.1/private', ydl_factory=factory)
        self.assertEqual(result.video, 'invalid_url')
        factory.assert_not_called()
        for url in (URL, 'https://youtu.be/abcdefghijk', 'https://youtube.com/shorts/abcdefghijk'):
            self.assertTrue(valid_youtube_test_url(url))
        for url in ('https://youtube.com.evil.test/watch?v=abcdefghijk',
                    'https://user:pass@youtube.com/watch?v=abcdefghijk',
                    'https://youtube.com:444/watch?v=abcdefghijk', 'file:///etc/passwd'):
            self.assertFalse(valid_youtube_test_url(url))

    def test_legacy_cookie_error_classifier_excludes_external_failures(self):
        function = command_function('is_youtube_cookie_error', {'classify_error': classify_error})
        for text in ('HTTP Error 403', 'no formats found', 'Unable to extract',
                     "Sign in to confirm you're not a bot", 'DNS name resolution failed'):
            self.assertFalse(function(text))
        self.assertTrue(function('Login required'))

    def test_failed_source_preserves_saved_file_and_cleans_candidate(self):
        for result in (CookieCheckResult(CookieFileReport('ready'), 'timeout'),
                       CookieCheckResult(CookieFileReport('invalid_row'))):
            namespace = {'os': os, 'diagnose_youtube_cookie_file': Mock(return_value=result)}
            function = command_function('_validate_cookie_candidate', namespace)
            before = self.path.read_bytes()
            actual = function(b'candidate', str(self.path), 123)
            self.assertEqual(actual, result)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse(list(self.path.parent.glob('.cookie-candidate-*')))

    def test_successful_source_installs_candidate_atomically(self):
        result = CookieCheckResult(CookieFileReport('ready', content='candidate'), 'accessible')
        namespace = {'os': os, 'diagnose_youtube_cookie_file': Mock(return_value=result)}
        function = command_function('_validate_cookie_candidate', namespace)
        function(b'candidate', str(self.path), 123)
        self.assertEqual(self.path.read_bytes(), b'candidate')
        self.assertFalse(list(self.path.parent.glob('.cookie-candidate-*')))

    def test_invalid_upload_preserves_user_cookie(self):
        namespace = {'os': os, 'inspect_cookie_file': inspect_cookie_file,
                     'CookieCheckResult': CookieCheckResult,
                     '_render_cookie_diagnostic': lambda result, user: render_cookie_report(result, 'uz'),
                     'enums': SimpleNamespace(ParseMode=SimpleNamespace(DISABLED='disabled'))}
        function = command_function('_save_validated_cookie', namespace)
        safe_messages = SimpleNamespace(safe_send_message=Mock())
        self.path.write_text(HEADER + 'broken row\n')
        with patch.dict(sys.modules, {'HELPERS.safe_messeger': safe_messages}):
            self.assertFalse(function(None, SimpleNamespace(chat=SimpleNamespace(id=123)), self.path))
        self.assertIn('ustunlar soni noto', safe_messages.safe_send_message.call_args.args[1])

    def test_successful_candidate_persists_normalized_file(self):
        def diagnose(path, user, **kwargs):
            return CookieCheckResult(inspect_cookie_file(path), 'accessible')
        function = command_function('_validate_cookie_candidate',
                                    {'os': os, 'diagnose_youtube_cookie_file': diagnose})
        function(('\ufeff' + HEADER + ROW).encode(), str(self.path), 123)
        self.assertTrue(self.path.read_bytes().startswith(HEADER.encode()))
        self.assertEqual(inspect_cookie_file(self.path).code, 'ready')

    def test_uploaded_cookie_clears_caches_and_triggers_fresh_diagnostic(self):
        namespace = dict(os=os, inspect_cookie_file=inspect_cookie_file,
                         CookieCheckResult=CookieCheckResult,
                         Config=SimpleNamespace(COOKIE_FILE_PATH='TXT/cookie.txt'),
                         create_directory=lambda path: Path(path).mkdir(parents=True, exist_ok=True),
                         clear_youtube_cookie_cache=Mock(), clear_cookie_cache=Mock(),
                         send_to_user=Mock(), send_to_logger=Mock(), checking_cookie_file=Mock(),
                         safe_get_messages=lambda user: SimpleNamespace(
                             COOKIES_FILE_SAVED_MSG='Saved', COOKIES_FILE_SAVED_USER_LOG_MSG='Saved {user_id}'))
        function = command_function('_save_validated_cookie', namespace)
        message = SimpleNamespace(chat=SimpleNamespace(id=123))
        with working_directory(self.directory.name):
            Path('users/123').mkdir(parents=True)
            Path('users/123/cookie.txt').write_text('previous')
            self.assertTrue(function(None, message, self.path))
            self.assertEqual(Path('users/123/cookie.txt').read_text(), HEADER + ROW)
            self.assertEqual(Path('users/123/cookie.txt').stat().st_mode & 0o777, 0o600)
        namespace['clear_youtube_cookie_cache'].assert_called_once_with(123)
        namespace['clear_cookie_cache'].assert_called_once_with(123)
        namespace['checking_cookie_file'].assert_called_once_with(None, message)

    def test_manual_command_passes_target_and_displays_fresh_report(self):
        result = CookieCheckResult(inspect_cookie_file(self.path), 'http_forbidden', 'authenticated')
        safe_messages = SimpleNamespace(safe_send_message=Mock(return_value=SimpleNamespace(id=42)))
        namespace = dict(os=os, Config=SimpleNamespace(COOKIE_FILE_PATH='TXT/cookie.txt'),
                         safe_get_messages=lambda user: SimpleNamespace(COOKIES_YOUTUBE_TEST_START_MSG='Testing'),
                         diagnose_youtube_cookie_file=Mock(return_value=result),
                         _render_cookie_diagnostic=lambda report, user: render_cookie_report(report, 'uz'),
                         safe_edit_message_text=Mock(),
                         enums=SimpleNamespace(ParseMode=SimpleNamespace(DISABLED='disabled')))
        function = command_function('checking_cookie_file', namespace)
        message = SimpleNamespace(chat=SimpleNamespace(id=123), text='/check_cookie ' + URL)
        with patch.dict(sys.modules, {'HELPERS.safe_messeger': safe_messages}):
            function(None, message)
        namespace['diagnose_youtube_cookie_file'].assert_called_once_with('users/123/cookie.txt', '123', URL)
        output = namespace['safe_edit_message_text'].call_args.args[2]
        self.assertIn('403', output)
        self.assertIn('akkauntga kirilganini tasdiqladi', output)
        self.assertNotIn(SECRET, output)

    def test_automatic_preflight_does_not_rotate_on_network_failure(self):
        import types
        limits = SimpleNamespace(LimitsConfig=SimpleNamespace())
        result = CookieCheckResult(CookieFileReport('ready'), 'timeout')
        namespace = dict(os=os, time=__import__('time'), logger=logging.getLogger('test'),
                         _youtube_cookie_cache={}, check_youtube_cookie_retry_limit=lambda user: True,
                         cleanup_expired_tasks=lambda: None, start_cookie_task=Mock(return_value='task'),
                         finish_cookie_task=Mock(), create_directory=lambda path: None,
                         Config=SimpleNamespace(YOUTUBE_COOKIE_TEST_URL=URL, COOKIE_FILE_PATH='TXT/cookie.txt'),
                         LoggerMsg=SimpleNamespace(COOKIES_YOUTUBE_STARTING_ENSURE_LOG_MSG='{user_id}',
                                                   COOKIES_YOUTUBE_CHECKING_EXISTING_LOG_MSG='{user_id}'),
                         diagnose_youtube_cookie_file=Mock(return_value=result),
                         get_youtube_cookie_urls=Mock(side_effect=AssertionError('must not rotate')))
        function = command_function('ensure_working_youtube_cookies', namespace)
        with working_directory(self.directory.name):
            Path('users/123').mkdir(parents=True)
            saved = Path('users/123/cookie.txt')
            saved.write_text('preserve me')
            with patch.dict(sys.modules, {'CONFIG': types.ModuleType('CONFIG'), 'CONFIG.limits': limits}):
                self.assertFalse(function(123))
            self.assertEqual(saved.read_text(), 'preserve me')
        namespace['get_youtube_cookie_urls'].assert_not_called()

    def test_all_translations_cover_every_reason(self):
        self.assertEqual(set(TEXT['uz']), set(TEXT['en']))
        result = CookieCheckResult(CookieFileReport('invalid_row', line=4))
        self.assertIn('4-qatorida', render_cookie_report(result, 'uz'))

    def test_real_yt_dlp_cookie_parser_and_transport_contract(self):
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            self.skipTest('Install yt-dlp for the library integration check')
        page = Mock()
        page.url = 'https://www.youtube.com/'
        page.read.return_value = b'ytcfg.set({"LOGGED_IN":true});'
        page.__enter__ = Mock(return_value=page)
        page.__exit__ = Mock(return_value=False)
        with patch.object(YoutubeDL, 'urlopen', return_value=page), \
             patch.object(YoutubeDL, 'extract_info', return_value=PLAYABLE):
            result = diagnose_cookie_file(self.path, URL)
        self.assertTrue(result.usable)
        self.assertEqual(result.auth, 'authenticated')


if __name__ == '__main__':
    unittest.main()
