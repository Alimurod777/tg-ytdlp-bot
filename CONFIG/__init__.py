"""Keep legacy config imports working on an environment-only checkout."""

import importlib
from pathlib import Path
import sys

from CONFIG.env_config import configure

if Path(__file__).with_name("config.py").is_file():
    _module = importlib.import_module("CONFIG.config")
    configure(_module.Config, fresh=False)
else:
    _module = importlib.import_module("CONFIG._config")
    configure(_module.Config, fresh=True)
    sys.modules[__name__ + ".config"] = _module

config = _module
