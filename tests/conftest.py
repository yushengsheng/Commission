import csv
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HEADERS = ["订单类型", "好友ID（现货）", "返佣收入", "返佣收入(USDT)", "返佣时间"]
ROWS = [
    ["spot", "001", "999", "0.10000000", "2026-08-18 00:00:00"],
    ["spot", "001", "999", "0.10000000", "2026-08-18 00:00:00"],
    ["USDT-futures", "2", "999", "0.20000000", "2026-08-24 23:59:59.999999"],
    ["spot", "2", "999", "0.30000000", "2026-08-25 00:00:00"],
    ["spot", "12", "999", "1", "2026-08-17 23:59:59"],
    ["spot", "123", "999", "2", "2026-08-20 12:00:00"],
    ["spot", "3", "999", "-0.05", "2026-08-24 10:00:00"],
    ["spot", "4", "999", "0", "2026-08-24 10:00:00"],
]


@pytest.fixture
def make_csv(tmp_path):
    def make(rows=None, headers=None, filename="sample.csv", encoding="utf-8-sig"):
        path = tmp_path / filename
        with path.open("w", encoding=encoding, newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADERS if headers is None else headers)
            writer.writerows(ROWS if rows is None else rows)
        return path
    return make


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    from app import STYLE
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    return application
