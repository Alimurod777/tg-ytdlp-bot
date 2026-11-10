"""Keep legacy config imports working on an environment-only checkout."""

import importlib
from pathlib import Path
import sys

from CONFIG.env_config import configure

if __name__ + ".config" in sys.modules:
    # Respect an already initialized config module (also used by isolated tests).
    _module = sys.modules[__name__ + ".config"]
elif Path(__file__).with_name("config.py").is_file():
    _module = importlib.import_module("CONFIG.config")
    configure(_module.Config, fresh=False)
else:
    _module = importlib.import_module("CONFIG._config")
    configure(_module.Config, fresh=True)
    sys.modules[__name__ + ".config"] = _module

config = _module

from CONFIG.overrides import apply as _apply_overrides
_apply_overrides(_module.Config)
