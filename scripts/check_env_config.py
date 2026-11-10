"""Check startup from an uploaded .env using isolated checkout directories."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = '''BOT_NAME=example_bot
API_ID=12345
API_HASH=example_hash
BOT_TOKEN=example_token
ADMIN=123,456
LOGS_ID=-100123
'''


class EnvConfigTests(unittest.TestCase):
    def run_config(self, content=BASE, code="", overrides=None, legacy=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "CONFIG", root / "CONFIG", ignore=shutil.ignore_patterns(
                "config.py", "__pycache__", ".*",
            ))
            if content is not None:
                (root / ".env").write_text(content)
            if legacy is not None:
                (root / "CONFIG/config.py").write_text(legacy)
            env = {key: value for key, value in os.environ.items()
                   if not key.isupper() or key in {
                       "PATH", "PYTHONPATH", "HOME", "SYSTEMROOT", "LANG", "LC_ALL",
                   }}
            env.update(overrides or {})
            return subprocess.run(
                [sys.executable, "-c", "from CONFIG.config import Config\n" + code],
                cwd=root, env=env, capture_output=True, text=True,
            )

    def test_uploaded_env_and_derived_defaults(self):
        result = self.run_config(code='''
assert Config.API_ID == 12345
assert Config.ADMIN == [123,456]
assert Config.LOGS_VIDEO_ID == Config.LOGS_ID == -100123
assert Config.LOG_EXCEPTION == -100123
assert Config.BOT_DB_PATH == 'bot/example_bot/'
assert Config.MINIAPP_URL == 'https://t.me/example_bot/?startapp'
assert Config.SUBSCRIBE_CHANNEL == 0
assert Config.ADMIN_GROUP == []
assert Config.PROXY_IP == ''
assert Config.DB_ENGINE == 'json'
assert Config.BROWSER_SESSION_ENABLED is False
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_process_environment_overrides_file(self):
        result = self.run_config(overrides={"API_ID": "987"}, code="assert Config.API_ID == 987")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_typed_options_and_literal_password(self):
        result = self.run_config(BASE + '''USE_FIREBASE=false
ADMIN=[123,456]
ALLOWED_GROUP=[-100999]
LOGS_VIDEO_ID=-100888
DASHBOARD_PASSWORD='secret#with$dollar'
''', code='''
assert Config.USE_FIREBASE is False
assert Config.ADMIN == [123,456]
assert Config.ALLOWED_GROUP == [-100999]
assert Config.LOGS_VIDEO_ID == -100888
assert Config.DASHBOARD_PASSWORD == 'secret#with$dollar'
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_credentials_explained(self):
        result = self.run_config("")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing required .env settings:", result.stderr)
        self.assertIn("BOT_TOKEN", result.stderr)

    def test_invalid_values_do_not_leak(self):
        for key in ("API_ID", "ADMIN", "USE_FIREBASE"):
            with self.subTest(key=key):
                result = self.run_config(BASE + f"{key}=private_invalid_value\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Invalid configuration value for {key}", result.stderr)
                self.assertNotIn("private_invalid_value", result.stderr)

    def test_dashboard_updates_persist_in_overrides(self):
        code = """
from pathlib import Path
from CONFIG import overrides
original_env = Path('.env').read_bytes()
accepted, errors = overrides.save_values({
    'PROXY_PASSWORD': 'secret#with$dollar',
    'ALLOWED_GROUP': [-100111], 'LOGS_ID': '-100222',
})
assert not errors
assert len(accepted) == 3
rejected, errors = overrides.save_values({'UNSUPPORTED_SETTING': 'value'})
assert not rejected and 'UNSUPPORTED_SETTING' in errors
# A fresh process must pick up the saved settings, while preserving .env.
import subprocess, sys
child = subprocess.run([sys.executable, '-c',
    "from CONFIG.config import Config; "
    "assert Config.LOGS_ID == -100222; "
    "assert Config.ALLOWED_GROUP == [-100111]; "
    "assert Config.PROXY_PASSWORD == 'secret#with$dollar'"],
    capture_output=True, text=True)
assert child.returncode == 0, child.stderr
assert Path('.env').read_bytes() == original_env
"""
        result = self.run_config(code=code)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_env_overrides_complete_python_config(self):
        result = self.run_config(
            BASE + "LOGS_VIDEO_ID=-100999\n",
            legacy=(ROOT / 'CONFIG/_config.py').read_text(),
            code="""
assert Config.API_ID == 12345
assert Config.BOT_NAME == 'example_bot'
assert Config.BOT_NAME_FOR_USERS == 'example_bot'
assert Config.BOT_DB_PATH == 'bot/example_bot/'
assert Config.LOGS_ID == -100123
assert Config.LOGS_VIDEO_ID == -100999
assert Config.LOGS_IMG_ID == -100123
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_python_config_without_env(self):
        result = self.run_config(None, legacy='''class Config:
    API_ID = 123
    BOT_NAME = "legacy_bot"
    BOT_TOKEN = "legacy_token"
    ADMIN = [987]
''', code='''
assert Config.API_ID == 123
assert Config.ADMIN == [987]
assert Config.BOT_NAME == 'legacy_bot'
assert Config.BOT_TOKEN == 'legacy_token'
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_python_config_remains_supported(self):
        result = self.run_config("API_ID=987\n", legacy='''class Config:
    API_ID = 123
    BOT_NAME = "legacy_bot"
    BOT_TOKEN = "legacy_token"
''', code='''
assert Config.API_ID == 987
assert Config.BOT_NAME == 'legacy_bot'
assert Config.BOT_TOKEN == 'legacy_token'
''')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
