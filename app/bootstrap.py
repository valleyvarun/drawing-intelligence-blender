"""Dedicated entry point; launch through run.ps1 to isolate all Blender user data."""

import sys
from importlib import import_module
from pathlib import Path
from typing import Any

application: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(application))

application_module: Any = import_module("drawing_app")
application_module.register()
