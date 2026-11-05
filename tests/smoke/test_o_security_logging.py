"""Regression guards for credential logging and the list-download address filter.

Run: python tests/smoke/test_o_security_logging.py   (offline, no bot config needed)
"""
import ast
import sys
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# names whose full value can carry proxy credentials
SECRET_NAMES = {"proxy_url", "proxy_config"}
SECRET_ATTRS = {"PROXY_PASSWORD", "PROXY_2_PASSWORD", "PROXY_USER", "PROXY_2_USER", "BOT_TOKEN", "API_HASH"}
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


def _is_log_call(node):
    f = node.func
    return (isinstance(f, ast.Attribute) and f.attr in LOG_METHODS
            and isinstance(f.value, ast.Name) and f.value.id in {"logger", "logging", "log"}) or \
           (isinstance(f, ast.Name) and f.id == "print")


def _leaks(expr):
    """Names/attributes that reach the log line as a whole value."""
    found = []
    for node in ast.walk(expr):
        if isinstance(node, ast.FormattedValue):
            value = node.value
            if isinstance(value, ast.Name) and value.id in SECRET_NAMES:
                found.append(value.id)
            if isinstance(value, ast.Attribute) and value.attr in SECRET_ATTRS:
                found.append(value.attr)
        if isinstance(node, ast.keyword) and isinstance(node.value, ast.Name) and node.value.id in SECRET_NAMES:
            found.append(node.value.id)
    return found


class CredentialLogging(unittest.TestCase):
    def test_no_log_call_prints_proxy_credentials(self):
        offenders = []
        for name in subprocess.check_output(
                ['git', 'ls-files', '--', '*.py'], cwd=ROOT, text=True).splitlines():
            path = ROOT / name
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(("venv/", "tests/", ".git/")):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _is_log_call(node):
                    for name in _leaks(node):
                        offenders.append(f"{rel}:{node.lineno} {name}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_detector_catches_the_old_patterns(self):
        old = 'logger.info(f"Force using proxy for audio download: {proxy_url}")'
        self.assertEqual(_leaks(ast.parse(old).body[0].value), ["proxy_url"])
        old = 'logger.info(MSG.format(proxy_url=proxy_url))'
        self.assertEqual(_leaks(ast.parse(old).body[0].value), ["proxy_url"])
        safe = 'logger.info(f"proxy: {proxy_config.get(\'ip\')}:{proxy_config.get(\'port\')}")'
        self.assertEqual(_leaks(ast.parse(safe).body[0].value), [])


if __name__ == "__main__":
    # The shared smoke runner passes the repository root to standalone scripts.
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_dir():
        del sys.argv[1]
    unittest.main(verbosity=2)
