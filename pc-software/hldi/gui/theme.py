"""Application stylesheet — a distinctive dark "industrial console" theme."""

# Palette: near-black slate background, warm copper accent (echoing PCB
# copper), red for the laser/stop affordances, muted steel for chrome.
STYLE = """
* {
    font-family: "DejaVu Sans", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #c7d0d9;
}
QMainWindow, QDialog { background: #0a0e14; }

QLabel#H1 {
    font-size: 15px;
    font-weight: 600;
    color: #e9b949;
    letter-spacing: 1px;
}
QLabel#Mono {
    font-family: "DejaVu Sans Mono", monospace;
    font-size: 18px;
    color: #6fd3c0;
}
QLabel#Caption { color: #6b7785; font-size: 11px; letter-spacing: 1px; }

QGroupBox {
    border: 1px solid #1b2430;
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    background: #0d131c;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #6b7785;
    font-size: 11px;
    letter-spacing: 2px;
}

QPushButton {
    background: #161d27;
    border: 1px solid #232e3b;
    border-radius: 6px;
    padding: 7px 14px;
    color: #c7d0d9;
}
QPushButton:hover { border-color: #e9b949; color: #e9b949; }
QPushButton:pressed { background: #0d131c; }
QPushButton:disabled { color: #3d4651; border-color: #161d27; }

QPushButton#Accent {
    background: #e9b949; color: #0a0e14; border: none; font-weight: 600;
}
QPushButton#Accent:hover { background: #f0c75f; }
QPushButton#Danger {
    background: #2a1416; color: #ff5c57; border: 1px solid #5c2326;
    font-weight: 600;
}
QPushButton#Danger:hover { background: #3a1a1d; }

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #0a0e14;
    border: 1px solid #232e3b;
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: #e9b949;
    selection-color: #0a0e14;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #6fd3c0;
}
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: #0d131c; border: 1px solid #232e3b;
    selection-background-color: #161d27;
}

QProgressBar {
    background: #0a0e14;
    border: 1px solid #232e3b;
    border-radius: 5px;
    height: 16px;
    text-align: center;
    color: #c7d0d9;
}
QProgressBar::chunk {
    background: #6fd3c0; border-radius: 4px;
}

QPlainTextEdit, QTextEdit {
    background: #060a0f;
    border: 1px solid #1b2430;
    border-radius: 6px;
    font-family: "DejaVu Sans Mono", monospace;
    font-size: 12px;
    color: #8aa0b4;
}

QStatusBar { background: #060a0f; color: #6b7785; }
QStatusBar::item { border: none; }

QToolTip {
    background: #161d27; color: #c7d0d9;
    border: 1px solid #e9b949; padding: 4px;
}
"""


def status_dot(connected: bool) -> str:
    return "#6fd3c0" if connected else "#ff5c57"
