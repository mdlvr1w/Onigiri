"""App shell: left nav, canvas, docked settings, scene strip."""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy

from PyQt6.QtCore import QPoint, QSize, QTimer, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMenu,
    QStyle,
)

from onigiri import asset_path
from onigiri.autostart import write_autostart
from onigiri.kwin import (
    KWinRules,
    close_stray_terminals,
)
from onigiri.launch import (
    activate_app,
    load_applications,
    spawn,
)
from onigiri.extensions import apply_scene, list_extensions, payload_from_scene, extension_status, stop_extensions, create_extension_page
from onigiri.model import Config, Profile, Scene

_NAV_ITEMS = (
    ("Scenes", ("view-preview", "folder-pictures", "image-x-generic"), QStyle.StandardPixmap.SP_FileDialogContentsView),
    ("Tiles", ("view-split-left-right", "object-columns", "window"), QStyle.StandardPixmap.SP_FileDialogListView),
    ("Extensions", ("plugins", "application-x-addon", "applications-other"), QStyle.StandardPixmap.SP_FileDialogInfoView),
    ("Settings", ("settings-configure", "configure", "preferences-system"), QStyle.StandardPixmap.SP_FileDialogDetailedView),
)


def _theme_icon(names: tuple[str, ...], fallback: QStyle.StandardPixmap) -> QIcon:
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    app = QApplication.instance()
    if app is not None:
        return app.style().standardIcon(fallback)
    return QIcon()
from onigiri.palette import dominant_colors, slot_colors
from onigiri.playlist import interval_ms
from onigiri.hotkey import GlobalHotkey, parse_hotkey
from onigiri.scenes import Scenes
from onigiri.session import Session
from onigiri.tiler import Tiler
from onigiri.ui.jobs import SceneJobs
from onigiri.ui.canvas import LayoutCanvas, MonitorView, monitor_at
from onigiri.ui.filmstrip import Filmstrip
from onigiri.ui.hotkey_edit import HotkeyBuilder
from onigiri.ui.overlay import SidePanel
from onigiri.ui.scene_dialog import SceneDialog
from onigiri.ui.popup import CursorPopup, cursor_point
from onigiri.ui.rules_dialog import RulesDialog
from onigiri.ui.screens import screen_list
from onigiri.ui.blur import enable_background_blur
from onigiri.ui.extension_page import ExtensionPageContext
from onigiri.ui.theme import BareStack, Panel, Shell, wrap_panel
from onigiri.wallpaper import color_hex, wallpaper_for, set_scene_wallpaper


class MainWindow(QMainWindow):
    def __init__(
        self,
        config_path: Path | None = None,
        kwin_path: Path | None = None,
        autostart_file: Path | None = None,
        reconfigure_fn=None,
        close_windows_fn=None,
        place_windows_fn=None,
        running_names_fn=None,
        set_wallpaper_fn=None,
        apply_colors_fn=None,
    ) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setWindowTitle("Onigiri")
        self.resize(1180, 760)
        self.setMinimumSize(900, 600)
        self._scene_id = ""
        self.autostart_file = autostart_file
        self.session = Session(config_path)
        self.kwin = KWinRules(kwin_path, reconfigure_fn=reconfigure_fn)
        self._close_stray_terminals = close_stray_terminals
        self._running_names = running_names_fn
        self.tiler = Tiler(
            self.session,
            self.kwin,
            close_windows_fn=close_windows_fn,
            place_windows_fn=place_windows_fn,
            running_names_fn=running_names_fn,
            spawn_fn=lambda command, use_shell: spawn(command, use_shell),
            activate_fn=lambda command, use_shell, app_id="": activate_app(command, use_shell, app_id),
            reconfigure_later=lambda delay, fn: QTimer.singleShot(delay, fn),
            place_later=lambda delay, items: QTimer.singleShot(delay, lambda pending=items: self.tiler._place_windows(pending)),
        )
        self.scenes = Scenes(
            self.session,
            set_wallpaper_fn=set_wallpaper_fn,
            apply_colors_fn=apply_colors_fn,
        )
        self._extension_pages = {}
        self._extension_contexts = {}
        self._extension_current = None
        self._plugin_infos = {}
        self._scene_jobs = SceneJobs(self)
        QApplication.instance().aboutToQuit.connect(self._scene_jobs.shutdown)
        self._scene_request = 0
        self._scene_applying = False
        self._quitting = False
        self._loading = False
        self._scene_timer = QTimer(self)
        self._scene_timer.setSingleShot(True)
        self._scene_timer.timeout.connect(self._playlist_tick)

        self._build_ui()
        self._build_tray()
        self._extension_timer = QTimer(self)
        self._extension_timer.timeout.connect(self._poll_extension_status)
        self._extension_timer.start(1000)
        self._popup = CursorPopup()
        self._popup.overlayChanged.connect(self._on_overlay_changed)
        self._popup.closed.connect(self._on_popup_closed)
        self._popup.tileRemoved.connect(self._on_overlay_tile_removed)
        self._hotkey = GlobalHotkey(self)
        self._hotkey.pressed.connect(self.show_cursor_popup)
        self._ensure_initial_profile()
        self.monitor_name = self._preferred_monitor()
        self.refresh()
        self._on_nav(self.nav_list.currentRow())
        if not self.session.load_error:
            self.apply_rules()
            self.apply_overlay_rules()
        self._hotkey.set_spec(parse_hotkey(self.config.hotkey))
        if self.session.load_error:
            self.statusBar().showMessage(self.session.load_error)

    @property
    def config_path(self):
        return self.session.config_path

    @config_path.setter
    def config_path(self, value) -> None:
        self.session.config_path = value

    @property
    def config(self):
        return self.session.config

    @config.setter
    def config(self, value) -> None:
        self.session.config = value

    @property
    def undo_stack(self):
        return self.session.undo_stack

    @undo_stack.setter
    def undo_stack(self, value) -> None:
        self.session.undo_stack = value

    @property
    def redo_stack(self):
        return self.session.redo_stack

    @property
    def profile_index(self) -> int:
        return self.session.profile_index

    @profile_index.setter
    def profile_index(self, value: int) -> None:
        self.session.profile_index = value

    @property
    def monitor_name(self) -> str:
        return self.session.monitor_name

    @monitor_name.setter
    def monitor_name(self, value: str) -> None:
        self.session.monitor_name = value

    @property
    def selected_ids(self) -> list[str]:
        return self.session.selected_ids

    @selected_ids.setter
    def selected_ids(self, value: list[str]) -> None:
        self.session.selected_ids = value

    @property
    def _coalesce(self):
        return self.session._coalesce

    @_coalesce.setter
    def _coalesce(self, value) -> None:
        self.session._coalesce = value

    @property
    def _playlist_index(self) -> int:
        return self.scenes.playlist_index

    @_playlist_index.setter
    def _playlist_index(self, value: int) -> None:
        self.scenes.playlist_index = value

    @property
    def _close_windows(self):
        return self.tiler._close_windows

    @property
    def _place_windows(self):
        return self.tiler._place_windows

    @property
    def selected_tile_id(self) -> str | None:
        return self.selected_ids[-1] if self.selected_ids else None

    @selected_tile_id.setter
    def selected_tile_id(self, value: str | None) -> None:
        self.selected_ids = [value] if value else []

    def _build_ui(self) -> None:
        icon = QIcon(str(asset_path("onigiri_icon.png")))
        if not icon.isNull():
            self.setWindowIcon(icon)

        root = Shell()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(8, 8, 8, 8)
        shell.setSpacing(8)
        shell.addWidget(self._build_nav())
        shell.addWidget(self._build_content(), 1)

        self.nav_list.currentRowChanged.connect(self._on_nav)
        self.playlist_bar.playToggled.connect(self._toggle_playlist)
        self.playlist_bar.settingsChanged.connect(self._on_playlist_settings)
        self.filmstrip.createRequested.connect(self._create_scene)
        self.filmstrip.sceneClicked.connect(self._on_scene_clicked)
        self.filmstrip.reordered.connect(self._on_scenes_reordered)
        self.gap_spin.valueChanged.connect(self._on_gap)
        self.layout_gap_spin.valueChanged.connect(self._on_layout_gap)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.apply_button.clicked.connect(lambda: self.launch_tiles())
        self.canvas.tilesSelected.connect(self._on_tiles_selected)
        self.canvas.splitRequested.connect(self._split)
        self.canvas.combineRequested.connect(self._combine)
        self.canvas.tileRequested.connect(self.add_first_tile)
        self.canvas.slotRequested.connect(self.fill_empty_slot)
        self.canvas.tileOpened.connect(self._open_tile)
        self.canvas.deleteRequested.connect(self.remove_tile)
        self.canvas.editBegan.connect(self._on_drag_began)
        self.canvas.editEnded.connect(self._on_drag_ended)
        self.canvas.monitorActivated.connect(self._activate_monitor)
        self.inspector.aboutToEdit.connect(self._on_about_to_edit)
        self.inspector.edited.connect(self._on_inspector_edited)
        self.inspector.launchRequested.connect(self._launch_selected_tile)
        self.overlay.scene_panel.changed.connect(self._on_scene_panel_changed)
        self.overlay.scene_panel.pictureRequested.connect(self._change_scene_picture)
        self.overlay.scene_panel.deleteRequested.connect(self._delete_selected_scene)
        self.overlay.scene_panel.testRequested.connect(self._test_scene_plugin)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_row)
        self.profile_combo.customContextMenuRequested.connect(self._profile_menu)
        self.layout_combo.currentIndexChanged.connect(self._on_layout_row)
        self.layout_combo.customContextMenuRequested.connect(self._layout_menu)
        self.hotkey_edit.changed.connect(self._on_hotkey_changed)
        self._apps = load_applications()
        self.inspector.set_apps(self._apps)

        self._undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self._redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self._undo_shortcut.activated.connect(self.undo)
        self._redo_shortcut.activated.connect(self.redo)
        self._save_shortcut.activated.connect(self.save_layout)

        for key, parent, slot in (
            (Qt.Key.Key_Delete, self.profile_combo, self.delete_current_profile),
            (Qt.Key.Key_F2, self.profile_combo, self.prompt_rename_profile),
            (Qt.Key.Key_Delete, self.layout_combo, self.delete_current_layout),
            (Qt.Key.Key_F2, self.layout_combo, self.prompt_rename_layout),
        ):
            shortcut = QShortcut(QKeySequence(key), parent)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(slot)

    def _wrap_panel(self, child: QWidget) -> Panel:
        return wrap_panel(child)

    def _build_nav(self) -> Panel:
        rail = Panel()
        rail.setFixedWidth(184)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(14, 16, 14, 16)
        layout.setSpacing(12)
        brand_row = QHBoxLayout()
        mark = QLabel()
        icon = self.windowIcon()
        if not icon.isNull():
            mark.setPixmap(icon.pixmap(28, 28))
        title = QLabel("Onigiri")
        title.setObjectName("brand")
        brand_row.addWidget(mark)
        brand_row.addWidget(title)
        brand_row.addStretch(1)
        layout.addLayout(brand_row)
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self.nav_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.nav_list.setIconSize(QSize(18, 18))
        self.nav_list.setSpacing(2)
        for name, icons, fallback in _NAV_ITEMS:
            self.nav_list.addItem(QListWidgetItem(_theme_icon(icons, fallback), name))
        self.nav_list.setCurrentRow(0)
        layout.addWidget(self.nav_list, 1)
        return rail

    def _build_content(self) -> QWidget:
        content = QWidget()
        content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        content.setAutoFillBackground(False)
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        self.undo_button = QPushButton("Undo")
        self.undo_button.setToolTip("Undo (Ctrl+Z)")
        self.redo_button = QPushButton("Redo")
        self.redo_button.setToolTip("Redo (Ctrl+Shift+Z)")
        self.apply_button = QPushButton("Apply layout")
        self.apply_button.setObjectName("primary")
        self.apply_button.setToolTip("Place existing windows and launch apps assigned to this layout")
        self.launch_button = self.apply_button
        self.new_scene_button = QPushButton("New scene")
        self.new_scene_button.setToolTip("Create a background and lighting scene")
        self.new_scene_button.clicked.connect(self._create_scene)
        self.apply_scene_button = QPushButton("Apply")
        self.apply_scene_button.setObjectName("primary")
        self.apply_scene_button.setToolTip("Apply the selected scene’s background and extensions")
        self.apply_scene_button.clicked.connect(self._apply_selected_scene)
        self.apply_scene_button.setEnabled(False)
        self.page_title = QLabel("Scenes")
        self.page_title.setObjectName("brand")

        canvas_bar = QWidget()
        canvas_row = QHBoxLayout(canvas_bar)
        canvas_row.setContentsMargins(0, 0, 0, 0)
        canvas_row.setSpacing(8)
        canvas_row.addWidget(self.page_title)
        canvas_row.addStretch(1)
        canvas_row.addWidget(self.undo_button)
        canvas_row.addWidget(self.redo_button)
        side_bar = QWidget()
        self.page_actions = side_bar
        side_row = QHBoxLayout(side_bar)
        side_row.setContentsMargins(0, 0, 0, 0)
        side_row.setSpacing(8)
        side_row.addStretch(1)
        side_row.addWidget(self.new_scene_button)
        side_row.addWidget(self.apply_scene_button)
        side_row.addWidget(self.apply_button)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(canvas_bar, 1)
        header.addWidget(side_bar)
        column.addLayout(header)

        self.context_label = QLabel()
        self.context_label.setObjectName("muted")
        self.context_label.setWordWrap(True)
        column.addWidget(self.context_label)
        self.page_hint = QLabel("Select a scene below, then click Apply for its background and lighting. Scene edits are saved automatically.")
        self.page_hint.setObjectName("muted")
        self.page_hint.setWordWrap(True)
        column.addWidget(self.page_hint)
        self.scene_state = QLabel("No scene applied this session")
        self.scene_state.setObjectName("muted")
        self.scene_state.setWordWrap(True)

        self.canvas = LayoutCanvas()
        self.canvas.setMinimumWidth(200)
        self.canvas.setObjectName("canvas")
        self.overlay = SidePanel()
        self.inspector = self.overlay.inspector
        self.gap_spin = self.inspector.gap_spin
        self.right_stack = BareStack()
        self.right_stack.setObjectName("rightStack")
        self.right_stack.setAutoFillBackground(False)
        self.scene_editor_page = QWidget()
        self.scene_editor_layout = QVBoxLayout(self.scene_editor_page)
        self.scene_editor_layout.setContentsMargins(0, 0, 0, 0)
        self.scene_editor_layout.addWidget(self.overlay)
        self.right_stack.addWidget(self.scene_editor_page)
        self.right_stack.addWidget(self._build_tiles_page())
        self.right_stack.addWidget(self._build_plugins_page())
        self.right_stack.addWidget(self._build_settings_page())
        right_panel = self._wrap_panel(self.right_stack)
        self.right_panel = right_panel
        right_panel.setFixedWidth(300)

        self.filmstrip = Filmstrip()
        self.playlist_bar = self.filmstrip.playlist_bar
        self.strip_panel = self._wrap_panel(self.filmstrip)

        self.canvas_workspace = QWidget()
        canvas_column = QVBoxLayout(self.canvas_workspace)
        canvas_column.setContentsMargins(0, 0, 0, 0)
        canvas_column.setSpacing(8)
        canvas_column.addWidget(self._wrap_panel(self.canvas), 1)
        canvas_column.addWidget(self.playlist_bar)

        work = QHBoxLayout()
        work.setSpacing(8)
        work.addWidget(self.canvas_workspace, 1)
        work.addWidget(right_panel)
        column.addLayout(work, 1)
        column.addWidget(self.scene_state)
        column.addWidget(self.strip_panel)
        return content

    def _page_block(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("sidePage")
        page.setAutoFillBackground(False)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        caption = QLabel(title)
        caption.setObjectName("section")
        layout.addWidget(caption)
        return page, layout

    def _build_tiles_page(self) -> QWidget:
        page, layout = self._page_block("PROFILE")
        self.profile_combo = QComboBox()
        self.layout_combo = QComboBox()
        self.profile_actions = QToolButton()
        self.layout_actions = QToolButton()
        for selector, actions, kind, create, rename, delete in (
            (self.profile_combo, self.profile_actions, "profile", self.prompt_new_profile,
             self.prompt_rename_profile, self.delete_current_profile),
            (self.layout_combo, self.layout_actions, "layout", self.prompt_new_layout,
             self.prompt_rename_layout, self.delete_current_layout),
        ):
            selector.setMinimumContentsLength(1)
            selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            selector.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            selector.setAccessibleName(f"Current {kind}")
            actions.setText("Manage")
            actions.setAccessibleName(f"Manage {kind}s")
            actions.setToolTip(f"Create, rename or delete {kind}s")
            actions.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(actions)
            menu.addAction(f"New {kind}", create)
            menu.addAction(f"Rename {kind}", rename)
            menu.addAction(f"Delete {kind}", delete)
            actions.setMenu(menu)
        for index, (selector, actions) in enumerate((
            (self.profile_combo, self.profile_actions), (self.layout_combo, self.layout_actions)
        )):
            if index:
                caption = QLabel("LAYOUT")
                caption.setObjectName("section")
                layout.addWidget(caption)
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(selector, 1)
            row.addWidget(actions)
            layout.addLayout(row)
        self.layout_gap_spin = QSpinBox()
        self.layout_gap_spin.setRange(0, 200)
        self.layout_gap_spin.setPrefix("Gap: ")
        self.layout_gap_spin.setSuffix(" px")
        self.layout_gap_spin.setToolTip("Space between tiles on the selected screen")
        layout.addWidget(self.layout_gap_spin)
        self.layout_editor_layout = layout
        return page

    def _build_plugins_page(self) -> QWidget:
        self.extension_stack = BareStack()
        overview, layout = self._page_block("EXTENSIONS")
        self.plugin_list = QListWidget()
        self.plugin_list.setObjectName("navList")
        self.plugin_list.itemClicked.connect(lambda item: self._open_extension(item.data(Qt.ItemDataRole.UserRole)))
        self.plugin_list.itemActivated.connect(lambda item: self._open_extension(item.data(Qt.ItemDataRole.UserRole)))
        hint = QLabel("Select an extension to open its controls. These work independently of scene settings.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self.plugin_list, 1)
        self.extension_stack.addWidget(overview)
        detail = QWidget()
        column = QVBoxLayout(detail)
        column.setContentsMargins(12, 12, 12, 12)
        back = QPushButton("← All extensions")
        back.clicked.connect(self._show_extensions)
        back.setMaximumWidth(180)
        column.addWidget(back)
        self.extension_page_stack = BareStack()
        column.addWidget(self.extension_page_stack, 1)
        self.extension_stack.addWidget(detail)
        return self.extension_stack

    def _show_extensions(self) -> None:
        self._extension_current = None
        self.extension_stack.setCurrentIndex(0)
        if self.nav_list.currentRow() == 2:
            self.page_title.setText("Extensions")

    def _open_extension(self, stem) -> None:
        info = self._plugin_infos.get(stem)
        if info is None:
            return
        if self.nav_list.currentRow() != 2:
            self.nav_list.setCurrentRow(2)
        if stem not in self._extension_pages:
            context = ExtensionPageContext(info, self._scene_jobs, self.statusBar().showMessage,
                                           open_callback=self._open_extension)
            self._extension_contexts[stem] = context
            try:
                page = create_extension_page(info, context)
                if not isinstance(page, QWidget):
                    raise TypeError("create_page(context) must return a QWidget")
            except Exception as error:
                page = QLabel(f"This extension’s page could not be opened: {error}")
                page.setTextFormat(Qt.TextFormat.PlainText)
                page.setWordWrap(True)
            scroll = QScrollArea()
            scroll.setObjectName("extensionPageScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setAutoFillBackground(False)
            scroll.viewport().setAutoFillBackground(False)
            scroll.setWidget(page)
            page.setAutoFillBackground(False)
            self.extension_page_stack.addWidget(scroll)
            self._extension_pages[stem] = (scroll, page)
        scroll, page = self._extension_pages[stem]
        self.extension_page_stack.setCurrentWidget(scroll)
        self.extension_stack.setCurrentIndex(1)
        self._extension_current = stem
        self.page_title.setText(f"Extensions · {info.title}")
        on_show = getattr(page, "on_show", None)
        if callable(on_show):
            try:
                on_show()
            except Exception as error:
                self._extension_contexts[stem].set_status(str(error))

    def _build_settings_page(self) -> QWidget:
        page, layout = self._page_block("SETTINGS")
        self.hotkey_edit = HotkeyBuilder()
        self.autostart_button = QPushButton("Start at login")
        self.autostart_button.clicked.connect(lambda: self.create_autostart())
        self.autostart_button.setMaximumWidth(360)
        self.rules_button = QPushButton("Window rules")
        self.rules_button.setMaximumWidth(360)
        self.rules_button.clicked.connect(self.show_rules)
        layout.addWidget(QLabel("Open the quick tiler"))
        layout.addWidget(self.hotkey_edit)
        layout.addWidget(self.autostart_button)
        layout.addWidget(self.rules_button)
        layout.addStretch(1)
        return page

    def _on_nav(self, row: int) -> None:
        names = ("Scenes", "Tiles", "Extensions", "Settings")
        self.page_title.setText(names[row] if 0 <= row < len(names) else "Onigiri")
        if row == 1:
            self.layout_editor_layout.addWidget(self.overlay, 1)
        elif row == 0:
            self.scene_editor_layout.addWidget(self.overlay)
        self.right_stack.setCurrentIndex(max(0, row))
        canvas_pages = row <= 1
        self.canvas_workspace.setVisible(canvas_pages)
        self.right_panel.setMinimumWidth(300 if canvas_pages else 0)
        self.right_panel.setMaximumWidth(300 if canvas_pages else 16777215)
        self.context_label.setVisible(row == 1)
        self.scene_state.setVisible(row == 0)
        hints = (
            "Select a scene below, then click Apply for its background and lighting. Scene edits are saved automatically.",
            "Choose a profile and layout, then edit its tiles. Edits are saved automatically. Apply layout places windows and launches apps.",
            "Open an extension to control its hardware directly, independently of scenes.",
            "Configure the quick tiler and desktop integration.",
        )
        self.page_hint.setText(hints[row] if 0 <= row < len(hints) else "")
        self.filmstrip.setVisible(row == 0)
        self.strip_panel.setVisible(row == 0)
        layout_page = row == 1
        scene_page = row == 0
        self.apply_button.setVisible(layout_page)
        self.undo_button.setVisible(layout_page)
        self.redo_button.setVisible(layout_page)
        self.new_scene_button.setVisible(scene_page)
        self.apply_scene_button.setVisible(scene_page)
        self.page_actions.setVisible(layout_page or scene_page)
        for shortcut in (self._undo_shortcut, self._redo_shortcut, self._save_shortcut):
            shortcut.setEnabled(layout_page)
        self._sync_playlist()
        self._sync_overlay()
        if canvas_pages:
            self._sync_canvas()
        self.centralWidget().update()
        if row == 2:
            self._fill_plugins()
            context = self._extension_contexts.get(self._extension_current)
            if context is not None:
                self.page_title.setText(f"Extensions · {context.info.title}")

    def _profile_menu(self, pos) -> None:
        self.profile_actions.menu().exec(self.profile_combo.mapToGlobal(pos))

    def _layout_menu(self, pos) -> None:
        self.layout_actions.menu().exec(self.layout_combo.mapToGlobal(pos))

    def _open_tile(self, tile_id: str) -> None:
        self._on_tiles_selected([tile_id] if tile_id else [])
        self.inspector.name_edit.setFocus()
        self.inspector.name_edit.selectAll()

    def _poll_extension_status(self) -> None:
        for message in extension_status():
            self.statusBar().showMessage(message)
            stem, _, status = message.partition(": ")
            panel = self.overlay.scene_panel.panels.get(stem)
            if panel is not None:
                panel.status.setText(status)
            context = self._extension_contexts.get(stem)
            if context is not None:
                context.set_status(status)

    def _stop_effects(self) -> None:
        self._scene_jobs.submit(stop_extensions, lambda errors: self.statusBar().showMessage(
            "; ".join(errors) if errors else "Continuous effects stopped. Apply a scene to restart."))

    def _build_tray(self) -> None:
        icon = self.windowIcon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Onigiri")
        menu = QMenu()
        menu.addAction("Show Onigiri", self._show_from_tray)
        menu.addAction("Stop continuous effects", self._stop_effects)
        menu.addAction("Quit", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _ensure_initial_profile(self) -> None:
        self.session.ensure_initial_profile()

    def _preferred_monitor(self) -> str:
        return self.session.preferred_monitor()

    def current_profile(self) -> Profile | None:
        return self.session.current_profile()

    def current_layout(self):
        return self.session.current_layout()

    def current_screen(self):
        return self.session.current_screen()

    def current_area(self):
        return self.session.current_area()

    def _remember_geometry(self) -> None:
        self.session.remember_geometry()

    def save(self) -> None:
        self.session.save()

    def save_layout(self) -> None:
        self._remember_geometry()
        self.save()
        self.statusBar().showMessage(f"Saved layout to {self.session.save_path()}")

    def discard_edits(self) -> None:
        if not self.undo_stack:
            return
        snapshot = self.undo_stack[0]
        self.session.undo_stack = []
        self.session.redo_stack = []
        self.session._coalesce = None
        self.config = Config.from_dict(snapshot)
        self.save()
        self.refresh()
        self.statusBar().showMessage("Discarded edits.")

    def push_undo(self, coalesce: str | None = None) -> None:
        self.session.push_undo(coalesce)
        self._update_undo_buttons()

    def undo(self) -> None:
        if self.session.undo():
            self.refresh()

    def redo(self) -> None:
        if self.session.redo():
            self.refresh()

    def _update_undo_buttons(self) -> None:
        self.undo_button.setEnabled(bool(self.undo_stack))
        self.redo_button.setEnabled(bool(self.redo_stack))

    def _clamp_indexes(self) -> None:
        self.session.clamp_indexes()

    def _ensure_selection(self) -> None:
        known = set(self._tiles_by_id())
        self.selected_ids = [tile_id for tile_id in self.selected_ids if tile_id in known]

    def refresh(self) -> None:
        self._loading = True
        try:
            self._clamp_indexes()
            self._ensure_selection()
            self._fill_profiles()
            self._fill_monitors()
            self._fill_layouts()
            profile = self.current_profile()
            screen = self.current_screen()
            gap = screen.gap if screen is not None else 8
            self._sync_gap_spins(gap)
            self.setWindowTitle("Onigiri" if profile is None else f"Onigiri — {profile.name}")
            self.context_label.setText(
                "No profile selected" if profile is None
                else f"Profile: {profile.name}  →  Layout: {profile.current}"
            )
            self._sync_canvas()
            self._sync_overlay()
            self._sync_hotkey_edit()
            self._sync_scenes()
            self._fill_plugins()
            self._update_undo_buttons()
            has_profile = profile is not None
            self.apply_button.setEnabled(has_profile)
            self.gap_spin.setEnabled(has_profile)
            self.layout_gap_spin.setEnabled(has_profile)
        finally:
            self._loading = False

    def _fill_profiles(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self.config.profiles:
            self.profile_combo.addItem(profile.name)
        if 0 <= self.profile_index < self.profile_combo.count():
            self.profile_combo.setCurrentIndex(self.profile_index)
        self.profile_combo.setToolTip(self.profile_combo.currentText())
        self.profile_combo.blockSignals(False)

    def _fill_monitors(self) -> None:
        return

    def _fill_layouts(self) -> None:
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        profile = self.current_profile()
        current = -1
        if profile is not None:
            for name in profile.layouts:
                self.layout_combo.addItem(name)
                if name == profile.current:
                    current = self.layout_combo.count() - 1
        if current >= 0:
            self.layout_combo.setCurrentIndex(current)
        self.layout_combo.setToolTip(self.layout_combo.currentText())
        self.layout_combo.blockSignals(False)

    def _fill_plugins(self) -> None:
        self.plugin_list.clear()
        infos = list_extensions()
        self._plugin_infos = {info.stem: info for info in infos}
        for info in infos:
            label = info.title if not info.error else f"{info.title} (error)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, info.stem)
            item.setToolTip("Open extension controls")
            self.plugin_list.addItem(item)
        if not infos:
            self.plugin_list.addItem("No extensions installed.")

    def _layout_screens(self, profile=None):
        return self.session.layout_screens(profile)

    def _sync_canvas(self) -> None:
        profile = self.current_profile()
        views: list[MonitorView] = []
        screens = [screen for screen in screen_list() if screen.connected]
        stored = self._layout_screens(profile)
        sources = (
            [(screen.name, screen.x, screen.y, screen.width, screen.height) for screen in screens]
            if screens
            else [(name, state.x, state.y, state.width, state.height) for name, state in stored.items()]
        )
        wallpapers = wallpaper_for(sources)
        selected_scene = self._scene_by_id(self._scene_id) if self.nav_list.currentRow() == 0 else None
        if selected_scene is not None:
            source = (color_hex(selected_scene.background_color)
                      if selected_scene.background_color is not None else selected_scene.wallpaper)
            if source:
                wallpapers = {name: (source, "crop") for name, *_ in sources}
        if screens:
            for screen in screens:
                state = stored.get(screen.name)
                path, fill = wallpapers.get(screen.name, ("", "crop"))
                views.append(
                    MonitorView(
                        name=screen.name,
                        x=screen.x,
                        y=screen.y,
                        width=screen.width,
                        height=screen.height,
                        root=None if state is None else state.root,
                        background=path,
                        fill=fill,
                        active=screen.name == self.monitor_name,
                        gap=8 if state is None else state.gap,
                    )
                )
        elif profile is not None:
            for name, state in stored.items():
                path, fill = wallpapers.get(name, ("", "crop"))
                views.append(
                    MonitorView(
                        name=name,
                        x=state.x,
                        y=state.y,
                        width=state.width,
                        height=state.height,
                        root=state.root,
                        background=path,
                        fill=fill,
                        active=name == self.monitor_name,
                        gap=state.gap,
                    )
                )
        self.canvas.set_monitors(views, selected=self.selected_ids)

    def _activate_monitor(self, name: str) -> None:
        if self._loading or not name:
            return
        if name == self.monitor_name:
            self._sync_overlay()
            return
        self.monitor_name = name
        self._loading = True
        try:
            self._fill_layouts()
            self._sync_canvas()
            self._sync_overlay()
        finally:
            self._loading = False

    def _tiles_for_launch(self):
        return self.tiler.tiles_for_launch()

    def _duplicate_set(self) -> set[str]:
        return self.tiler.duplicate_set()

    def _tiles_by_id(self):
        return self.tiler.tiles_by_id()

    def _sync_inspector(self) -> None:
        self._sync_overlay()

    def _sync_overlay(self) -> None:
        profile = self.current_profile()
        screen = self.current_screen()
        has_tree = screen is not None and screen.root is not None
        by_id = self._tiles_by_id()
        tiles = [by_id[tile_id] for tile_id in self.selected_ids if tile_id in by_id]
        self.inspector.set_selection(
            tiles,
            profile.name if profile else "",
            self._duplicate_set(),
            screen.gap if tiles and has_tree else None,
        )
        if tiles:
            self.overlay.ensure("tile")
            return
        if self.nav_list.currentRow() == 1:
            self.overlay.hint.setText("Select a tile to assign an app or terminal command. Right-click a tile to split or combine it.")
            self.overlay.ensure("hint")
            return
        self.overlay.hint.setText("Select a scene below to edit its background and lighting, or select a tile to edit its app.")
        scene = self._scene_by_id(self._scene_id) if self._scene_id else None
        if scene is not None:
            self.overlay.scene_panel.load(scene)
            self.overlay.ensure("scene")
            return
        self.overlay.ensure("hint")

    def _on_profile_row(self, row: int) -> None:
        if self._loading or row < 0 or row == self.profile_index:
            return
        self.profile_index = row
        self.selected_tile_id = None
        self.monitor_name = self._preferred_monitor()
        self.refresh()

    def _on_layout_row(self, row: int) -> None:
        if self._loading or row < 0:
            return
        profile = self.current_profile()
        if profile is None:
            return
        name = self.layout_combo.itemText(row)
        if not name or profile.current == name or name not in profile.layouts:
            return
        if self.session.set_current_layout(name):
            self.refresh()

    def _sync_hotkey_edit(self) -> None:
        self.hotkey_edit.blockSignals(True)
        self.hotkey_edit.set_text(self.config.hotkey)
        self.hotkey_edit.blockSignals(False)

    def _scene_by_id(self, scene_id: str) -> Scene | None:
        return self.scenes.by_id(scene_id)

    def _apply_selected_scene(self) -> None:
        scene = self._scene_by_id(self._scene_id)
        if scene is not None:
            self._apply_scene(scene)

    def _create_scene(self) -> None:
        dialog = SceneDialog(parent=self, screens=screen_list(usable=False))
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.deleted:
            return
        scene = dialog.built()
        self.scenes.append(scene)
        self._select_scene(scene, apply=True)

    def _on_scene_clicked(self, scene_id: str) -> None:
        scene = self._scene_by_id(scene_id)
        if scene is None:
            return
        self._select_scene(scene)
        self._sync_canvas()

    def _select_scene(self, scene: Scene, *, apply: bool = False) -> None:
        self._scene_id = scene.id
        self.selected_ids = []
        self.canvas.set_selected([])
        self.filmstrip.set_selected(scene.id)
        if apply:
            self._apply_scene(scene)
        self._sync_overlay()
        self._sync_scenes()

    def _on_scene_panel_changed(self) -> None:
        if self._loading:
            return
        built = self.overlay.scene_panel.built()
        current = self._scene_by_id(built.id)
        if current is None:
            return
        self.scenes.replace_or_delete(current, built, False)
        self.filmstrip.set_selected(built.id)
        self._paint_scene(built)
        self._sync_scenes()
        self._sync_canvas()

    def _change_scene_picture(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Choose picture", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)")
        if not path:
            return
        blue, purple = slot_colors(dominant_colors(path))
        self.overlay.scene_panel.set_wallpaper(path)
        built = self.overlay.scene_panel.built()
        if blue is not None:
            built.blue = blue
        if purple is not None:
            built.purple = purple
        current = self._scene_by_id(built.id)
        if current is None:
            return
        self.scenes.replace_or_delete(current, built, False)
        self._apply_scene(built)
        self.overlay.scene_panel.load(built)
        self._sync_scenes()

    def _delete_selected_scene(self) -> None:
        scene = self._scene_by_id(self._scene_id)
        if scene is None:
            return
        answer = QMessageBox.question(self, "Delete scene", f"Delete {scene.name}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.scenes.replace_or_delete(scene, None, True)
        self._stop_effects()
        self._scene_id = ""
        self.overlay.ensure("hint")
        self._sync_scenes()

    def _test_scene_plugin(self, stem: str) -> None:
        panel = self.overlay.scene_panel.panels.get(stem)
        if panel is None or not panel.is_checked():
            return
        built = self.overlay.scene_panel.built()
        payload = payload_from_scene(built)
        payload.plugins = {stem: panel.values()}
        panel.status.setText("Updating…")
        self._scene_jobs.submit(
            lambda: apply_scene(payload),
            lambda errors: panel.status.setText("; ".join(errors) if errors else "Updated."),
        )

    def _on_scenes_reordered(self, ids: list[str]) -> None:
        self.scenes.reorder(ids)
        self._sync_scenes()

    def _sync_scenes(self) -> None:
        self.filmstrip.set_scenes(self.config.scenes)
        self.filmstrip.set_selected(self._scene_id)
        self.apply_scene_button.setEnabled(self._scene_by_id(self._scene_id) is not None)
        self._sync_playlist()

    def _sync_playlist(self) -> None:
        many = len(self.config.scenes) > 1
        scenes_page = self.nav_list.currentRow() == 0
        self.playlist_bar.setVisible(many and scenes_page)
        if not many and self.config.playlist.playing:
            self.config.playlist.playing = False
            if not self._loading:
                self.save()
        self.playlist_bar.set_playlist(self.config.playlist)
        self.centralWidget().update()
        self._arm_playlist_timer()

    def _on_playlist_settings(self) -> None:
        if self._loading:
            return
        playing = self.config.playlist.playing
        self.config.playlist = self.playlist_bar.playlist()
        self.config.playlist.playing = playing
        self.save()
        self._arm_playlist_timer()

    def _toggle_playlist(self) -> None:
        if len(self.config.scenes) < 2:
            return
        self.config.playlist.playing = not self.config.playlist.playing
        if self.config.playlist.playing:
            self._playlist_index = min(max(self._playlist_index, 0), len(self.config.scenes) - 1)
            self._apply_scene(self.config.scenes[self._playlist_index])
        self.save()
        self._sync_playlist()

    def _arm_playlist_timer(self) -> None:
        if len(self.config.scenes) < 2 or not self.config.playlist.playing or self._scene_applying:
            self._scene_timer.stop()
            return
        milliseconds = interval_ms(self.config.playlist.interval, self.config.playlist.unit)
        if self._scene_timer.isActive() and self._scene_timer.interval() == milliseconds:
            return
        self._scene_timer.start(milliseconds)

    def _playlist_tick(self) -> None:
        if not self.config.playlist.playing or self._scene_applying:
            return
        scene = self.scenes.tick()
        if scene is None:
            self._sync_playlist()
            return
        self._apply_scene(scene)

    def _push_wallpaper(self, scene: Scene) -> None:
        self.scenes.push_wallpaper(scene)

    def _apply_scene(self, scene: Scene) -> None:
        self._scene_request += 1
        request = self._scene_request
        snapshot = deepcopy(scene)
        # Resolve Qt screen objects on the UI thread, before dispatching work.
        screens = [(s.name, s.x, s.y, s.width, s.height) for s in self.scenes._screens()]

        def work():
            if self.scenes._set_wallpaper is not None:
                self.scenes.push_wallpaper(snapshot)
            else:
                set_scene_wallpaper(snapshot.wallpaper, screens, snapshot.wallpaper_pan, background_color=snapshot.background_color)
            return self.scenes.paint(snapshot)

        def done(errors):
            if request != self._scene_request:
                return
            try:
                if errors:
                    self.scene_state.setText(f"Scene needs attention: {snapshot.name}")
                    self.statusBar().showMessage(f"Scene {snapshot.name}: " + "; ".join(errors))
                    return
                self.scenes.playlist_index = next(
                    (i for i, item in enumerate(self.config.scenes) if item.id == snapshot.id), 0
                )
                self._scene_id = snapshot.id
                self.filmstrip.set_selected(snapshot.id)
                self._sync_canvas()
                self._sync_overlay()
                self.scene_state.setText(f"Applied scene: {snapshot.name}")
                self.statusBar().showMessage(f"Applied {snapshot.name}.")
            finally:
                # Both success and failure advance playback after a full interval.
                # Never enqueue another automatic scene while this one is applying.
                self._scene_applying = False
                self._arm_playlist_timer()

        self._scene_timer.stop()
        self._scene_applying = True

        self.scene_state.setText(f"Applying scene: {snapshot.name}…")
        self.statusBar().showMessage(f"Applying {snapshot.name}…")
        # Keep injected UI/test adapters on their caller's thread.
        if self.scenes._set_wallpaper is not None or self.scenes._apply_colors is not None:
            try:
                done(work())
            except Exception as error:
                done([str(error)])
        else:
            self._scene_jobs.submit(work, done)

    def _paint_scene(self, scene: Scene) -> None:
        snapshot = deepcopy(scene)

        def done(errors):
            if errors:
                self.statusBar().showMessage("Scene extensions: " + "; ".join(errors))

        if self.scenes._apply_colors is not None:
            done(self.scenes.paint(snapshot))
        else:
            self._scene_jobs.submit(lambda: self.scenes.paint(snapshot), done)

    def _on_hotkey_changed(self) -> None:
        if self._loading:
            return
        text = self.hotkey_edit.text()
        if text == self.config.hotkey:
            return
        self.session.set_hotkey(text)
        self._hotkey.set_spec(self.hotkey_edit.spec())

    def show_cursor_popup(self, pos=None) -> None:
        self._show_overlay(pos, fill_screen=True)

    def show_mini_popup(self, pos=None) -> None:
        self._show_overlay(pos, fill_screen=False)

    def _show_overlay(self, pos=None, fill_screen: bool = True) -> None:
        point = pos if isinstance(pos, QPoint) else cursor_point()
        self._sync_canvas()
        view = monitor_at(self.canvas.views, point)
        if view is None:
            screen = QGuiApplication.screenAt(point)
            name = screen.name() if screen is not None else ""
            view = next((item for item in self.canvas.views if item.name == name), None)
        profile = self.current_profile()
        overlay = None
        if profile is not None and view is not None:
            overlay = profile.overlays.get(view.name)
        self._popup.show_at(point, view, overlay, self._apps, fill_screen=fill_screen)

    def _on_overlay_changed(self, connector: str, layout) -> None:
        self.tiler.store_overlay(connector, layout)

    def _on_overlay_tile_removed(self, tile) -> None:
        self._close_assigned(tile, overlay=True)

    def _close_assigned(self, tile, *, overlay: bool = False) -> None:
        self.tiler.close_assigned(tile, overlay=overlay)

    def _on_popup_closed(self) -> None:
        if self._quitting:
            return
        if self.tiler.overlay_closed(self._popup.monitor_name):
            self.launch_overlay_tiles()

    def _sync_gap_spins(self, value: int) -> None:
        for spin in (self.gap_spin, self.layout_gap_spin):
            if spin.value() != value:
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)

    def _on_gap(self, value: int) -> None:
        if self._loading:
            return
        if self.tiler.set_gap(value):
            self._update_undo_buttons()
            self._sync_canvas()
        self._sync_gap_spins(value)

    def _on_layout_gap(self, value: int) -> None:
        self._on_gap(value)

    def _on_tiles_selected(self, tile_ids: list) -> None:
        self.selected_ids = [str(tile_id) for tile_id in tile_ids if tile_id]
        self.canvas.set_selected(self.selected_ids)
        self._sync_inspector()

    def _on_about_to_edit(self, key: str) -> None:
        self.push_undo(key or None)

    def _on_inspector_edited(self) -> None:
        self.inspector.set_duplicates(self._duplicate_set())
        self.inspector.refresh_preview()
        self._sync_canvas()
        self.save()

    def _on_drag_began(self) -> None:
        self.push_undo("drag")

    def _on_drag_ended(self) -> None:
        self._coalesce = None
        self.save()
        self._sync_canvas()

    def _ask_name(self, title: str, label: str, default: str = "") -> str | None:
        text, ok = QInputDialog.getText(self, title, label, text=default)
        if not ok:
            return None
        cleaned = text.strip()
        return cleaned or None

    def prompt_new_profile(self) -> None:
        name = self._ask_name("New profile", "Profile name")
        if name:
            self.create_profile(name)

    def create_profile(self, name: str) -> bool:
        error = self.session.create_profile(name)
        if error == "empty":
            return False
        if error:
            QMessageBox.warning(self, "Name in use", error)
            return False
        self._update_undo_buttons()
        self.refresh()
        return True

    def prompt_rename_profile(self) -> None:
        profile = self.current_profile()
        if profile is None:
            return
        name = self._ask_name("Rename profile", "Profile name", profile.name)
        if name:
            self.rename_profile(name)

    def rename_profile(self, name: str, *, immediate: bool = True) -> bool:
        profile = self.current_profile()
        cleaned = name.strip()
        error = self.session.rename_profile(name)
        if error == "empty":
            return False
        if error:
            QMessageBox.warning(self, "Name in use", error)
            return False
        self._update_undo_buttons()
        if immediate:
            self.refresh()
        else:
            QTimer.singleShot(0, self.refresh)
        return True

    def delete_current_profile(self) -> None:
        profile = self.current_profile()
        if profile is None:
            return
        answer = QMessageBox.question(self, "Delete profile", f"Delete profile {profile.name}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.session.delete_current_profile()
        self._update_undo_buttons()
        self.refresh()

    def prompt_new_layout(self) -> None:
        name = self._ask_name("New layout", "Layout name")
        if name:
            self.create_layout(name)

    def create_layout(self, name: str) -> bool:
        error = self.session.create_layout(name)
        if error == "empty":
            return False
        if error:
            QMessageBox.warning(self, "Name in use", error)
            return False
        self._update_undo_buttons()
        self.refresh()
        return True

    def prompt_rename_layout(self) -> None:
        profile = self.current_profile()
        if profile is None:
            return
        name = self._ask_name("Rename layout", "Layout name", profile.current)
        if name:
            self.rename_layout(name)

    def rename_layout(self, name: str, *, immediate: bool = True) -> bool:
        profile = self.current_profile()
        cleaned = name.strip()
        error = self.session.rename_layout(name)
        if error == "empty":
            return False
        if error:
            QMessageBox.warning(self, "Name in use", error)
            return False
        self._update_undo_buttons()
        if immediate:
            self.refresh()
        else:
            QTimer.singleShot(0, self.refresh)
        return True

    def delete_current_layout(self) -> None:
        profile = self.current_profile()
        if profile is None:
            return
        answer = QMessageBox.question(self, "Delete layout", f"Delete layout {profile.current}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.session.delete_current_layout()
        self._update_undo_buttons()
        self.refresh()

    def split_current(self, orientation: str) -> None:
        if self.selected_tile_id:
            self._split(self.selected_tile_id, orientation)

    def _split(self, tile_id: str, orientation: str) -> None:
        if self.tiler.split(tile_id, orientation) is not None:
            self._update_undo_buttons()
            self.refresh()
        else:
            self._update_undo_buttons()

    def _combine(self, tile_id: str) -> None:
        if self.tiler.combine(tile_id):
            self._update_undo_buttons()
            self.refresh()
        else:
            self._update_undo_buttons()

    def show_rules(self) -> None:
        RulesDialog(self.kwin, self).exec()

    def create_autostart(self, notify: bool = True):
        profile = self.current_profile()
        if profile is None:
            return None
        path = write_autostart(profile.name, self.autostart_file)
        self.statusBar().showMessage(f"Autostart written to {path}")
        if notify:
            QMessageBox.information(
                self,
                "Autostart created",
                f"When you log in, Onigiri will start in the tray, apply “{profile.name}”, and open its tiles.\n\n{path}",
            )
        return path

    def add_first_tile(self, monitor: str) -> None:
        if self.tiler.add_first_tile(monitor):
            self._update_undo_buttons()
            self.refresh()

    def remove_tile(self, tile_id: str) -> None:
        if self.tiler.remove_tile(tile_id):
            self._update_undo_buttons()
            self.refresh()
        else:
            self._update_undo_buttons()

    def fill_empty_slot(self, monitor: str, slot: str) -> None:
        if self.tiler.fill_empty_slot(monitor, slot):
            self._update_undo_buttons()
            self.refresh()
        else:
            self._update_undo_buttons()

    def clear_screen(self, monitor: str) -> None:
        if self.tiler.clear_screen(monitor):
            self._update_undo_buttons()
            self.refresh()

    def collect_placements(self):
        return self.tiler.collect_placements()

    def collect_overlay_placements(self):
        return self.tiler.collect_overlay_placements()

    def apply_rules(self) -> int:
        profile = self.current_profile()
        if profile is None:
            return 0
        try:
            count = self.tiler.apply_rules()
        except OSError as exc:
            QMessageBox.critical(self, "Could not write window rules", str(exc))
            return 0
        self.statusBar().showMessage(f"Placed {count} windows for {profile.name}.")
        return count

    def apply_overlay_rules(self) -> int:
        if self.current_profile() is None:
            return 0
        try:
            return self.tiler.apply_overlay_rules()
        except OSError as exc:
            QMessageBox.critical(self, "Could not write window rules", str(exc))
            return 0

    def launch_tiles(self, only_tile: str | None = None, only_tiles: list[str] | None = None) -> int:
        try:
            launched, error = self.tiler.launch_tiles(only_tile=only_tile, only_tiles=only_tiles)
        except OSError as exc:
            QMessageBox.critical(self, "Could not write window rules", str(exc))
            return 0
        if error:
            self.statusBar().showMessage(f"Launched {launched}. Could not start one command: {error}")
        else:
            self.statusBar().showMessage(f"Launched {launched} tiles.")
        return launched

    def launch_overlay_tiles(self, only_tiles: list[str] | None = None) -> int:
        try:
            launched, moved, error = self.tiler.launch_overlay_tiles(only_tiles=only_tiles)
        except OSError as exc:
            QMessageBox.critical(self, "Could not write window rules", str(exc))
            return 0
        if error:
            self.statusBar().showMessage(f"Launched {launched}. Could not start one command: {error}")
        elif launched and moved:
            self.statusBar().showMessage(f"Moved {moved} open windows and launched {launched}.")
        elif moved:
            self.statusBar().showMessage(f"Moved {moved} open windows.")
        else:
            self.statusBar().showMessage(f"Launched {launched} tiles.")
        return launched

    def _spawn_placements(self, launching) -> int:
        launched, error = self.tiler.spawn_placements(launching)
        if error:
            self.statusBar().showMessage(f"Launched {launched}. Could not start one command: {error}")
        else:
            self.statusBar().showMessage(f"Launched {launched} tiles.")
        return launched

    def _place_or_spawn(self, launching) -> int:
        launched, moved, error = self.tiler.place_or_spawn(launching)
        if error:
            self.statusBar().showMessage(f"Launched {launched}. Could not start one command: {error}")
        elif launched and moved:
            self.statusBar().showMessage(f"Moved {moved} open windows and launched {launched}.")
        elif moved:
            self.statusBar().showMessage(f"Moved {moved} open windows.")
        else:
            self.statusBar().showMessage(f"Launched {launched} tiles.")
        return launched

    def start_hidden(self) -> None:
        self.tray.show()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
        else:
            self.showMinimized()

    def launch_named(self, name: str, delay_ms: int = 0) -> bool:
        for index, profile in enumerate(self.config.profiles):
            if profile.name == name:
                self.profile_index = index
                self.selected_tile_id = None
                self.monitor_name = self._preferred_monitor()
                self.refresh()
                if delay_ms > 0:
                    QTimer.singleShot(delay_ms, lambda: self.launch_tiles())
                    QTimer.singleShot(delay_ms + 900, self._close_stray_terminals)
                else:
                    self.launch_tiles()
                    QTimer.singleShot(900, self._close_stray_terminals)
                return True
        self.statusBar().showMessage(f"Profile {name} was not found.")
        return False

    def _launch_selected_tile(self) -> None:
        if not self.selected_ids:
            self.statusBar().showMessage("Select a tile first.")
            return
        if len(self.selected_ids) == 1:
            self.launch_tiles(only_tile=self.selected_ids[0])
            return
        self.launch_tiles(only_tiles=self.selected_ids)

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.isVisible():
                self.hide()
            else:
                self._show_from_tray()

    def quit_app(self) -> None:
        self._quitting = True
        self.tray.hide()
        QApplication.quit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        enable_background_blur(self)

    def closeEvent(self, event) -> None:
        if self._quitting or not QSystemTrayIcon.isSystemTrayAvailable():
            self._quitting = True
            if not self.session.load_error:
                self.save()
            event.accept()
            QApplication.quit()
            return
        event.ignore()
        self.hide()
        self.statusBar().showMessage("Onigiri is still running in the tray.")
