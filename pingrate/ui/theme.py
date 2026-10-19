BG, PANEL, PANEL2, BORDER = "#0e1218", "#161c25", "#1d2530", "#2a3441"
TEXT, MUTED = "#e6edf5", "#8b9bb0"
ACCENT, GOOD, WARN, BAD = "#4cc9f0", "#3ddc97", "#ffb454", "#ff5c70"
LEVEL = {"ok": GOOD, "warn": WARN, "bad": BAD, "wait": MUTED}

QSS = f"""
* {{ font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QTabWidget::pane {{ border: 0; }}
QTabBar::tab {{ background: {PANEL}; padding: 9px 18px; margin-right: 4px; border-top-left-radius: 8px;
  border-top-right-radius: 8px; color: {MUTED}; }}
QTabBar::tab:selected {{ background: {PANEL2}; color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{ background: {PANEL}; border: 1px solid {BORDER};
  border-radius: 8px; padding: 7px 10px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QPushButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 16px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#primary {{ background: {ACCENT}; color: #04141b; border: 0; font-weight: 600; }}
QPushButton#primary:hover {{ background: #7bdcf7; }}
QPushButton#diag {{ background: #2b1d3a; border: 1px solid #6c4aa0; font-weight: 600; }}
QPushButton#diag:hover {{ border-color: #b18cff; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#cardlabel {{ color: {MUTED}; font-size: 11px; }}
QLabel#cardvalue {{ font-size: 20px; font-weight: 600; }}
QTableWidget, QTreeWidget {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px;
  gridline-color: {BORDER}; alternate-background-color: {PANEL2}; outline: 0; }}
QHeaderView::section {{ background: {PANEL2}; color: {MUTED}; border: 0; padding: 7px; font-weight: 600; }}
QTableWidget::item:selected, QTreeWidget::item:selected {{ background: #243246; }}
QProgressBar {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; height: 10px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; }}
QScrollBar:vertical {{ background: {BG}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QCheckBox {{ spacing: 8px; }}
"""
