"""Run blocking scene operations in order and deliver results on the UI thread."""

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot


class _Task(QRunnable):
    def __init__(self, owner, token, work):
        super().__init__()
        self.owner, self.token, self.work = owner, token, work

    def run(self):
        try:
            errors = self.work() or []
        except Exception as exc:
            errors = [str(exc)]
        self.owner.finished.emit(self.token, errors)


class SceneJobs(QObject):
    finished = pyqtSignal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.callbacks = {}
        self.next_token = 0
        self.finished.connect(self._finish)

    def submit(self, work, done):
        self.next_token += 1
        token = self.next_token
        self.callbacks[token] = done
        self.pool.start(_Task(self, token, work))

    @pyqtSlot(int, object)
    def _finish(self, token, errors):
        callback = self.callbacks.pop(token, None)
        if callback is not None:
            callback(errors)

    def shutdown(self):
        """Drain the in-flight scene before stopping its continuous extensions."""
        from onigiri.extensions import stop_extensions
        self.pool.clear()
        self.pool.waitForDone()
        self.callbacks.clear()
        stop_extensions()
