"""List, enable, and delete KWin window rules."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from onigiri.kwin import KWinRules


class RulesDialog(QDialog):
    def __init__(self, rules: KWinRules, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("rulesDialog")
        self.setWindowTitle("Window rules")
        self.resize(560, 460)
        self._rules = rules
        self._loading = False

        note = QLabel(
            "Green entries were written by Onigiri. Unchecking a rule sets Enabled=false "
            "and leaves the rest of the file alone."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)

        self.list = QListWidget()
        self.list.itemChanged.connect(self._on_toggled)

        remove = QPushButton("Delete selected")
        remove.clicked.connect(self._on_delete)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(remove)
        buttons.addStretch(1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(note)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)
        self.reload()

    def reload(self) -> None:
        self._loading = True
        self.list.clear()
        for rule in self._rules.list_rules():
            label = rule.description or rule.id
            details = []
            if rule.wmclass:
                details.append(rule.wmclass)
            elif rule.title:
                details.append(rule.title)
            if rule.position:
                details.append(rule.position)
            if details:
                label = f"{label}   {' · '.join(details)}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, rule.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, rule.owned)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
            if rule.owned:
                item.setForeground(QColor("#9ddec0"))
            self.list.addItem(item)
        self._loading = False

    def _on_toggled(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        rule_id = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        try:
            self._rules.set_enabled(str(rule_id), enabled)
        except OSError as exc:
            QMessageBox.critical(self, "Could not update the rule", str(exc))
            self.reload()

    def _on_delete(self) -> None:
        item = self.list.currentItem()
        if item is None:
            QMessageBox.information(self, "No rule selected", "Select a rule first.")
            return
        rule_id = str(item.data(Qt.ItemDataRole.UserRole))
        owned = bool(item.data(Qt.ItemDataRole.UserRole + 1))
        if not owned:
            answer = QMessageBox.question(
                self,
                "Delete window rule",
                "This rule was not created by Onigiri. Delete it from kwinrulesrc?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        else:
            answer = QMessageBox.question(self, "Delete window rule", "Delete this Onigiri rule?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self._rules.delete_rule(rule_id)
        except OSError as exc:
            QMessageBox.critical(self, "Could not delete the rule", str(exc))
            return
        self.reload()
