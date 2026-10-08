"""Render the desktop UI using generated, non-sensitive sample data."""
import csv
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication
from app import MainWindow, STYLE


def main():
    application = QApplication([])
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    output = Path(__file__).resolve().parents[1] / "artifacts"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rebate-preview-") as folder:
        path = Path(folder) / "界面演示-非真实数据.csv"
        second = Path(folder) / "界面演示-分卷2.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["好友ID（现货）", "返佣收入(USDT)", "返佣时间", "订单类型"])
            for day in range(18, 25):
                for uid in range(20):
                    for _ in range(uid % 6 + 1):
                        writer.writerow([str(10010000 + uid), f"{uid + 1}.{day}120087", f"2026-08-{day} 23:59:59", "spot"])
        with second.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["好友ID（现货）", "返佣收入(USDT)", "返佣时间", "订单类型"])
            for uid in range(20, 26):
                writer.writerow([str(10010000 + uid), "1", "2026-08-17", "spot"])
            writer.writerow(["10010026", "0E-8", "2026-08-20", "spot"])
            writer.writerow(["10010027", "-0.10", "2026-08-20", "spot"])
        window = MainWindow(settings=QSettings(str(Path(folder) / "preview.ini"), QSettings.Format.IniFormat))
        window.show()
        window.import_files([str(path), str(second)])
        monitor = QTimer()
        monitor.setInterval(50)
        def save():
            if window.worker is not None:
                return
            monitor.stop()
            if window.result is None:
                print("PREVIEW FAILED", window.last_error)
                application.exit(1)
                return
            window.start_date.setText("2026-08-18")
            window.end_date.setText("2026-08-24")
            window.id_input.setPlainText("\n".join(str(10010000 + uid) for uid in range(30)))
            window.calculate()
            def capture():
                assert window.grab().save(str(output / "ui-preview.png"))
                window.resize(960, 680)
                window.status_filter.setCurrentIndex(window.status_filter.findData("no_rebate"))
                application.processEvents()
                assert window.grab().save(str(output / "ui-compact.png"))
                window.show_missing_button.click()
                application.processEvents()
                assert window.grab().save(str(output / "ui-missing.png"))
                assert window.models[0].rowCount() == 2
                assert window.result.count == 464
                window.end_date.setText("2026-08-23")
                application.processEvents()
                assert window.result.count == 464 and not window.export_button.isEnabled()
                assert window.grab().save(str(output / "ui-pending.png"))
                window.id_input.setPlainText("\n".join(str(10010000 + uid) for uid in range(28)))
                window.end_date.setText("2026-08-24")
                window.calculate()
                application.processEvents()
                assert window.grab().save(str(output / "ui-summary.png"))
                window.audit_label.linkActivated.emit("inactive")
                application.processEvents()
                assert len(window.models[0].rows) == 6
                assert window.grab().save(str(output / "ui-summary-filtered.png"))
                print("GUI preview saved; synthetic data only")
                window.close()
                application.quit()
            QTimer.singleShot(200, capture)
        monitor.timeout.connect(save)
        monitor.start()
        QTimer.singleShot(30000, lambda: application.exit(2))
        return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
