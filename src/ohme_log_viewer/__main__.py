"""Entry point for the Ohme Log Viewer."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def main() -> int:
    """Run the application. Returns the Qt exit code."""
    app = QApplication(sys.argv)
    app.setApplicationName("Ohme Log Viewer")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
