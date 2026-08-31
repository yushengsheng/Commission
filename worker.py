"""Background imports with cooperative cancellation and atomic publication."""
from PySide6.QtCore import QThread, Signal

from rebate_engine import ImportCancelled, load_csvs


class ImportWorker(QThread):
    loaded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(int, int)
    file_started = Signal(int, int, str)

    def __init__(self, path, encoding="utf-8-sig", parent=None):
        super().__init__(parent)
        self.path = path
        self.encoding = encoding

    def run(self):
        try:
            dataset = load_csvs(self.path, self.progress.emit, self.isInterruptionRequested,
                                self.encoding, self.file_started.emit)
            if self.isInterruptionRequested():
                raise ImportCancelled()
            self.loaded.emit(dataset)
        except ImportCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
