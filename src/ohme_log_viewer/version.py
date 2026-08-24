"""Single source of truth for the application version.

The packaging metadata in ``pyproject.toml`` reads ``__version__`` from here
rather than carrying its own copy, so the window title and the installed
distribution can never disagree about which version is running.
"""

from __future__ import annotations

__version__ = "1.7"
