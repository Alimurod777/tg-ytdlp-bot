"""Exercise the download exception message handler without a bot session."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from CONFIG.errors import is_cookie_error
from CONFIG.LANGUAGES.messages_EN import Messages


class DownloadErrorMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'DOWN_AND_UP/down_and_up.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        # Run the real generic exception handler, including its sent-message
        # guard and cookie hint, without importing Telegram or starting downloads.
        block = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.UnaryOp)
            and isinstance(node.test.operand, ast.Name)
            and node.test.operand.id == 'error_message_sent'
            and any(isinstance(child, ast.Name) and child.id == '_dl_err_cat'
                    for child in ast.walk(node))
        )
        cls.handler = compile(ast.Module(body=[block], type_ignores=[]), str(path), 'exec')
        cls.messages = Messages

    def run_handler(self, error, already_sent=False):
        env = dict(
            e=RuntimeError(error), error_message_sent=already_sent,
            user_id=123, message=SimpleNamespace(id=456),
            url='https://www.youtube.com/watch?v=98yduNN5b7Q',
            safe_get_messages=lambda user: self.messages,
            send_to_user=Mock(), safe_send_message=Mock(), logger=Mock(),
            is_cookie_error=is_cookie_error,
            ReplyParameters=lambda **kwargs: kwargs,
            enums=SimpleNamespace(ParseMode=SimpleNamespace(HTML='html')),
        )
        exec(self.handler, env)
        return env

    def test_wrapped_youtube_bot_challenge_shows_sign_in_guidance(self):
        for wording in ("you're", 'you’re', 'you are'):
            error = (
                'Failed to extract video information: ERROR: [youtube] '
                f'98yduNN5b7Q: Sign in to confirm {wording} not a bot. '
                'Use --cookies-from-browser or --cookies for the authentication.'
            )
            with self.subTest(wording=wording):
                env = self.run_handler(error)
                env['send_to_user'].assert_called_once_with(
                    env['message'], self.messages.ALWAYS_ASK_SIGN_IN_REQUIRED_MSG,
                    parse_mode='html',
                )
                self.assertIn('/cookie', env['send_to_user'].call_args.args[1])
                self.assertTrue(env['error_message_sent'])
                env['safe_send_message'].assert_called_once()

    def test_already_reported_challenge_does_not_send_again(self):
        env = self.run_handler("Sign in to confirm you're not a bot", already_sent=True)
        env['send_to_user'].assert_not_called()
        env['safe_send_message'].assert_not_called()

    def test_unclassified_failure_keeps_generic_message(self):
        env = self.run_handler('Unexpected failure')
        env['send_to_user'].assert_called_once_with(
            env['message'], self.messages.UNKNOWN_ERROR_MSG.format(error=env['e']),
        )


if __name__ == '__main__':
    unittest.main()
