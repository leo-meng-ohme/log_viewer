"""Entry point for the Ohme Log Viewer."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .version import __version__


def main() -> int:
    """Run the application. Returns the Qt exit code."""
    app = QApplication(sys.argv)
    app.setApplicationName("Ohme Log Viewer")
    app.setApplicationVersion(__version__)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
