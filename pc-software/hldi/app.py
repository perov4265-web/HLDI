"""HLDI application entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .gui.main_window import MainWindow
from .gui.theme import STYLE


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("HLDI")
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
