"""Lazy-loaded standalone Hue pages. Importing the scene extensions does not open UI."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from onigiri.extensions import stop_extension
from onigiri.ui.extension_page import ExtensionControlPage
from _hue import HueSettings, LinkButtonNeeded, discover_bridges, list_lights, load_hue, pair_entertainment, save_hue


def note(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setObjectName("muted")
    return label


class HueSetup(QWidget):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.rows = {}
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(10)
        self.connection = note("")
        self.column.addWidget(self.connection)
        self.host = QLineEdit()
        self.host.setPlaceholderText("Hue Bridge IP address or hostname")
        self.host.setAccessibleName("Hue Bridge address")
        self.column.addWidget(self.host)
        actions = QHBoxLayout()
        self.discover_button = QPushButton("Find bridges")
        self.discover_button.clicked.connect(self.discover)
        self.pair_button = QPushButton("Pair / authorize streaming")
        self.pair_button.clicked.connect(self.pair)
        actions.addWidget(self.discover_button)
        actions.addWidget(self.pair_button)
        self.column.addLayout(actions)
        self.bridges = QComboBox()
        self.bridges.setAccessibleName("Discovered Hue bridges")
        self.bridges.activated.connect(lambda index: self.host.setText(self.bridges.itemData(index) or ""))
        self.bridges.hide()
        self.column.addWidget(self.bridges)
        self.column.addWidget(note("Press your bridge’s physical link button before pairing. Existing lamp assignments are kept when authorizing the same bridge."))
        refresh = QPushButton("Refresh lamps")
        refresh.clicked.connect(self.refresh_lamps)
        self.column.addWidget(refresh)
        self.lamps = QWidget()
        self.lamp_layout = QVBoxLayout(self.lamps)
        self.lamp_layout.setContentsMargins(0, 0, 0, 0)
        self.column.addWidget(self.lamps)
        self.save_button = QPushButton("Save lamp assignments")
        self.save_button.clicked.connect(self.save_assignments)
        self.save_button.setEnabled(False)
        self.column.addWidget(self.save_button)
        self.on_show()

    def on_show(self):
        settings = load_hue()
        self.host.setText(settings.bridge_ip)
        if settings.paired():
            suffix = "Streaming authorized." if settings.clientkey else "Authorize streaming to use Hue screen sync."
            self.connection.setText(f"Paired with {settings.bridge_ip}. {suffix}")
        else:
            self.connection.setText("No bridge paired. Find a bridge or enter its address, then pair.")

    def discover(self):
        def loaded(bridges):
            self.bridges.clear()
            for bridge in bridges:
                self.bridges.addItem(f"{bridge.name or 'Hue Bridge'} — {bridge.ip}", bridge.ip)
            self.bridges.setVisible(bool(bridges))
            if bridges:
                self.host.setText(bridges[0].ip)
            self.context.set_status("Choose a discovered bridge." if bridges else "No bridges found. Enter its IP address manually.")
        self.context.run(discover_bridges, loaded, message="Looking for Hue bridges…")

    def pair(self):
        host = self.host.text().strip()
        if not host or any(char in host for char in "/?#@ "):
            self.context.set_status("Enter the bridge’s IP address or hostname, without a URL prefix.")
            return
        def work():
            errors = stop_extension(replace(self.context.info, stem="hue_sync",
                                           path=self.context.info.path.with_name("hue_sync.py")))
            if errors:
                raise RuntimeError("; ".join(errors))
            existing = load_hue()
            settings = existing if existing.bridge_ip == host else HueSettings(bridge_ip=host)
            deadline = time.monotonic() + 30
            while True:
                try:
                    upgraded = pair_entertainment(settings)
                    save_hue(upgraded)
                    return None
                except LinkButtonNeeded:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("No bridge-button press detected. Press the button, then try Pair again.") from None
                    time.sleep(1)
        def paired(_):
            self.on_show()
            self.context.set_status("Bridge authorized. Refresh lamps to review their assignments.")
        self.context.run(work, paired, message="Press the Hue Bridge button; waiting for authorization…")

    def refresh_lamps(self):
        def work():
            settings = load_hue()
            if not settings.paired():
                raise RuntimeError("Pair the bridge before loading lamps.")
            return settings, list_lights(settings)
        self.context.run(work, self._show_lamps, message="Loading Hue lamps…")

    def _show_lamps(self, result):
        settings, lights = result
        while self.lamp_layout.count():
            item = self.lamp_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.rows = {}
        for light in lights:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            label = note(light.name + (" (offline)" if not light.reachable else ""))
            selector = QComboBox()
            selector.addItem("Unassigned", "")
            selector.addItem("Color 1", "blue")
            selector.addItem("Color 2", "purple")
            selector.setAccessibleName(f"Color assignment for {light.name}")
            selector.setCurrentIndex(1 if light.id in settings.blue else 2 if light.id in settings.purple else 0)
            layout.addWidget(label, 1)
            layout.addWidget(selector)
            self.lamp_layout.addWidget(row)
            self.rows[light.id] = selector
        self.save_button.setEnabled(bool(lights))
        self._assignment_bridge = settings.bridge_ip
        self.context.set_status("Lamp assignments loaded." if lights else "The bridge returned no lamps.")

    def save_assignments(self):
        assignments = {light_id: selector.currentData() for light_id, selector in self.rows.items()}
        bridge = getattr(self, "_assignment_bridge", "")
        def work():
            settings = load_hue()
            if settings.bridge_ip != bridge:
                raise RuntimeError("The paired bridge changed. Refresh lamps before saving assignments.")
            settings.blue = [key for key, slot in assignments.items() if slot == "blue"]
            settings.purple = [key for key, slot in assignments.items() if slot == "purple"]
            save_hue(settings)
        self.context.run(work, lambda _: self.context.set_status("Lamp assignments saved. Apply colors when ready."), message="Saving lamp assignments…")


class HuePage(ExtensionControlPage):
    def __init__(self, context):
        super().__init__(context, description="Pair your bridge, assign lamps to two colors, and apply lighting without a scene.")
        self.setup = HueSetup(context)
        self.setup.setMaximumWidth(560)
        self.column.insertWidget(2, self.setup)

    def on_show(self):
        super().on_show()
        self.setup.on_show()


class HueSyncPage(ExtensionControlPage):
    def __init__(self, context):
        super().__init__(context, description="Sync Hue lights with your screen independently of a scene. KDE will ask which screen to share.", apply_label="Start / update sync")
        setup = QWidget()
        column = QVBoxLayout(setup)
        column.setContentsMargins(0, 0, 0, 0)
        self.connection = note("")
        column.addWidget(self.connection)
        actions = QHBoxLayout()
        bridge = QPushButton("Bridge and lamp setup")
        bridge.clicked.connect(lambda: context.open_extension("hue"))
        refresh = QPushButton("Refresh Entertainment areas")
        refresh.clicked.connect(self.refresh_areas)
        actions.addWidget(bridge)
        actions.addWidget(refresh)
        column.addLayout(actions)
        self.areas = QComboBox()
        self.areas.setEditable(True)
        self.areas.setAccessibleName("Hue Entertainment area")
        self.areas.lineEdit().setPlaceholderText("Area name or ID (blank if only one)")
        area_field = self.editor._texts["area"]
        self.areas.setEditText(area_field.text())
        self.areas.lineEdit().textEdited.connect(area_field.setText)
        self.areas.activated.connect(self.choose_area)
        # Replace the generic text input with this discoverable, editable selector.
        form = self.editor.body.layout()
        index = form.indexOf(area_field)
        if index > 0 and form.itemAt(index - 1).widget() is not None:
            form.itemAt(index - 1).widget().hide()
        area_field.hide()
        column.addWidget(note("Entertainment area"))
        column.addWidget(self.areas)
        self.stop_button.setText("Stop sync")
        setup.setMaximumWidth(560)
        self.column.insertWidget(2, setup)
        self.on_show()

    def on_show(self):
        super().on_show()
        settings = load_hue()
        if not settings.paired():
            self.connection.setText("Set up a Hue Bridge before starting sync.")
        elif not settings.clientkey:
            self.connection.setText("Bridge paired. Authorize streaming on the Hue lamps page before starting sync.")
        else:
            self.connection.setText("Bridge paired and streaming authorized. Choose an area and start sync.")

    def refresh_areas(self):
        def work():
            try:
                from hue_entertainment import HueEntertainmentAPI
            except ImportError:
                raise RuntimeError("Hue screen-sync dependencies are missing. Reinstall with --hue-sync.") from None
            settings = load_hue()
            if not settings.paired():
                raise RuntimeError("Pair a bridge on the Hue lamps page first.")
            async def query():
                api = HueEntertainmentAPI(settings.bridge_ip, settings.username)
                try:
                    return await asyncio.wait_for(api.get_entertainment_areas(), timeout=12)
                finally:
                    await api.close()
            try:
                return asyncio.run(query())
            except Exception:
                raise RuntimeError("Could not load Entertainment areas. Check the bridge and network.") from None
        def loaded(areas):
            current = self.editor._texts["area"].text()
            self.areas.clear()
            selected = 0
            for index, area in enumerate(areas):
                self.areas.addItem(area.name, area.id)
                if current in (area.id, area.name):
                    selected = index
            if areas:
                self.areas.setCurrentIndex(selected)
                self.choose_area(selected)
            self.context.set_status("Select an area, then Start sync." if areas else "Create an Entertainment area in the Hue app first.")
        self.context.run(work, loaded, message="Loading Entertainment areas…")

    def choose_area(self, index):
        self.editor._texts["area"].setText(self.areas.itemData(index) or "")


    def _set_status(self, message):
        super()._set_status(message)
        self.apply_button.setText("Update sync" if self.context.is_running() else "Start sync")
