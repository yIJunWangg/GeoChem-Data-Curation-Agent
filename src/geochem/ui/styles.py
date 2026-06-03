"""Shared Qt stylesheet for the GeoChem desktop app."""

APP_QSS = """
* {
    font-family: "Inter", "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: #172033;
}

QMainWindow, QWidget#central {
    background: #f7f9fc;
}

QFrame#sidebar {
    background: #ffffff;
    border-right: 1px solid #dfe5ee;
}

QFrame#topbar {
    background: #ffffff;
    border-bottom: 1px solid #dfe5ee;
}

QLabel#brandTitle {
    font-size: 24px;
    font-weight: 700;
    color: #0f3d75;
}

QLabel#brandSub {
    color: #53637a;
}

QLabel#pageTitle {
    font-size: 26px;
    font-weight: 750;
    color: #111827;
}

QLabel#pageSubtitle {
    color: #53637a;
}

QLabel#sectionTitle {
    font-size: 15px;
    font-weight: 700;
}

QLabel#taskStatus {
    color: #1468d8;
    background: #eaf3ff;
    border: 1px solid #b8d7ff;
    border-radius: 7px;
    padding: 5px 10px;
    font-weight: 650;
}

QPushButton {
    min-height: 32px;
    padding: 7px 13px;
    border-radius: 7px;
    border: 1px solid #d7deea;
    background: #ffffff;
}

QPushButton:hover {
    background: #f4f7fb;
    border-color: #b8c7dd;
}

QPushButton#primaryButton {
    background: #1468d8;
    border: 1px solid #1468d8;
    color: white;
    font-weight: 650;
}

QPushButton#primaryButton:hover {
    background: #0f5fc8;
}

QPushButton#dangerButton {
    color: #d92d20;
    border-color: #f5b5ad;
    background: #fff8f7;
}

QPushButton#navButton {
    text-align: left;
    border: 0;
    border-radius: 7px;
    padding: 10px 14px;
    background: transparent;
    color: #354258;
    font-size: 14px;
}

QPushButton#navButton:checked {
    background: #eaf3ff;
    color: #1468d8;
    border: 1px solid #b8d7ff;
    font-weight: 650;
}

QFrame.card, QFrame[class="card"] {
    background: #ffffff;
    border: 1px solid #dfe5ee;
    border-radius: 8px;
}

QLabel.metricValue, QLabel[class="metricValue"] {
    font-size: 28px;
    font-weight: 750;
    color: #172033;
}

QLabel.metricLabel, QLabel[class="metricLabel"] {
    color: #53637a;
}

QLabel#warningText, QLabel[class="warningText"] {
    color: #b54708;
    background: #fffaeb;
    border: 1px solid #fedf89;
    border-radius: 7px;
    padding: 8px;
}

QLabel[class="stepBadge"] {
    background: #f8fafd;
    border: 1px solid #dfe5ee;
    border-radius: 8px;
    padding: 10px;
    min-width: 86px;
    color: #344054;
}

QTabWidget::pane {
    border: 1px solid #dfe5ee;
    border-radius: 8px;
    background: #ffffff;
}

QTabBar::tab {
    padding: 9px 18px;
    color: #53637a;
}

QTabBar::tab:selected {
    color: #1468d8;
    font-weight: 650;
    border-bottom: 2px solid #1468d8;
}

QLineEdit, QComboBox {
    min-height: 32px;
    padding: 5px 10px;
    border: 1px solid #d7deea;
    border-radius: 7px;
    background: #ffffff;
}

QTableView {
    background: #ffffff;
    border: 1px solid #dfe5ee;
    border-radius: 8px;
    gridline-color: #e6ebf3;
    selection-background-color: #e7f1ff;
    selection-color: #172033;
    alternate-background-color: #fbfcfe;
}

QHeaderView::section {
    background: #f8fafd;
    border: 0;
    border-bottom: 1px solid #dfe5ee;
    padding: 8px;
    font-weight: 650;
    color: #344054;
}

QTableView::item {
    padding: 6px;
}

QTreeWidget {
    background: #ffffff;
    border: 1px solid #dfe5ee;
    border-radius: 8px;
}

QTextEdit, QPlainTextEdit {
    background: #ffffff;
    border: 1px solid #dfe5ee;
    border-radius: 8px;
    padding: 8px;
}

QPlainTextEdit#importConsole {
    background: #0f172a;
    color: #dbeafe;
    border: 1px solid #24324a;
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 12px;
}

QProgressBar {
    border: 0;
    background: #e9eef6;
    border-radius: 5px;
    height: 10px;
}

QProgressBar::chunk {
    background: #2f7de1;
    border-radius: 5px;
}
"""
