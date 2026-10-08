"""Local rebate settlement desktop UI. Run: .venv/bin/python app.py"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QLockFile, QModelIndex, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QTabWidget, QTableView, QVBoxLayout, QWidget,
)

from exporter import export_result, validate_export_target
from rebate_engine import DataError, Dataset, Filters, QueryResult, STATUS_LABELS, format_money, parse_ids
from worker import ImportWorker

MISSING_COLOR = "#c0392b"

STYLE = """
QMainWindow, QWidget#shell { background: #f3f6f6; color: #142e32; }
QWidget { font-family: 'PingFang SC', 'Microsoft YaHei'; font-size: 12px; color: #142e32; }
QLabel { background: transparent; }
QLabel#kicker { color: #21877a; font-size: 11px; font-weight: 700; letter-spacing: 2px; }
QLabel#title { font-size: 23px; font-weight: 700; }
QLabel#muted { color: #708387; font-size: 12px; }
QLabel#section { font-size: 14px; font-weight: 650; }
QLabel#badge { background: #e1f0eb; color: #217864; border-radius: 12px; padding: 7px 13px; }
QFrame#panel { background: #ffffff; border: 1px solid #e1e8e7; border-radius: 15px; }
QFrame#drop { background: #f6fbf9; border: 1px dashed #8fbdb1; border-radius: 12px; }
QFrame#drop[dragging="true"] { background: #d9f1e8; border: 2px solid #249b7a; }
QFrame#hero { background: #133c3c; border: none; border-radius: 16px; }
QFrame#hero QLabel { color: #eefcf8; }
QFrame#hero QLabel#heroCaption { color: #a9cbc3; font-size: 12px; }
QLabel#amount { font-family: 'Menlo', 'Consolas', monospace; font-size: 34px; font-weight: 650; }
QLabel#metric { font-size: 18px; font-weight: 650; }
QLabel#audit { color: #345f54; background: #edf7f1; border-radius: 8px; padding: 7px; }
QFrame#missingPanel { background: #fff3f1; border: 1px solid #f0cbc6; border-radius: 8px; }
QLabel#missingTitle { color: #c0392b; font-weight: 650; }
QPlainTextEdit#missingIds { color: #c0392b; background: transparent; border: none; padding: 0; }
QLabel#notice { color: #735615; background: #fff6df; border-radius: 8px; padding: 9px; font-size: 12px; }
QPushButton { background: #ffffff; border: 1px solid #d5dfdc; border-radius: 8px; padding: 7px 10px; font-weight: 550; }
QPushButton:hover { background: #eef6f3; border-color: #88b3a8; }
QPushButton:pressed { background: #deeee7; }
QPushButton:disabled { color: #a9b7b3; background: #f1f4f3; border-color: #e4eae7; }
QPushButton#primary { background: #237e64; color: white; border: 1px solid #237e64; }
QPushButton#primary:hover { background: #1b6b54; }
QPushButton#primary:disabled { background: #bdd7cd; border-color: #bdd7cd; color: #f4faf7; }
QPushButton#copy { background: #204e4c; color: #d7ede6; border-color: #3c6661; padding: 7px 12px; }
QPushButton#copy:disabled { color: #628c82; }
QPlainTextEdit, QComboBox, QLineEdit { background: #fbfcfc; border: 1px solid #d9e2df; border-radius: 7px; padding: 6px; selection-background-color: #298b6e; }
QPlainTextEdit:focus, QComboBox:focus, QLineEdit:focus { border-color: #27866a; }
QComboBox::drop-down { border: none; width: 23px; }
QTabWidget::pane { border: none; background: white; }
QTabBar::tab { background: white; color: #7d908a; padding: 7px 11px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #217e64; border-bottom: 2px solid #217e64; font-weight: 650; }
QTableView { background: white; border: none; selection-background-color: #e1f1e9; selection-color: #173a2d; gridline-color: #edf2ef; }
QHeaderView::section { background: #f6f9f7; color: #607b70; border: none; padding: 7px; font-size: 11px; }
QTableView::item { padding: 5px; border-bottom: 1px solid #f0f4f1; }
QProgressBar { background: #e4ece8; border: none; border-radius: 4px; height: 7px; }
QProgressBar::chunk { background: #309777; border-radius: 4px; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: #f3f6f4; width: 8px; }
QScrollBar::handle:vertical { background: #c9d8d0; border-radius: 4px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QStatusBar { color: #657d74; font-size: 12px; background: #edf3ef; }
"""


def label(text="", name=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def parse_filter_date(value: str, name: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise DataError(f"{name}请输入 YYYY-MM-DD，例如 2026-08-18")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f"{name}不是有效日期") from exc


class DateInput(QLineEdit):
    """Insert separators while typing digits; keep invalid pasted input visible for validation."""

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("YYYY-MM-DD")
        self.textEdited.connect(self._format_digits)

    def _format_digits(self, text):
        if not re.fullmatch(r"[0-9-]*", text):
            return
        digits = text.replace("-", "")
        if len(digits) > 8:
            return
        formatted = digits[:4]
        if len(digits) > 4:
            formatted += "-" + digits[4:6]
        if len(digits) > 6:
            formatted += "-" + digits[6:]
        if formatted != text:
            count_before_cursor = sum(char.isdigit() for char in text[:self.cursorPosition()])
            self.setText(formatted)
            offset = (count_before_cursor >= 4 and len(digits) > 4) + (count_before_cursor >= 6 and len(digits) > 6)
            self.setCursorPosition(count_before_cursor + offset)


def panel(name="panel", margins=20):
    widget = QFrame()
    widget.setObjectName(name)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(12)
    return widget, layout


class DropArea(QFrame):
    fileDropped = Signal(object)
    rejected = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(6)
        title = label("拖入一份或多份 CSV", "section")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = label("拆分文件一起选 · 合并统计", "muted")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.choose_button = QPushButton("选择文件…")
        layout.addWidget(title)
        layout.addWidget(self.choose_button)

    def _highlight(self, active):
        self.setProperty("dragging", active)
        self.style().unpolish(self)
        self.style().polish(self)

    @staticmethod
    def paths(event):
        urls = event.mimeData().urls()
        paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
        if paths and len(paths) == len(urls) and all(
                Path(path).suffix.lower() == ".csv" and Path(path).is_file() for path in paths):
            return paths
        return None

    def dragEnterEvent(self, event):
        if self.isEnabled() and self.paths(event):
            event.acceptProposedAction()
            self._highlight(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._highlight(False)
        event.accept()

    def dropEvent(self, event):
        self._highlight(False)
        path = self.paths(event)
        if path and self.isEnabled():
            event.acceptProposedAction()
            self.fileDropped.emit(path)
        else:
            event.ignore()
            self.rejected.emit("请选择一个或多个本地 CSV 文件，ZIP 请先解压")


class SummaryModel(QAbstractTableModel):
    def __init__(self, headings, parent=None):
        super().__init__(parent)
        self.headings = headings
        self.rows = []
        self.missing_ids = set()
        self.sort_column = 0
        self.sort_order = Qt.SortOrder.AscendingOrder

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headings)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        value = self.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.ForegroundRole and self.rows[index.row()][0] in self.missing_ids:
            return QColor(MISSING_COLOR)
        if role == Qt.ItemDataRole.DisplayRole:
            if isinstance(value, Decimal):
                return format_money(value, grouping=True)
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, int):
                return f"{value:,}"
            return str(value)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int((Qt.AlignmentFlag.AlignRight if isinstance(value, (Decimal, int)) else
                        Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and isinstance(value, Decimal):
            return QColor("#24775c" if value >= 0 else "#b04d42")
        if role == Qt.ItemDataRole.ForegroundRole and isinstance(value, str):
            if value in (STATUS_LABELS["missing"], "否"):
                return QColor("#b04d42")
            if value in (STATUS_LABELS["inactive"], STATUS_LABELS["zero"]):
                return QColor("#9a741f")
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headings[section]
        return None

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = sorted(rows, key=lambda row: row[self.sort_column],
                           reverse=self.sort_order == Qt.SortOrder.DescendingOrder)
        self.endResetModel()

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self.sort_column, self.sort_order = column, order
        self.set_rows(self.rows)


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings("local.rebate.settlement", "rebate-settlement")
        self.dataset: Dataset | None = None
        self.result: QueryResult | None = None
        self.pending_filters = False
        self.applied_scope = ""
        self.worker: ImportWorker | None = None
        self.cancel_requested = False
        self.close_requested = False
        self.last_error = ""
        self.setWindowTitle("返佣结算 · 本地 CSV 统计")
        self.resize(1080, 740)
        self.setMinimumSize(960, 680)
        shell = QWidget()
        shell.setObjectName("shell")
        self.setCentralWidget(shell)
        root = QVBoxLayout(shell)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(5)
        titles.addWidget(label("REBATE  /  LOCAL", "kicker"))
        titles.addWidget(label("返佣结算", "title"))
        header.addLayout(titles)
        header.addStretch()
        privacy_badge = label("本地处理 · 数据不上传", "badge")
        privacy_badge.setFixedHeight(34)
        header.addWidget(privacy_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)
        sidebar, side = panel(margins=14)
        side.setSpacing(8)
        sidebar.setFixedWidth(286)
        body.addWidget(sidebar)
        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side.addWidget(side_scroll)
        side_content = QWidget()
        side_content.setStyleSheet("background: transparent;")
        side_scroll.setWidget(side_content)
        settings = QVBoxLayout(side_content)
        settings.setContentsMargins(0, 0, 0, 0)
        settings.setSpacing(8)
        self.drop_area = DropArea()
        self.drop_area.choose_button.clicked.connect(self.choose_file)
        self.drop_area.fileDropped.connect(self.import_files)
        self.drop_area.rejected.connect(self.show_error)
        settings.addWidget(self.drop_area)
        self.file_label = label("尚未导入 CSV", "muted", True)
        settings.addWidget(self.file_label)
        self.file_details_button = QPushButton("查看文件与全量核对")
        self.file_details_button.setEnabled(False)
        self.file_details_button.clicked.connect(self.show_sources)
        settings.addWidget(self.file_details_button)
        encoding_row = QHBoxLayout()
        encoding_row.addWidget(label("编码", "muted"))
        self.encoding_combo = QComboBox()
        self.encoding_combo.addItem("UTF-8（默认）", "utf-8-sig")
        self.encoding_combo.addItem("GB18030 / GBK", "gb18030")
        encoding_row.addWidget(self.encoding_combo, 1)
        settings.addLayout(encoding_row)
        self.progress_area = QWidget()
        progress_layout = QVBoxLayout(self.progress_area)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.progress_label = label("等待导入", "muted")
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.cancel_button = QPushButton("取消导入")
        self.cancel_button.clicked.connect(self.cancel_import)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.cancel_button)
        settings.addWidget(self.progress_area)
        self.progress_area.hide()
        self.filters_widget = QWidget()
        filters_layout = QVBoxLayout(self.filters_widget)
        filters_layout.setContentsMargins(0, 5, 0, 0)
        filters_layout.setSpacing(7)
        settings.addWidget(self.filters_widget)
        filters_layout.addWidget(label("日期与好友", "section"))
        filters_layout.addWidget(label("只输入年月日数字 · 横线自动补 · 留空不限", "muted"))
        self.start_date = DateInput()
        self.end_date = DateInput()
        for box in (self.start_date, self.end_date):
            box.textChanged.connect(self.filters_changed)
        dates = QHBoxLayout()
        for title, date_edit in (("开始日期", self.start_date), ("结束日期", self.end_date)):
            col = QVBoxLayout()
            col.addWidget(label(title))
            col.addWidget(date_edit)
            dates.addLayout(col)
        filters_layout.addLayout(dates)
        filters_layout.addWidget(label("好友 UID", "section"))
        self.id_input = QPlainTextEdit()
        self.id_input.setPlaceholderText("粘贴一个或多个好友 ID\n每行一个，或用空格、逗号分隔\n\n留空统计所有好友")
        self.id_input.setMinimumHeight(84)
        self.id_input.setMaximumHeight(94)
        self.id_input.textChanged.connect(self.filters_changed)
        filters_layout.addWidget(self.id_input)
        self.id_hint = label("未限定 ID · 统计所有好友", "muted", True)
        filters_layout.addWidget(self.id_hint)
        self.order_combo = QComboBox()
        self.order_combo.addItem("全部订单类型", None)
        self.order_combo.currentIndexChanged.connect(self.filters_changed)
        filters_layout.addWidget(self.order_combo)
        buttons = QHBoxLayout()
        self.query_button = QPushButton("统计返佣")
        self.query_button.setObjectName("primary")
        self.query_button.setEnabled(False)
        self.query_button.clicked.connect(self.calculate)
        self.reset_button = QPushButton("清空条件")
        self.reset_button.clicked.connect(self.reset_filters)
        buttons.addWidget(self.query_button, 1)
        buttons.addWidget(self.reset_button)
        settings.addStretch()
        # Primary actions stay visible even when the settings pane needs scrolling.
        side.addLayout(buttons)
        side.addWidget(label("重新选择会替换整批 · 重复行全部累计", "muted", True))

        right = QVBoxLayout()
        right.setSpacing(8)
        body.addLayout(right, 1)
        hero, hero_layout = panel("hero", 12)
        hero_layout.setSpacing(4)
        hero_top = QHBoxLayout()
        hero_top.addWidget(label("筛选后返佣总额", "heroCaption"))
        hero_top.addStretch()
        self.copy_button = QPushButton("复制金额")
        self.copy_button.setObjectName("copy")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_total)
        hero_top.addWidget(self.copy_button)
        hero_layout.addLayout(hero_top)
        amount_row = QHBoxLayout()
        self.total_label = label("—", "amount")
        self.total_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        amount_row.addWidget(self.total_label)
        amount_row.addWidget(label("USDT", "heroCaption"))
        amount_row.addStretch()
        hero_layout.addLayout(amount_row)
        self.scope_label = label("导入 CSV 后即可开始统计", "heroCaption", True)
        hero_layout.addWidget(self.scope_label)
        right.addWidget(hero)
        metrics = QHBoxLayout()
        self.count_label = self.add_metric(metrics, "返佣记录数", "—")
        self.friends_label = self.add_metric(metrics, "匹配好友数", "—")
        self.days_label = self.add_metric(metrics, "有记录的日期", "—")
        right.addLayout(metrics)
        self.audit_label = label("UID 核对 · 导入后显示完整结果", "audit", True)
        self.audit_label.setTextFormat(Qt.TextFormat.RichText)
        self.audit_label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse |
                                                 Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.audit_label.setToolTip("点击数字筛选下方 UID；再次点击同一项恢复全部")
        self.audit_label.linkActivated.connect(self.toggle_summary_filter)
        right.addWidget(self.audit_label)
        self.missing_panel, missing_layout = panel("missingPanel", 8)
        missing_layout.setSpacing(4)
        missing_header = QHBoxLayout()
        self.missing_label = label("", "missingTitle")
        self.missing_label.setTextFormat(Qt.TextFormat.RichText)
        self.missing_label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse |
                                                   Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.missing_label.setToolTip("点击状态数字筛选下方 UID；再次点击同一项恢复全部")
        self.missing_label.linkActivated.connect(self.toggle_summary_filter)
        missing_header.addWidget(self.missing_label, 1)
        self.copy_missing_button = QPushButton("复制名单")
        self.copy_missing_button.clicked.connect(self.copy_missing_ids)
        self.show_missing_button = QPushButton("表内查看")
        self.show_missing_button.clicked.connect(self.show_missing_ids)
        missing_header.addWidget(self.copy_missing_button)
        missing_header.addWidget(self.show_missing_button)
        missing_layout.addLayout(missing_header)
        self.missing_list = QPlainTextEdit()
        self.missing_list.setObjectName("missingIds")
        self.missing_list.setReadOnly(True)
        self.missing_list.setFixedHeight(30)
        self.missing_list.setAccessibleName("所选 CSV 中未找到的完整 ID 名单")
        missing_layout.addWidget(self.missing_list)
        right.addWidget(self.missing_panel)
        self.missing_panel.hide()
        self.warning_label = label("", "notice", True)
        self.warning_label.hide()
        right.addWidget(self.warning_label)
        table_panel, table_layout = panel(margins=10)
        table_layout.setSpacing(6)
        table_header = QHBoxLayout()
        table_header.addWidget(label("结算明细", "section"))
        table_header.addStretch()
        self.copy_ids_button = QPushButton("复制表内 UID")
        self.copy_ids_button.setEnabled(False)
        self.copy_ids_button.clicked.connect(self.copy_visible_ids)
        table_header.addWidget(self.copy_ids_button)
        self.export_button = QPushButton("导出当前表")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_current)
        table_header.addWidget(self.export_button)
        table_layout.addLayout(table_header)
        self.uid_controls = QWidget()
        controls = QHBoxLayout(self.uid_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self.status_filter = QComboBox()
        self.status_filter.addItem("全部 UID 状态", "")
        self.status_filter.addItem("无记录 / 净额为 0", "no_rebate")
        for key in ("missing", "inactive", "zero", "positive", "negative"):
            self.status_filter.addItem(STATUS_LABELS[key], key)
        self.uid_search = QLineEdit()
        self.uid_search.setPlaceholderText("查找表内 UID")
        self.uid_search.setClearButtonEnabled(True)
        controls.addWidget(self.status_filter)
        controls.addWidget(self.uid_search, 1)
        table_layout.addWidget(self.uid_controls)
        self.tabs = QTabWidget()
        self.models = []
        for title, headings in (("UID 核对", ["好友 UID", "匹配笔数", "返佣 (USDT)", "在文件中", "当前状态", "全量笔数"]),
                                ("按日期", ["返佣日期", "返佣笔数", "返佣收入 (USDT)"]),
                                ("日期 × 好友", ["返佣日期", "好友 ID", "返佣笔数", "返佣收入 (USDT)"])):
            table = QTableView()
            model = SummaryModel(headings, table)
            table.setModel(model)
            table.setShowGrid(False)
            table.setSortingEnabled(True)
            table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
            table.verticalHeader().hide()
            table.verticalHeader().setDefaultSectionSize(32)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setMinimumSectionSize(55)
            if len(headings) == 6:
                table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
                for column, width in enumerate((104, 62, 136, 60, 160, 64)):
                    table.setColumnWidth(column, width)
            table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
            table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
            self.models.append(model)
            self.tabs.addTab(table, title)
        table_layout.addWidget(self.tabs, 1)
        self.status_filter.currentIndexChanged.connect(self.update_uid_view)
        self.uid_search.textChanged.connect(self.update_uid_view)
        self.tabs.currentChanged.connect(self.update_uid_view)
        self.table_footer = label("每一条原始记录均计入金额；汇总表按所选维度合并展示。", "muted", True)
        table_layout.addWidget(self.table_footer)
        right.addWidget(table_panel, 1)
        self.statusBar().showMessage("就绪 · 可多选或同时拖入多个 CSV")

    @staticmethod
    def add_metric(layout, title, initial):
        box, col = panel(margins=8)
        col.setSpacing(4)
        col.addWidget(label(title, "muted"))
        value = label(initial, "metric")
        col.addWidget(value)
        layout.addWidget(box, 1)
        return value

    def show_error(self, message):
        self.last_error = message
        self.warning_label.setText(message)
        self.warning_label.setToolTip("")
        self.warning_label.show()
        self.statusBar().showMessage(message)

    def invalidate_result(self, caption="条件已更改，请点击“统计返佣”更新结果"):
        self.result = None
        self.pending_filters = False
        self.applied_scope = ""
        self.copy_ids_button.setEnabled(False)
        for model in self.models:
            model.set_rows([])
        for widget in (self.total_label, self.count_label, self.friends_label, self.days_label):
            widget.setText("—")
        self.copy_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.scope_label.setText(caption)
        self.table_footer.setText("结果尚未更新 · 请重新统计")
        self.audit_label.setText("UID 核对 · 结果尚未更新")
        self.audit_label.show()
        self.missing_label.clear()
        self.missing_list.clear()
        self.missing_panel.hide()
        self.copy_missing_button.setEnabled(False)
        self.show_missing_button.setEnabled(False)
        self.warning_label.clear()
        self.warning_label.setToolTip("")
        self.warning_label.hide()

    def filters_changed(self, *_):
        try:
            ids = parse_ids(self.id_input.toPlainText())
            self.id_hint.setText(f"已识别 {len(ids)} 个不同 ID" if ids else "未限定 ID · 统计所有好友")
        except DataError as exc:
            self.id_hint.setText(str(exc))
        if self.dataset is None:
            return
        self.pending_filters = True
        if self.result is not None:
            self.scope_label.setText(self.applied_scope + "\n条件已修改 · 当前仍显示上次统计结果")
        else:
            self.scope_label.setText("条件已修改 · 点击“统计返佣”生成结果")
        self.copy_button.setEnabled(False)
        self.copy_ids_button.setEnabled(False)
        self.copy_missing_button.setEnabled(False)
        self.show_missing_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.warning_label.clear()
        self.warning_label.hide()
        self.statusBar().showMessage("条件已修改 · 当前显示上次结果，点击“统计返佣”更新")

    def get_filters(self):
        return Filters(parse_ids(self.id_input.toPlainText()),
                       parse_filter_date(self.start_date.text(), "开始日期"),
                       parse_filter_date(self.end_date.text(), "结束日期"),
                       self.order_combo.currentData())

    def reset_filters(self):
        self.start_date.clear()
        self.end_date.clear()
        self.id_input.clear()
        self.order_combo.setCurrentIndex(0)

    def choose_file(self):
        folder = Path(str(self.settings.value("last_directory", str(Path.home()))))
        if not folder.is_dir():
            folder = Path.home()
        paths, _ = QFileDialog.getOpenFileNames(self, "选择返佣 CSV（可多选）", str(folder), "CSV 表格 (*.csv *.CSV)")
        if paths:
            self.settings.setValue("last_directory", str(Path(paths[0]).expanduser().resolve().parent))
            self.settings.sync()
            self.import_files(paths)

    def restore_last_files(self):
        saved = self.settings.value("last_files", [])
        paths = [saved] if isinstance(saved, str) else saved
        if not paths:
            return
        paths = [Path(path) for path in paths]
        if not all(path.is_file() for path in paths):
            self.show_error("上次导入的 CSV 已移动或删除，请重新选择文件")
            return
        index = self.encoding_combo.findData(self.settings.value("last_encoding", "utf-8-sig"))
        if index >= 0:
            self.encoding_combo.setCurrentIndex(index)
        self.import_files(paths)

    def import_file(self, path):
        self.import_files([path])

    def import_files(self, paths):
        if not paths:
            return
        if self.worker is not None:
            self.statusBar().showMessage("正在导入，请先取消或等待完成")
            return
        # Atomic replacement, never append. Even an identical file starts a fresh cache.
        self.dataset = None
        self.file_details_button.setEnabled(False)
        self.last_error = ""
        self.cancel_requested = False
        self.invalidate_result("正在导入并建立汇总缓存…")
        self.file_label.setText(f"正在导入 {len(paths)} 个 CSV…")
        self.file_label.setToolTip("")
        self.current_file_caption = ""
        self.progress_area.show()
        self.progress_bar.setValue(0)
        self.progress_label.setText("正在读取 CSV…")
        self.cancel_button.setEnabled(True)
        self.drop_area.setEnabled(False)
        self.encoding_combo.setEnabled(False)
        self.filters_widget.setEnabled(False)
        self.query_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.worker = ImportWorker(paths, self.encoding_combo.currentData(), self)
        self.worker.file_started.connect(self.update_file_progress)
        self.worker.progress.connect(self.update_progress)
        self.worker.loaded.connect(self.import_loaded)
        self.worker.failed.connect(self.import_failed)
        self.worker.cancelled.connect(self.import_cancelled)
        self.worker.finished.connect(self.import_finished)
        self.worker.start()

    def update_progress(self, percent, count):
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"{self.current_file_caption} · {percent}%\n已读取 {count:,} 条")

    def update_file_progress(self, index, total, name):
        self.current_file_caption = f"文件 {index}/{total}"
        self.file_label.setText(f"正在导入 {index}/{total}\n{name}")

    def cancel_import(self):
        self.cancel_requested = True
        if self.worker:
            self.worker.requestInterruption()
        self.cancel_button.setEnabled(False)
        self.progress_label.setText("正在取消，请稍候…")

    def import_loaded(self, dataset):
        if self.cancel_requested or self.close_requested:
            self.import_cancelled()
            return
        self.dataset = dataset
        self.file_label.setText(f"{len(dataset.sources)} 个 CSV · {dataset.size_bytes / 1_000_000:.1f} MB · 最新 {dataset.max_day or '无日期'}\n共 {dataset.row_count:,} 条 · {len(dataset.ids):,} 个 UID")
        self.file_details_button.setEnabled(True)
        previous_order = self.order_combo.currentData()
        self.order_combo.blockSignals(True)
        self.order_combo.clear()
        self.order_combo.addItem("全部订单类型", None)
        names = {"spot": "现货 · spot", "USDT-futures": "USDT 合约"}
        for order in dataset.order_types:
            self.order_combo.addItem(names.get(order, order), order)
        index = self.order_combo.findData(previous_order)
        if index >= 0:
            self.order_combo.setCurrentIndex(index)
        elif previous_order is not None:
            self.order_combo.addItem(f"{previous_order}（本批无此类型）", previous_order)
            self.order_combo.setCurrentIndex(self.order_combo.count() - 1)
        self.order_combo.blockSignals(False)
        self.calculate()
        self.settings.setValue("last_files", [str(source.path) for source in dataset.sources])
        self.settings.setValue("last_directory", str(dataset.sources[0].path.parent))
        self.settings.setValue("last_encoding", dataset.encoding)
        self.settings.sync()

    def import_failed(self, message):
        self.dataset = None
        self.file_details_button.setEnabled(False)
        self.invalidate_result("导入失败 · 未生成任何不完整的统计结果")
        self.file_label.setText("导入失败，请检查文件或编码后重试")
        self.show_error(message)

    def import_cancelled(self):
        self.dataset = None
        self.file_details_button.setEnabled(False)
        self.invalidate_result("已取消导入 · 未保留部分统计")
        self.file_label.setText("尚未导入 CSV")
        self.statusBar().showMessage("已取消，未发布部分结果")

    def import_finished(self):
        worker = self.worker
        self.worker = None
        if worker:
            worker.deleteLater()
        self.progress_area.hide()
        self.drop_area.setEnabled(True)
        self.encoding_combo.setEnabled(True)
        self.filters_widget.setEnabled(True)
        self.query_button.setEnabled(self.dataset is not None)
        self.reset_button.setEnabled(True)
        if self.close_requested:
            self.close()

    def calculate(self):
        if self.dataset is None:
            return
        try:
            result = self.dataset.query(self.get_filters())
        except DataError as exc:
            self.show_error(str(exc))
            return
        self.result = result
        self.pending_filters = False
        amount = format_money(result.total, grouping=True)
        self.total_label.setText(amount)
        self.total_label.setStyleSheet(f"font-size: {30 if len(amount) < 25 else 18}px;")
        self.count_label.setText(f"{result.count:,}")
        self.friends_label.setText(f"{result.matched_ids:,}")
        self.days_label.setText(f"{len(result.per_day):,}")
        f = result.filters
        start = str(f.start or self.dataset.min_day or "—")
        end = str(f.end or self.dataset.max_day or "—")
        scope = f"{start} 至 {end}（含首尾） · "
        scope += f"指定 {len(set(f.ids))} 个好友" if f.ids else "所有好友"
        scope += f" · {f.order_type or '全部订单'}"
        self.applied_scope = scope
        self.scope_label.setText(scope)
        self.models[1].set_rows([(day, a.count, a.total) for day, a in result.per_day.items()])
        self.models[2].set_rows([(day, uid, a.count, a.total) for (day, uid), a in result.daily_ids.items()])
        missing = result.missing_ids
        self.missing_list.setPlainText("、".join(missing))
        self.audit_label.setVisible(not missing)
        self.missing_panel.setVisible(bool(missing))
        self.copy_missing_button.setEnabled(bool(missing))
        self.show_missing_button.setEnabled(bool(missing))
        self.warning_label.clear()
        self.warning_label.setToolTip("")
        self.warning_label.hide()
        if f.end and self.dataset.max_day and f.end > self.dataset.max_day:
            self.warning_label.setText(f"所选结束日期为 {f.end}，文件最新返佣记录仅到 {self.dataset.max_day}；之后暂无记录，请核对导出范围。")
            self.warning_label.show()
        self.status_filter.blockSignals(True)
        self.status_filter.setCurrentIndex(0)
        self.status_filter.blockSignals(False)
        self.uid_search.blockSignals(True)
        self.uid_search.clear()
        self.uid_search.blockSignals(False)
        self.copy_button.setEnabled(True)
        self.export_button.setEnabled(True)
        self.update_uid_view()
        self.statusBar().showMessage("统计完成 · 金额精确计算 · 原始文件未修改")

    def update_uid_view(self, *_):
        is_uid = self.tabs.currentIndex() == 0
        self.uid_controls.setVisible(is_uid)
        self.copy_ids_button.setVisible(is_uid)
        if self.result is None:
            return
        result = self.result
        self.update_audit_links()
        ids = result.visible_ids(self.status_filter.currentData(), self.uid_search.text())
        self.models[0].missing_ids = set(result.missing_ids)
        self.models[0].set_rows([(uid, result.per_id[uid].count, result.per_id[uid].total,
            "否" if result.statuses[uid] == "missing" else "是", STATUS_LABELS[result.statuses[uid]],
            result.lifetime_counts.get(uid, 0)) for uid in ids])
        self.copy_ids_button.setEnabled(bool(ids) and not self.pending_filters)
        caption = f"源文件共 {self.dataset.row_count:,} 条，本次匹配 {result.count:,} 条。"
        if is_uid:
            caption += f"\n表内 {len(ids):,}/{len(result.per_id):,} 个 UID · 状态/查找仅筛选此表，不改变总额。"
        else:
            caption += "相同记录全部累计。"
        self.table_footer.setText(caption)

    def update_audit_links(self):
        result = self.result
        if result is None:
            return
        counts = Counter(result.statuses.values())
        presence = ("输入 UID 全部在所选文件中" if result.filters.ids and not counts["missing"] else
                    f"已输入 {len(result.filters.ids)} 个 UID" if result.filters.ids else
                    f"核对批次全部 {len(result.per_id)} 个 UID")
        selected = self.status_filter.currentData()
        def link(status, caption):
            color = "#125f4d" if selected == status else "#345f54"
            weight = "700" if selected == status else "400"
            return f'<a href="{status}" style="color: {color}; font-weight: {weight}; text-decoration: underline;">{caption}</a>'
        missing_caption = f"未找到 {counts['missing']} 个"
        first = f"{presence} · {link('missing', missing_caption)}"
        second = " · ".join((link("positive", f"有返佣 {counts['positive']}"),
                             link("negative", f"净额为负 {counts['negative']}"),
                             link("zero", f"净额为 0 {counts['zero']}"),
                             link("inactive", f"当前无记录 {counts['inactive']}")))
        self.audit_label.setText(first + "<br>" + second)
        self.missing_label.setText(link("missing", missing_caption) + " · " + second)

    def toggle_summary_filter(self, status):
        if self.result is None or status not in ("missing", "positive", "negative", "zero", "inactive"):
            return
        self.uid_search.clear()
        target = "" if self.status_filter.currentData() == status else status
        self.status_filter.setCurrentIndex(self.status_filter.findData(target))
        self.tabs.setCurrentIndex(0)

    def copy_visible_ids(self):
        if self.result is not None and not self.pending_filters:
            QApplication.clipboard().setText("\n".join(row[0] for row in self.models[0].rows))
            self.statusBar().showMessage(f"已复制表内 {len(self.models[0].rows)} 个完整 UID", 5000)

    def copy_missing_ids(self):
        if self.result is not None and not self.pending_filters and self.result.missing_ids:
            QApplication.clipboard().setText("\n".join(self.result.missing_ids))
            self.statusBar().showMessage(f"已复制 {len(self.result.missing_ids)} 个未找到的完整 ID", 5000)

    def show_missing_ids(self):
        if self.result is not None and not self.pending_filters and self.result.missing_ids:
            self.uid_search.clear()
            self.status_filter.setCurrentIndex(self.status_filter.findData("missing"))
            self.tabs.setCurrentIndex(0)

    def show_sources(self):
        if self.dataset is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("来源文件 · 全量核对（不受筛选影响）")
        dialog.resize(680, 400)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        sections = []
        for index, source in enumerate(self.dataset.sources, 1):
            sections.append(f"{index}. {source.path.name}\n{source.row_count:,} 条 · {format_money(source.total)} USDT\n"
                            f"{source.min_day or '无日期'} 至 {source.max_day or '无日期'}\n{source.path}")
        sections.append("所有文件逐行累计，包含文件之间的相同记录。此处展示原文件全量，不是当前筛选结果。")
        view.setPlainText("\n\n".join(sections))
        layout.addWidget(view)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def copy_total(self):
        if self.result and not self.pending_filters:
            QApplication.clipboard().setText(format_money(self.result.total))
            self.statusBar().showMessage("金额已复制（保留完整小数精度）", 5000)

    def export_current(self):
        if self.result is None or self.dataset is None or self.pending_filters:
            return
        index = self.tabs.currentIndex()
        names = ("按好友", "按日期", "日期与好友")
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "导出当前筛选结果", f"返佣汇总-{names[index]}",
            "CSV 表格 (*.csv);;Excel 工作簿 (*.xlsx)",
            options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        if Path(path).suffix.lower() not in (".csv", ".xlsx"):
            path += ".xlsx" if "*.xlsx" in selected_filter else ".csv"
        # Confirm AFTER extension normalization, so choosing "out" cannot silently
        # overwrite an existing "out.csv" that the native picker did not check.
        try:
            validate_export_target(path, self.dataset)
        except (OSError, DataError) as exc:
            self.show_error(str(exc))
            return
        if Path(path).exists():
            answer = QMessageBox.question(self, "确认覆盖", f"文件已存在，是否覆盖？\n{path}",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            count = export_result(path, self.dataset, self.result, ("id", "day", "daily_id")[index],
                                  self.status_filter.currentData(), self.uid_search.text())
        except (OSError, DataError) as exc:
            self.show_error(str(exc))
            return
        self.statusBar().showMessage(f"已导出 {count:,} 行：{path}")

    def closeEvent(self, event):
        if self.worker is not None:
            self.close_requested = True
            self.cancel_import()
            event.ignore()
        else:
            event.accept()


def instance_name():
    project = str(Path(__file__).resolve()).encode("utf-8")
    return f"rebate-settlement-{os.getuid()}-{hashlib.sha256(project).hexdigest()[:16]}"


def notify_running_instance(name, paths):
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(300):
        socket.write(json.dumps(paths, ensure_ascii=False).encode("utf-8") + b"\n")
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()


def serve_instance_requests(server, window):
    while server.hasPendingConnections():
        socket = server.nextPendingConnection()
        data = bytearray()

        def receive(socket=socket, data=data):
            data.extend(bytes(socket.readAll()))
            if b"\n" not in data:
                return
            try:
                paths = json.loads(data.split(b"\n", 1)[0])
            except (UnicodeDecodeError, json.JSONDecodeError):
                paths = []
            window.showNormal()
            window.raise_()
            window.activateWindow()
            if isinstance(paths, list) and paths and all(isinstance(path, str) for path in paths):
                window.import_files(paths)
            socket.disconnectFromServer()

        socket.readyRead.connect(receive)
        socket.disconnected.connect(socket.deleteLater)
        receive()


def main():
    parser = argparse.ArgumentParser(description="本地返佣结算工具")
    parser.add_argument("csv", nargs="*", help="启动后合并导入这些 CSV")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("返佣结算")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    name = instance_name()
    lock = QLockFile(str(Path(tempfile.gettempdir()) / f"{name}.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        notify_running_instance(name, args.csv)
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):
        print(f"无法建立单实例通道：{server.errorString()}", file=sys.stderr)
        return 1
    window = MainWindow()
    server.newConnection.connect(lambda: serve_instance_requests(server, window))
    window.show()
    if args.csv:
        QTimer.singleShot(0, lambda: window.import_files(args.csv))
    else:
        QTimer.singleShot(0, window.restore_last_files)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
