"""Application identity shared with the launcher (launcher/AppIdentity.cs) and the setup wizard."""

from __future__ import annotations

from pathlib import Path

from .state import ROOT

APP_NAME: str = "Drawing Intelligence"
# Taskbar identity; the engine's own ID is replaced with it, hence at most 11 characters.
APP_USER_MODEL_ID: str = "DrawIntel.1"
EXECUTABLE: str = "Drawing Intelligence.exe"
TEMPLATE: str = "drawing_intelligence"


def launcher_path() -> Path:
    """The application's own executable beside app/, if it has been built or installed."""
    return ROOT / EXECUTABLE


def engine_path() -> Path:
    return ROOT / "engine" / EXECUTABLE
