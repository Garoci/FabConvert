"""Modern flat QSS themes for the fabconvert GUI.

Two stylesheets — ``LIGHT`` and ``DARK`` — applied to a ``QApplication``.
Embedded as string constants (no data files to ship).  Both use rounded
corners, a soft panel background, an accent color, and the platform's default
sans-serif font.
"""

from __future__ import annotations

# Shared base — colors overridden per theme below via palette name lookups.
_ACCENT = "#3b82f6"      # a calm blue
_ACCENT_HOVER = "#2563eb"

_LIGHT = """
QWidget { background: #f7f8fa; color: #1f2329; font-size: 13px; }
QMainWindow { background: #f7f8fa; }
QFrame#Card, QGroupBox {
    background: #ffffff; border: 1px solid #e3e6ea; border-radius: 10px;
}
QGroupBox { margin-top: 14px; padding: 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #4b5563; }
QLabel#Title { font-size: 16px; font-weight: 700; color: #111827; }
QLabel#Subtitle { color: #6b7280; font-size: 12px; }
QLabel#DropHint { color: #9aa3af; font-size: 13px; }
QPushButton {
    background: #ffffff; border: 1px solid #d0d4da; border-radius: 8px;
    padding: 6px 14px; color: #1f2329;
}
QPushButton:hover { border-color: #aab2bd; }
QPushButton:pressed { background: #eef0f3; }
QPushButton#Primary {
    background: """ + _ACCENT + """; color: #ffffff; border: 1px solid """ + _ACCENT + """;
    font-weight: 600; padding: 8px 18px;
}
QPushButton#Primary:hover { background: """ + _ACCENT_HOVER + """; border-color: """ + _ACCENT_HOVER + """; }
QComboBox, QLineEdit, QSpinBox, QListWidget {
    background: #ffffff; border: 1px solid #d0d4da; border-radius: 8px;
    padding: 5px 8px; selection-background-color: """ + _ACCENT + """; selection-color: #ffffff;
}
QComboBox:hover, QLineEdit:focus { border-color: """ + _ACCENT + """; }
QComboBox QAbstractItemView { background: #ffffff; border: 1px solid #d0d4da; selection-background-color: """ + _ACCENT + """; }
 QListWidget { border: 1px solid #d0d4da; }
QProgressBar { background: #ffffff; border: 1px solid #d0d4da; border-radius: 8px; text-align: center; height: 18px; }
QProgressBar::chunk { background-color: """ + _ACCENT + """; border-radius: 7px; }
QStatusBar { background: #eef0f3; color: #4b5563; border-top: 1px solid #e3e6ea; }
QTabWidget::pane { border: 1px solid #e3e6ea; border-radius: 8px; }
QTabBar::tab { background: #eef0f3; padding: 6px 14px; border-radius: 8px; margin-right: 2px; }
QTabBar::tab:selected { background: #ffffff; border: 1px solid #e3e6ea; }
QScrollBar:vertical { background: #eef0f3; width: 10px; border: none; }
QScrollBar::handle:vertical { background: #c2c7cf; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #aab2bd; }
"""

_DARK = """
QWidget { background: #1e1f24; color: #e5e7eb; font-size: 13px; }
QMainWindow { background: #1e1f24; }
QFrame#Card, QGroupBox {
    background: #26272e; border: 1px solid #33343d; border-radius: 10px;
}
QGroupBox { margin-top: 14px; padding: 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #9ca3af; }
QLabel#Title { font-size: 16px; font-weight: 700; color: #f9fafb; }
QLabel#Subtitle { color: #9ca3af; font-size: 12px; }
QLabel#DropHint { color: #6b7280; font-size: 13px; }
QPushButton {
    background: #2c2d35; border: 1px solid #3f414b; border-radius: 8px;
    padding: 6px 14px; color: #e5e7eb;
}
QPushButton:hover { border-color: #5a5d68; }
QPushButton:pressed { background: #232429; }
QPushButton#Primary {
    background: """ + _ACCENT + """; color: #ffffff; border: 1px solid """ + _ACCENT + """;
    font-weight: 600; padding: 8px 18px;
}
QPushButton#Primary:hover { background: """ + _ACCENT_HOVER + """; border-color: """ + _ACCENT_HOVER + """; }
QComboBox, QLineEdit, QSpinBox, QListWidget {
    background: #2c2d35; border: 1px solid #3f414b; border-radius: 8px;
    padding: 5px 8px; color: #e5e7eb; selection-background-color: """ + _ACCENT + """; selection-color: #ffffff;
}
QComboBox:hover, QLineEdit:focus { border-color: """ + _ACCENT + """; }
QComboBox QAbstractItemView { background: #2c2d35; border: 1px solid #3f414b; selection-background-color: """ + _ACCENT + """; color: #e5e7eb; }
QProgressBar { background: #2c2d35; border: 1px solid #3f414b; border-radius: 8px; text-align: center; height: 18px; color: #e5e7eb; }
QProgressBar::chunk { background-color: """ + _ACCENT + """; border-radius: 7px; }
QStatusBar { background: #17181c; color: #9ca3af; border-top: 1px solid #33343d; }
QTabWidget::pane { border: 1px solid #33343d; border-radius: 8px; }
QTabBar::tab { background: #232429; padding: 6px 14px; border-radius: 8px; margin-right: 2px; color: #9ca3af; }
QTabBar::tab:selected { background: #2c2d35; border: 1px solid #3f414b; color: #e5e7eb; }
QScrollBar:vertical { background: #1e1f24; width: 10px; border: none; }
QScrollBar::handle:vertical { background: #3f414b; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #5a5d68; }
"""

LIGHT = _LIGHT
DARK = _DARK
