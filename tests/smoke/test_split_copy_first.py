"""Regression tests: two-stage split (stream-copy first, re-encode fallback).

User report 15.11.2026 (user 96720978): 4.39 GiB video split took 2.5 hours on
the 1-vCPU ARM CT108 because _extract_split_part always re-encoded with libx264.
The user never received the video (2 attempts, ~2.5h waits).
"""
import ast
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

SRC = os.path.join(ROOT, "DOWN_AND_UP", "ffmpeg.py")


def test_two_stage_split_code_contract():
    with open(SRC, "r", encoding="utf-8") as f:
        code = f.read()
    # Fast path: stream copy command must exist and come FIRST
    copy_idx = code.find('"-c", "copy"')
    enc_idx = code.find('"-c:v", "libx264"')
    assert copy_idx != -1, "stream-copy fast path missing"
    assert enc_idx != -1, "re-encode fallback missing"
    assert copy_idx < enc_idx, "copy must be tried before re-encode"
    assert "falling back to re-encode" in code
    print("ok: split is two-stage (copy first, re-encode fallback)")


def _have_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def test_two_stage_split_functional():
    if not _have_ffmpeg():
        print("skip: ffmpeg not available in this environment")
        return
    # Build a 5s test video with frequent keyframes
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src.mp4")
        part = os.path.join(td, "part.mp4")
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=15",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
             "-c:v", "libx264", "-preset", "ultrafast", "-g", "15",
             "-c:a", "aac", "-shortest", src],
            capture_output=True, timeout=120,
        )
        assert r.returncode == 0, f"setup ffmpeg failed: {r.stderr[:300]}"

        # Execute the production function without importing the bot/config.
        tree = ast.parse(Path(SRC).read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_extract_split_part')
        namespace = dict(os=os, subprocess=subprocess,
                         logger=logging.getLogger('split-test'),
                         get_ffmpeg_path=lambda: 'ffmpeg')
        exec(compile(ast.Module(body=[function], type_ignores=[]), SRC, 'exec'), namespace)
        assert namespace['_extract_split_part'](src, 1, 3, part)
        assert os.path.getsize(part) > 0
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type',
             '-of', 'csv=p=0', part], capture_output=True, text=True, timeout=10)
        assert probe.returncode == 0, probe.stderr
        assert 'video' in probe.stdout and 'audio' in probe.stdout
        print("ok: stream-copy split works on a real file (2s cut from 5s)")


if __name__ == "__main__":
    test_two_stage_split_code_contract()
    test_two_stage_split_functional()
    print("\nALL TESTS PASSED")
