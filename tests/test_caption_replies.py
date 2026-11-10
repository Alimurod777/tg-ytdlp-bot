"""Ordinary group replies must never resend videos or copy their text to logs."""
import ast
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class CaptionReplyTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'HELPERS/caption.py'
        tree = ast.parse(path.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'caption_editor')
        self.messages = Mock(return_value=SimpleNamespace(CAPTION_INFO_OF_VIDEO_MSG='{caption}'))
        self.log = Mock()
        namespace = dict(safe_get_messages=self.messages, send_to_logger=self.log)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        self.editor = namespace['caption_editor']
        self.app = Mock()

    def message(self, chat_id):
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id, first_name='User'),
                               text='Shuni Lichkaga Tashlab berolmaysizmi?',
                               reply_to_message=SimpleNamespace(video=SimpleNamespace(file_id='video')))

    def test_group_reply_does_not_resend_video_or_echo_text(self):
        self.editor(self.app, self.message(-123))
        self.app.send_video.assert_not_called()
        self.messages.assert_not_called()
        self.log.assert_not_called()

    def test_private_caption_edit_still_works(self):
        logger = ModuleType('HELPERS.logger')
        logger.get_log_channel = lambda _: -456
        message = self.message(123)
        with patch.dict('sys.modules', {'HELPERS.logger': logger}):
            self.editor(self.app, message)
        self.app.send_video.assert_any_call(123, 'video', caption=message.text)
        self.log.assert_called_once()


if __name__ == '__main__':
    unittest.main()
