"""Public extension-page context and reusable standalone controls."""
from __future__ import annotations

import json
import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from onigiri.extensions import apply_extension, stop_extension, extension_running
from onigiri.files import atomic_write_text
from onigiri.ui.scene_dialog import PluginPanel


def controls_path():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "onigiri/extension-controls.json"


class ExtensionPageContext:
    """UI-thread page factory context. All hardware work goes through run/apply/stop."""
    def __init__(self, info, jobs, status_callback, open_callback=None, settings_path=None):
        self.info, self.jobs = info, jobs
        self.status_callback = status_callback
        self.open_callback = open_callback
        self.settings_path = settings_path or controls_path()
        self.status_listener = None
        self.pending = 0
        self.last_status = ""

    def set_status(self, message):
        message = str(message)
        self.last_status = message
        self.status_callback(f"{self.info.title}: {message}")
        if self.status_listener is not None:
            self.status_listener(message)

    def run(self, work, on_success=None, message="Working…", on_error=None):
        """Run blocking work on Onigiri's serialized background queue."""
        self.pending += 1
        self.set_status(message)
        def done(result):
            self.pending -= 1
            if isinstance(result, dict) and "value" in result:
                try:
                    self.set_status("Done.")
                    if on_success is not None:
                        on_success(result["value"])
                except Exception as error:
                    self.set_status(str(error))
            else:
                error = "; ".join(str(item) for item in result) or "The operation failed."
                self.set_status(error)
                if on_error is not None:
                    on_error(error)
        self.jobs.submit(lambda: {"value": work()}, done)

    def saved_settings(self):
        if not self.settings_path.exists():
            return {}
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            settings = data.get(self.info.stem, {})
            return dict(settings) if isinstance(settings, dict) else {}
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise RuntimeError("Saved extension controls could not be read; the file was left unchanged.") from error

    def _save_settings(self, values):
        data = {}
        if self.settings_path.exists():
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise RuntimeError("Saved extension controls are invalid; the file was left unchanged.")
        data[self.info.stem] = dict(values)
        atomic_write_text(self.settings_path, json.dumps(data, indent=2) + "\n")

    def apply(self, settings, on_success=None):
        values = dict(settings)
        def work():
            self.saved_settings()  # Validate before changing hardware; preserve unreadable settings.
            errors = apply_extension(self.info, values)
            if errors:
                raise RuntimeError("; ".join(errors))
            self._save_settings(values)
        def done(_):
            self.set_status("Started." if self.info.continuous else "Applied.")
            if on_success is not None:
                on_success()
        self.run(work, done, message="Starting…" if self.info.continuous else "Applying…")

    def stop(self):
        def work():
            errors = stop_extension(self.info)
            if errors:
                raise RuntimeError("; ".join(errors))
        self.run(work, lambda _: self.set_status("Stopped."), message="Stopping…")

    def is_running(self):
        return extension_running(self.info)

    def open_extension(self, stem):
        if self.open_callback is not None:
            self.open_callback(stem)


class ExtensionControlPage(QWidget):
    """Fields plus explicit Apply/Start and Stop, separate from scene settings."""
    def __init__(self, context, description="", initial=None, apply_label=None):
        super().__init__()
        self.context = context
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(12, 12, 12, 12)
        self.column.setSpacing(12)
        title = QLabel(context.info.title)
        title.setObjectName("brand")
        title.setTextFormat(Qt.TextFormat.PlainText)
        self.column.addWidget(title)
        if description:
            note = QLabel(description)
            note.setWordWrap(True)
            note.setObjectName("muted")
            self.column.addWidget(note)
        self.editor = PluginPanel(context.info)
        self.editor.set_checked(True)
        self.editor.enabled.hide()
        self.editor.test_button.hide()
        self.editor.status.hide()
        self.editor.setMaximumWidth(560)
        self.column.addWidget(self.editor)
        try:
            values = dict(initial or {})
            values.update(context.saved_settings())
            self.editor.set_values(values, touched=False)
            initial_error = ""
        except Exception as error:
            initial_error = str(error)
        self.apply_button = QPushButton(apply_label or ("Start" if context.info.continuous else "Apply"))
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(lambda: context.apply(self.editor.values()))
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(context.stop)
        self.stop_button.setVisible(context.info.continuous)
        actions = QHBoxLayout()
        actions.addWidget(self.apply_button)
        actions.addWidget(self.stop_button)
        actions.addStretch(1)
        self.column.addLayout(actions)
        self.status = QLabel(initial_error)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        self.column.addWidget(self.status)
        self.column.addStretch(1)
        context.status_listener = self._set_status
        self._set_status(initial_error)

    def _set_status(self, message):
        self.status.setText(message)
        self.apply_button.setEnabled(self.context.pending == 0)
        self.stop_button.setEnabled(self.context.pending == 0 and self.context.is_running())

    def on_show(self):
        self._set_status(self.context.last_status or self.status.text())
