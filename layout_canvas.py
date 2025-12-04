from typing import Any, Dict, List, Optional
import logging

from PyQt6.QtWidgets import QWidget, QMenu, QApplication
from PyQt6.QtCore import Qt, pyqtSignal, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QMouseEvent, QPixmap, QGuiApplication

from models import ProfileModel

logger = logging.getLogger(__name__)


class LayoutCanvas(QWidget):
    """
    Recursive split-based layout editor.

    - Internal representation is a binary tree of splits.
    - Leaves represent slots that can be assigned to tiles.
    - Splits are draggable edges; moving them adjusts child ratios.
    - No per-slot manual geometry; all rects are derived from the tree.
    """

    tileSelected = pyqtSignal(int)       # index in profile.tiles, or -1 if none
    geometryChanged = pyqtSignal(int)    # index in profile.tiles whose geometry changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

        self._profile: Optional[ProfileModel] = None
        self._gap: int = 0

        # Optional background image for the canvas
        self._background_pixmap: Optional[QPixmap] = None

        # minimal width/height for a leaf region in SCREEN pixels
        # (change this value if you want bigger/smaller minimum tiles)
        self._min_leaf_size: float = 10.0

        # Recursive tree root:
        #   leaf: {"type": "leaf", "id": int, "tile_name": str}
        #   split: {"type": "split", "orientation": "h"|"v", "ratio": float,
        #           "first": node, "second": node}
        self._root: Optional[dict] = None
        self._next_leaf_id: int = 0

        # Cached geometry derived from the tree
        self._leaf_rects: dict[int, dict] = {}   # id -> {"x","y","w","h","tile_name"}
        self._split_lines: list[dict] = []       # {"node", "orientation", "x1","y1","x2","y2","parent_x","parent_y","parent_w","parent_h"}

        # Selection & interaction
        self._selected_leaf_id: Optional[int] = None
        self._active_split_node: Optional[dict] = None
        self._active_split_orientation: Optional[str] = None
        self._last_mouse_pos: Optional[QPointF] = None

        # World->canvas transform
        self._scale: float = 1.0
        self._offset_x: float = 0.0
        self._offset_y: float = 0.0

    def set_background_image(self, path: Optional[str]) -> None:
        """
        Load an image from disk and use it as canvas background.
        Pass None or an empty string to clear the background.
        """
        if not path:
            self._background_pixmap = None
        else:
            pm = QPixmap(path)
            if pm.isNull():
                # Failed to load -> just clear
                self._background_pixmap = None
            else:
                self._background_pixmap = pm

        self.update()

    # ========= basic helpers =========

    def _alloc_leaf_id(self) -> int:
        lid = self._next_leaf_id
        self._next_leaf_id += 1
        return lid

    def _compute_screen_bbox(self) -> tuple[int, int]:
        """
        Compute the virtual screen size for the currently selected monitor.

        - If the profile has monitor == "default", use the primary screen.
        - Otherwise, try to find the QScreen with that name.
        """
        monitor_name = None
        if self._profile is not None:
            monitor_name = getattr(self._profile, "monitor", None)

        screen = None
        if monitor_name and monitor_name != "default":
            # Look for the matching QScreen by name
            for s in QApplication.screens():
                if s.name() == monitor_name:
                    screen = s
                    break

        if screen is None:
            screen = QApplication.primaryScreen()

        if screen:
            geo = screen.geometry()
            screen_w = geo.width()
            screen_h = geo.height()
        else:
            screen_w, screen_h = 1920, 1080

        return max(screen_w, 1), max(screen_h, 1)

    def _recompute_transform(self) -> None:
        """
        Compute scale and offset so that the whole screen fits into the canvas,
        using the entire widget area (no extra margins).
        """
        screen_w, screen_h = self._compute_screen_bbox()
        rect = self.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            self._scale = 1.0
            self._offset_x = 0.0
            self._offset_y = 0.0
            return

        # Use full widget space, no outer margin
        available_w = max(rect.width(), 1)
        available_h = max(rect.height(), 1)

        sx = available_w / float(screen_w)
        sy = available_h / float(screen_h)
        self._scale = min(sx, sy)

        canvas_w = screen_w * self._scale
        canvas_h = screen_h * self._scale

        # Center the screen inside the widget (no extra padding beyond
        # what comes from aspect ratio differences)
        self._offset_x = (rect.width() - canvas_w) / 2.0
        self._offset_y = (rect.height() - canvas_h) / 2.0

    def _world_to_canvas(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        cx = self._offset_x + x * self._scale
        cy = self._offset_y + y * self._scale
        cw = w * self._scale
        ch = h * self._scale
        return cx, cy, cw, ch

    def _canvas_to_world(self, x: float, y: float) -> tuple[float, float]:
        wx = (x - self._offset_x) / self._scale
        wy = (y - self._offset_y) / self._scale
        return wx, wy

    # ========= tree operations =========

    def _ensure_root(self) -> None:
        """Ensure there is at least one full-screen leaf."""
        if self._root is not None:
            return
        leaf_id = self._alloc_leaf_id()
        self._root = {"type": "leaf", "id": leaf_id, "tile_name": ""}

    def _rebuild_from_tree(self) -> None:
        """
        Compute leaf rectangles and split lines from the current tree.
        """
        self._leaf_rects.clear()
        self._split_lines.clear()
        if self._root is None:
            return

        screen_w, screen_h = self._compute_screen_bbox()

        def walk(node: dict, x: float, y: float, w: float, h: float) -> None:
            if node["type"] == "leaf":
                self._leaf_rects[node["id"]] = {
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "tile_name": node.get("tile_name", ""),
                }
                return

            orient = node.get("orientation", "v")
            ratio = float(node.get("ratio", 0.5))

            # derive a min ratio from the desired leaf pixel size
            if orient == "v":
                # children share parent width
                min_ratio = self._min_leaf_size / max(w, 1.0)
            else:
                # children share parent height
                min_ratio = self._min_leaf_size / max(h, 1.0)

            # keep it sane on very small parents and leave room for both sides
            min_ratio = max(0.01, min(min_ratio, 0.49))
            max_ratio = 1.0 - min_ratio

            ratio = max(min_ratio, min(ratio, max_ratio))
            node["ratio"] = ratio

            if orient == "v":
                w1 = w * ratio
                w2 = w - w1
                x_split = x + w1
                # record split line
                self._split_lines.append(
                    {
                        "node": node,
                        "orientation": "v",
                        "x1": x_split,
                        "y1": y,
                        "x2": x_split,
                        "y2": y + h,
                        "parent_x": x,
                        "parent_y": y,
                        "parent_w": w,
                        "parent_h": h,
                    }
                )
                walk(node["first"], x, y, w1, h)
                walk(node["second"], x_split, y, w2, h)
            else:
                h1 = h * ratio
                h2 = h - h1
                y_split = y + h1
                self._split_lines.append(
                    {
                        "node": node,
                        "orientation": "h",
                        "x1": x,
                        "y1": y_split,
                        "x2": x + w,
                        "y2": y_split,
                        "parent_x": x,
                        "parent_y": y,
                        "parent_w": w,
                        "parent_h": h,
                    }
                )
                walk(node["first"], x, y, w, h1)
                walk(node["second"], x, y_split, w, h2)

        walk(self._root, 0.0, 0.0, float(screen_w), float(screen_h))

    def _find_leaf_at_canvas_pos(self, pos: QPointF) -> Optional[int]:
        if not self._leaf_rects:
            return None
        x = pos.x()
        y = pos.y()
        for lid, rect in self._leaf_rects.items():
            cx, cy, cw, ch = self._world_to_canvas(
                rect["x"], rect["y"], rect["w"], rect["h"]
            )
            if cx <= x <= cx + cw and cy <= y <= cy + ch:
                return lid
        return None

    def _find_split_at_canvas_pos(self, pos: QPointF, tol: float = 6.0) -> Optional[dict]:
        if not self._split_lines:
            return None
        x = pos.x()
        y = pos.y()
        best = None
        best_dist = None
        for info in self._split_lines:
            if info["orientation"] == "v":
                cx1, cy1, _, _ = self._world_to_canvas(info["x1"], info["y1"], 0.0, 0.0)
                _, cy2, _, _ = self._world_to_canvas(info["x2"], info["y2"], 0.0, 0.0)
                # vertical line at x = cx1, y in [cy1, cy2]
                if cy1 <= y <= cy2:
                    dist = abs(x - cx1)
                else:
                    continue
            else:
                cx1, cy1, _, _ = self._world_to_canvas(info["x1"], info["y1"], 0.0, 0.0)
                cx2, _, _, _ = self._world_to_canvas(info["x2"], info["y2"], 0.0, 0.0)
                if cx1 <= x <= cx2:
                    dist = abs(y - cy1)
                else:
                    continue

            if dist <= tol and (best is None or dist < best_dist):
                best = info
                best_dist = dist
        return best

    # ========= public API =========

    def set_profile(self, profile: Optional[ProfileModel]) -> None:
        """
        Called by the main window whenever the current profile changes
        or when the user explicitly clicks "Edit Layout".

        Behavior:
        - If the profile has saved layout_slots, reconstruct the split tree
          from those rectangles.
        - Otherwise start with a single full-screen leaf.
        """
        self._profile = profile
        self._selected_leaf_id = None
        self._active_split_node = None
        self._active_split_orientation = None
        self._last_mouse_pos = None
        self._leaf_rects.clear()
        self._split_lines.clear()
        self._root = None
        self._next_leaf_id = 0

        if profile is None:
            # No profile -> no background, no tree
            self.set_background_image(None)
            self.update()
            return

        # Gap from profile
        self._gap = profile.tile_gap

        # Monitor-specific background image (if configured)
        bg_path = ""
        try:
            backgrounds = profile.monitor_backgrounds
            if isinstance(backgrounds, dict):
                bg_path = backgrounds.get(profile.monitor, "") or ""
        except Exception:
            bg_path = ""

        if bg_path:
            self.set_background_image(bg_path)
        else:
            self.set_background_image(None)


        # If a saved layout exists, rebuild the tree from it.
        raw_slots = getattr(profile, "layout_slots", None) or []
        if raw_slots:
            self._init_tree_from_profile_layout(raw_slots)
        else:
            # No saved layout yet – start with a single full-screen leaf
            self._ensure_root()

        self._rebuild_from_tree()
        self._recompute_transform()
        self.update()

    def _init_tree_from_profile_layout(self, raw_slots: list[dict[str, Any]]) -> None:
        """
        Build the internal split tree (_root) from saved layout_slots.

        Assumes:
        - raw_slots are non-overlapping, axis-aligned rectangles
        - they were originally produced by this same split-based layout
        """
        # Convert to a clean rect list
        rects: list[dict[str, Any]] = []
        for s in raw_slots:
            rects.append(
                {
                    "x": float(s.get("x", 0)),
                    "y": float(s.get("y", 0)),
                    "w": float(s.get("w", 0)),
                    "h": float(s.get("h", 0)),
                    "tile_name": str(s.get("tile_name") or ""),
                }
            )

        if not rects:
            self._ensure_root()
            return

        # Compute bounding box of all rects – this becomes the root region
        min_x = min(r["x"] for r in rects)
        min_y = min(r["y"] for r in rects)
        max_x = max(r["x"] + r["w"] for r in rects)
        max_y = max(r["y"] + r["h"] for r in rects)
        bounds = (min_x, min_y, max_x - min_x, max_y - min_y)

        # Reset ID allocator and build tree
        self._next_leaf_id = 0
        self._root = self._build_tree_from_rects(rects, bounds)

        if self._root is None:
            # Fallback: just one full-screen leaf
            self._ensure_root()

    def _build_tree_from_rects(
        self,
        rects: list[dict[str, Any]],
        bounds: tuple[float, float, float, float],
        eps: float = 0.5,
    ) -> Optional[dict]:
        """
        Recursively reconstruct a split tree from a set of rectangles.

        - If the region can be partitioned by a vertical line where no rect
          crosses that line, we create a vertical split.
        - Otherwise, we try the same with a horizontal line.
        - If no clean split is found and there is exactly one rect, we make
          a leaf.
        """
        x0, y0, w0, h0 = bounds
        if not rects:
            return None

        if len(rects) == 1:
            r = rects[0]
            leaf_id = self._alloc_leaf_id()
            return {
                "type": "leaf",
                "id": leaf_id,
                "tile_name": r.get("tile_name", ""),
            }

        # ----- try vertical splits -----
        xs = set()
        for r in rects:
            xs.add(r["x"])
            xs.add(r["x"] + r["w"])
        xs = sorted(xs)

        # candidate split lines (ignore outer edges)
        candidates_x = [x for x in xs if x0 + eps < x < x0 + w0 - eps]

        for split_x in candidates_x:
            left: list[dict[str, Any]] = []
            right: list[dict[str, Any]] = []
            crossing: list[dict[str, Any]] = []

            for r in rects:
                rx1 = r["x"]
                rx2 = r["x"] + r["w"]
                if rx2 <= split_x + eps:
                    left.append(r)
                elif rx1 >= split_x - eps:
                    right.append(r)
                else:
                    crossing.append(r)

            if not crossing and left and right:
                # valid vertical partition
                # left bounds
                l_min_x = min(r["x"] for r in left)
                l_min_y = min(r["y"] for r in left)
                l_max_x = max(r["x"] + r["w"] for r in left)
                l_max_y = max(r["y"] + r["h"] for r in left)
                left_bounds = (l_min_x, l_min_y, l_max_x - l_min_x, l_max_y - l_min_y)

                # right bounds
                r_min_x = min(r["x"] for r in right)
                r_min_y = min(r["y"] for r in right)
                r_max_x = max(r["x"] + r["w"] for r in right)
                r_max_y = max(r["y"] + r["h"] for r in right)
                right_bounds = (r_min_x, r_min_y, r_max_x - r_min_x, r_max_y - r_min_y)

                node: dict[str, Any] = {
                    "type": "split",
                    "orientation": "v",
                    "ratio": (split_x - x0) / float(w0) if w0 > 0 else 0.5,
                }
                node["first"] = self._build_tree_from_rects(left, left_bounds, eps)
                node["second"] = self._build_tree_from_rects(right, right_bounds, eps)
                return node

        # ----- try horizontal splits -----
        ys = set()
        for r in rects:
            ys.add(r["y"])
            ys.add(r["y"] + r["h"])
        ys = sorted(ys)

        candidates_y = [y for y in ys if y0 + eps < y < y0 + h0 - eps]

        for split_y in candidates_y:
            top: list[dict[str, Any]] = []
            bottom: list[dict[str, Any]] = []
            crossing: list[dict[str, Any]] = []

            for r in rects:
                ry1 = r["y"]
                ry2 = r["y"] + r["h"]
                if ry2 <= split_y + eps:
                    top.append(r)
                elif ry1 >= split_y - eps:
                    bottom.append(r)
                else:
                    crossing.append(r)

            if not crossing and top and bottom:
                # valid horizontal partition
                t_min_x = min(r["x"] for r in top)
                t_min_y = min(r["y"] for r in top)
                t_max_x = max(r["x"] + r["w"] for r in top)
                t_max_y = max(r["y"] + r["h"] for r in top)
                top_bounds = (t_min_x, t_min_y, t_max_x - t_min_x, t_max_y - t_min_y)

                b_min_x = min(r["x"] for r in bottom)
                b_min_y = min(r["y"] for r in bottom)
                b_max_x = max(r["x"] + r["w"] for r in bottom)
                b_max_y = max(r["y"] + r["h"] for r in bottom)
                bottom_bounds = (b_min_x, b_min_y, b_max_x - b_min_x, b_max_y - b_min_y)

                node = {
                    "type": "split",
                    "orientation": "h",
                    "ratio": (split_y - y0) / float(h0) if h0 > 0 else 0.5,
                }
                node["first"] = self._build_tree_from_rects(top, top_bounds, eps)
                node["second"] = self._build_tree_from_rects(bottom, bottom_bounds, eps)
                return node

        # Fallback: treat this region as a single leaf (geometry will still
        # be correct due to export_slots_for_profile using computed rects).
        leaf_id = self._alloc_leaf_id()
        # pick a tile name if all rects share the same name, otherwise empty
        names = {r.get("tile_name", "") for r in rects}
        tile_name = names.pop() if len(names) == 1 else ""
        return {"type": "leaf", "id": leaf_id, "tile_name": tile_name}

    def export_slots_for_profile(self) -> list[dict[str, Any]]:
        """
        Export current leaf rectangles to a flat list compatible with
        ProfileModel.layout_slots.
        """
        out: list[dict[str, Any]] = []
        for lid, rect in self._leaf_rects.items():
            out.append(
                {
                    "x": int(round(rect["x"])),
                    "y": int(round(rect["y"])),
                    "w": int(round(rect["w"])),
                    "h": int(round(rect["h"])),
                    "tile_name": str(rect.get("tile_name", "")),
                }
            )
        return out

    def set_selected_index(self, idx: Optional[int]) -> None:
        """
        MainWindow -> Canvas:
        - idx is a tile index in profile.tiles
        - We find the leaf that is assigned to that tile name and select it.
        - If idx is None, we clear selection.
        """
        self._selected_leaf_id = None
        if self._profile is None or idx is None:
            self.update()
            return

        tiles = self._profile.tiles
        if not (0 <= idx < len(tiles)):
            self.update()
            return

        target_name = tiles[idx].name
        for lid, rect in self._leaf_rects.items():
            if rect.get("tile_name") == target_name:
                self._selected_leaf_id = lid
                break

        self.update()

    # ========= Qt events =========

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recompute_transform()
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

        if self._root is None or self._profile is None:
            return

        # Rebuild geometry each paint to keep it in sync
        self._rebuild_from_tree()

        from PyQt6.QtGui import QPainter, QColor, QPen, QBrush
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background: fill everything with dark gray
        painter.fillRect(self.rect(), QColor(30, 30, 30))

        # If we have a background image, draw it only over the usable screen area
        if self._background_pixmap is not None:
            # "World" coords: 0..screen_w, 0..screen_h (the virtual screen)
            screen_w, screen_h = self._compute_screen_bbox()
            cx, cy, cw, ch = self._world_to_canvas(0.0, 0.0, float(screen_w), float(screen_h))

            # Draw the pixmap scaled into that screen rectangle
            painter.drawPixmap(int(cx), int(cy), int(cw), int(ch), self._background_pixmap)

        # Draw leaves (slots) — no visual gap, use logical rects directly
        for lid, rect in self._leaf_rects.items():
            x = rect["x"]
            y = rect["y"]
            w = rect["w"]
            h = rect["h"]

            cx, cy, cw, ch = self._world_to_canvas(x, y, w, h)

            is_selected = (lid == self._selected_leaf_id)

            painter.setBrush(QBrush(QColor(70, 90, 110, 180)))
            painter.setPen(QPen(QColor(200, 200, 200) if is_selected else QColor(120, 120, 120), 1.0))
            painter.drawRect(int(cx), int(cy), int(cw), int(ch))

            name = rect.get("tile_name") or ""
            if name:
                painter.setPen(QColor(230, 230, 230))
                painter.drawText(int(cx) + 4, int(cy) + 16, name)


        # Draw split lines as guides
        painter.setPen(QPen(QColor(220, 180, 80), 1.0))
        for info in self._split_lines:
            if info["orientation"] == "v":
                cx1, cy1, _, _ = self._world_to_canvas(info["x1"], info["y1"], 0.0, 0.0)
                _, cy2, _, _ = self._world_to_canvas(info["x2"], info["y2"], 0.0, 0.0)
                painter.drawLine(int(cx1), int(cy1), int(cx1), int(cy2))
            else:
                cx1, cy1, _, _ = self._world_to_canvas(info["x1"], info["y1"], 0.0, 0.0)
                cx2, _, _, _ = self._world_to_canvas(info["x2"], info["y2"], 0.0, 0.0)
                painter.drawLine(int(cx1), int(cy1), int(cx2), int(cy1))

        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._profile is None or self._root is None:
            return

        pos = event.position()

        if event.button() == Qt.MouseButton.LeftButton:
            # First see if we hit a split line
            split_info = self._find_split_at_canvas_pos(pos)
            if split_info is not None:
                self._active_split_node = split_info["node"]
                self._active_split_orientation = split_info["orientation"]
                self._last_mouse_pos = pos
                return

            # Otherwise select a leaf
            lid = self._find_leaf_at_canvas_pos(pos)
            self._selected_leaf_id = lid
            self._last_mouse_pos = pos

            if self._profile is not None and lid is not None:
                leaf = self._leaf_rects.get(lid)
                if leaf:
                    tile_name = leaf.get("tile_name") or ""
                    if tile_name:
                        idx = self._find_tile_index_by_name(self._profile, tile_name)
                        if idx is not None:
                            self.tileSelected.emit(idx)

            self.update()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._active_split_node is None
            or self._active_split_orientation is None
            or self._last_mouse_pos is None
        ):
            return super().mouseMoveEvent(event)

        pos = event.position()
        dx_canvas = pos.x() - self._last_mouse_pos.x()
        dy_canvas = pos.y() - self._last_mouse_pos.y()
        self._last_mouse_pos = pos

        if self._scale <= 0:
            return

        dx_world = dx_canvas / self._scale
        dy_world = dy_canvas / self._scale

        # Find the split info for this node (using latest geometry)
        self._rebuild_from_tree()
        info = None
        for s in self._split_lines:
            if s["node"] is self._active_split_node:
                info = s
                break
        if info is None:
            return

        # minimal child size in pixels (same value as used in _rebuild_from_tree)
        min_size = self._min_leaf_size
        snap_dist = 8.0

        if self._active_split_orientation == "v":
            # move x_split
            parent_x = info["parent_x"]
            parent_w = info["parent_w"]
            x_old = info["x1"]
            x_new = x_old + dx_world

            # clamp inside parent with min_size
            left_min = parent_x + min_size
            right_max = parent_x + parent_w - min_size
            if right_max <= left_min:
                return
            x_new = max(left_min, min(x_new, right_max))

            # magnetic snap against other vertical lines that overlap this parent rect
            candidates: list[float] = []
            for s in self._split_lines:
                if s["orientation"] != "v" or s["node"] is self._active_split_node:
                    continue
                # Only splits that overlap vertically with this parent region
                if not (s["y2"] <= info["parent_y"] or s["y1"] >= info["parent_y"] + info["parent_h"]):
                    candidates.append(s["x1"])
            for cx in candidates:
                if abs(cx - x_new) <= snap_dist:
                    x_new = cx
                    break

            # derive new ratio and clamp based on _min_leaf_size
            new_ratio = (x_new - parent_x) / parent_w

            min_ratio = self._min_leaf_size / max(parent_w, 1.0)
            min_ratio = max(0.01, min(min_ratio, 0.49))
            max_ratio = 1.0 - min_ratio

            new_ratio = max(min_ratio, min(new_ratio, max_ratio))
            self._active_split_node["orientation"] = "v"
            self._active_split_node["ratio"] = new_ratio

        else:
            # horizontal split, move y_split
            parent_y = info["parent_y"]
            parent_h = info["parent_h"]
            y_old = info["y1"]
            y_new = y_old + dy_world

            top_min = parent_y + min_size
            bottom_max = parent_y + parent_h - min_size
            if bottom_max <= top_min:
                return
            y_new = max(top_min, min(y_new, bottom_max))

            candidates: list[float] = []
            for s in self._split_lines:
                if s["orientation"] != "h" or s["node"] is self._active_split_node:
                    continue
                if not (s["x2"] <= info["parent_x"] or s["x1"] >= info["parent_x"] + info["parent_w"]):
                    candidates.append(s["y1"])
            for cy in candidates:
                if abs(cy - y_new) <= snap_dist:
                    y_new = cy
                    break

            new_ratio = (y_new - parent_y) / parent_h

            min_ratio = self._min_leaf_size / max(parent_h, 1.0)
            min_ratio = max(0.01, min(min_ratio, 0.49))
            max_ratio = 1.0 - min_ratio

            new_ratio = max(min_ratio, min(new_ratio, max_ratio))
            self._active_split_node["orientation"] = "h"
            self._active_split_node["ratio"] = new_ratio

        # After adjusting ratio, rebuild geometry and propagate into tiles
        self._rebuild_from_tree()
        self._push_geometry_into_tiles()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._active_split_node = None
            self._active_split_orientation = None
            self._last_mouse_pos = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self._profile is None or self._root is None:
            return

        pos = event.pos()
        pos_f = QPointF(pos)

        # Make sure geometry is up to date
        self._rebuild_from_tree()

        # ---------- 1) Edge menu: right-click on split line ----------
        split_info = self._find_split_at_canvas_pos(pos_f)
        if split_info is not None:
            node = split_info["node"]
            menu = QMenu(self)

            act_combine = None
            can_combine = False

            # Combine only if both children are leaves and same size
            first = node.get("first")
            second = node.get("second")
            if (
                isinstance(first, dict)
                and isinstance(second, dict)
                and first.get("type") == "leaf"
                and second.get("type") == "leaf"
            ):
                lid1 = first.get("id")
                lid2 = second.get("id")
                r1 = self._leaf_rects.get(lid1)
                r2 = self._leaf_rects.get(lid2)
                if r1 and r2:
                    if (
                        abs(r1["w"] - r2["w"]) < 1e-3
                        and abs(r1["h"] - r2["h"]) < 1e-3
                    ):
                        can_combine = True

            if can_combine:
                act_combine = QAction("Combine tiles (remove split)", self)
                menu.addAction(act_combine)

            chosen = menu.exec(event.globalPos())
            if chosen is None:
                return

            if act_combine is not None and chosen == act_combine:
                self._combine_split_node(node)

            return

        # ---------- 2) Tile menu: right-click inside a tile ----------
        lid = self._find_leaf_at_canvas_pos(pos_f)
        if lid is None:
            return

        leaf_rect = self._leaf_rects.get(lid)
        if leaf_rect is None:
            return

        menu = QMenu(self)

        act_split_h = QAction("Split horizontally", self)
        act_split_v = QAction("Split vertically", self)
        menu.addAction(act_split_h)
        menu.addAction(act_split_v)
        menu.addSeparator()

        assign_menu = menu.addMenu("Assign tile")

        # collect tile names that are already used by some other leaf
        used_names = {
            r.get("tile_name")
            for k, r in self._leaf_rects.items()
            if k != lid and r.get("tile_name")
        }
        tiles = self._profile.tiles

        act_none = QAction("<none>", self)
        assign_menu.addAction(act_none)

        tile_actions: dict[QAction, str] = {}
        for t in tiles:
            name = t.name or "<unnamed>"
            if name in used_names and name != leaf_rect.get("tile_name"):
                continue
            act = QAction(name, self)
            assign_menu.addAction(act)
            tile_actions[act] = name

        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return

        if chosen == act_split_h:
            count, ok = QInputDialog.getInt(
                self,
                "Horizontal split",
                "How many horizontal tiles?",
                2,   # default
                2,   # min
                16,  # max
                1,   # step
            )
            if ok:
                self._split_leaf_into(lid, count, horizontal=True)

        elif chosen == act_split_v:
            count, ok = QInputDialog.getInt(
                self,
                "Vertical split",
                "How many vertical tiles?",
                2,
                2,
                16,
                1,
            )
            if ok:
                self._split_leaf_into(lid, count, horizontal=False)

        elif chosen == act_none:
            leaf_rect["tile_name"] = ""
            # also clear in tree
            self._set_leaf_tile_name(lid, "")
            self._push_geometry_into_tiles()
            self.update()

        elif chosen in tile_actions:
            new_name = tile_actions[chosen]
            leaf_rect["tile_name"] = new_name
            self._set_leaf_tile_name(lid, new_name)
            # select that tile in the UI
            tile_idx = self._find_tile_index_by_name(self._profile, new_name)
            if tile_idx is not None:
                self.tileSelected.emit(tile_idx)
            self._push_geometry_into_tiles()
            self.update()

    # ========= split / leaf helpers =========

    def _find_leaf_node(self, node: dict, leaf_id: int) -> Optional[dict]:
        if node["type"] == "leaf":
            return node if node["id"] == leaf_id else None
        res = self._find_leaf_node(node["first"], leaf_id)
        if res is not None:
            return res
        return self._find_leaf_node(node["second"], leaf_id)

    def _replace_leaf_with_split(self, node: dict, leaf_id: int, new_node: dict) -> bool:
        """
        Recursively replace a leaf with id leaf_id by new_node.
        Returns True if replacement happened.
        """
        if node["type"] == "leaf":
            return False
        if node["first"]["type"] == "leaf" and node["first"]["id"] == leaf_id:
            node["first"] = new_node
            return True
        if node["second"]["type"] == "leaf" and node["second"]["id"] == leaf_id:
            node["second"] = new_node
            return True
        if self._replace_leaf_with_split(node["first"], leaf_id, new_node):
            return True
        return self._replace_leaf_with_split(node["second"], leaf_id, new_node)

    def _replace_split_with_leaf(self, node: dict, target_split: dict, new_leaf: dict) -> bool:
        """
        Recursively replace a split node with a leaf.
        Returns True if replacement happened.
        """
        if node.get("type") != "split":
            return False

        if node.get("first") is target_split:
            node["first"] = new_leaf
            return True
        if node.get("second") is target_split:
            node["second"] = new_leaf
            return True

        if self._replace_split_with_leaf(node.get("first"), target_split, new_leaf):
            return True
        return self._replace_split_with_leaf(node.get("second"), target_split, new_leaf)

    def _combine_split_node(self, split_node: dict) -> None:
        """
        Remove a split node and merge its two child leaves into one leaf.
        Keeps one of the tile assignments (if any).
        """
        if self._root is None:
            return

        first = split_node.get("first")
        second = split_node.get("second")
        if not (
            isinstance(first, dict)
            and isinstance(second, dict)
            and first.get("type") == "leaf"
            and second.get("type") == "leaf"
        ):
            return

        # Prefer a non-empty tile_name if present
        tile_name = (first.get("tile_name") or "") or (second.get("tile_name") or "")

        new_leaf = {
            "type": "leaf",
            "id": self._alloc_leaf_id(),
            "tile_name": tile_name,
        }

        if self._root is split_node:
            self._root = new_leaf
        else:
            self._replace_split_with_leaf(self._root, split_node, new_leaf)

        self._rebuild_from_tree()
        self._push_geometry_into_tiles()
        self.update()

    def _split_leaf(self, leaf_id: int, horizontal: bool) -> None:
        """
        Backwards-compatible: split into exactly 2 parts.
        """
        self._split_leaf_into(leaf_id, 2, horizontal)

    def _split_leaf_into(self, leaf_id: int, count: int, horizontal: bool) -> None:
        """
        Split a leaf into `count` equal parts in the given orientation.
        """
        if self._root is None:
            return
        if count <= 1:
            return

        # find leaf node
        if self._root.get("type") == "leaf" and self._root.get("id") == leaf_id:
            leaf_node = self._root
            is_root = True
        else:
            leaf_node = self._find_leaf_node(self._root, leaf_id)
            is_root = False

        if leaf_node is None or leaf_node.get("type") != "leaf":
            return

        tile_name = leaf_node.get("tile_name", "")

        # Build a subtree that splits this leaf into `count` equal parts
        new_subtree = self._build_equal_split_chain(count, horizontal, tile_name)

        if is_root:
            self._root = new_subtree
        else:
            self._replace_leaf_with_split(self._root, leaf_id, new_subtree)

        self._rebuild_from_tree()
        self._push_geometry_into_tiles()
        self.update()

    def _build_equal_split_chain(self, count: int, horizontal: bool, tile_name: str) -> dict:
        """
        Build a chain of splits that divides a region into `count` equal parts.

        The first leaf keeps the original tile assignment, the rest start empty.
        """
        orientation = "h" if horizontal else "v"

        if count == 1:
            return {
                "type": "leaf",
                "id": self._alloc_leaf_id(),
                "tile_name": tile_name,
            }

        # First leaf keeps the tile_name
        first_leaf = {
            "type": "leaf",
            "id": self._alloc_leaf_id(),
            "tile_name": tile_name,
        }

        # Remaining parts built recursively (they start without tile assignment)
        rest_subtree = self._build_equal_split_chain(count - 1, horizontal, "")

        # ratio gives first_leaf 1/count of the parent region
        node: dict[str, Any] = {
            "type": "split",
            "orientation": orientation,
            "ratio": 1.0 / float(count),
            "first": first_leaf,
            "second": rest_subtree,
        }
        return node

    def _set_leaf_tile_name(self, leaf_id: int, name: str) -> None:
        if self._root is None:
            return

        def walk(node: dict) -> None:
            if node["type"] == "leaf":
                if node["id"] == leaf_id:
                    node["tile_name"] = name
                return
            walk(node["first"])
            walk(node["second"])

        walk(self._root)

    def _push_geometry_into_tiles(self) -> None:
        """Push current leaf rects into the corresponding TileModel objects."""
        if self._profile is None:
            return

        tiles = self._profile.tiles

        # gap in pixels; comes from the profile / UI
        gap = float(self._gap)
        if gap < 0.0:
            gap = 0.0

        screen_w, screen_h = self._compute_screen_bbox()
        screen_w = float(screen_w)
        screen_h = float(screen_h)
        eps = 0.5  # tolerance for boundary checks

        for lid, rect in self._leaf_rects.items():
            name = rect.get("tile_name") or ""
            if not name:
                continue

            idx = self._find_tile_index_by_name(self._profile, name)
            if idx is None or not (0 <= idx < len(tiles)):
                continue

            x = float(rect["x"])
            y = float(rect["y"])
            w = float(rect["w"])
            h = float(rect["h"])

            if gap > 0.0:
                # Internal shared edges: gap/2 on each side -> total gap between tiles = gap
                # Outer screen edges: full gap to the screen border
                left_pad = gap if abs(x - 0.0) <= eps else gap / 2.0
                right_pad = gap if abs((x + w) - screen_w) <= eps else gap / 2.0
                top_pad = gap if abs(y - 0.0) <= eps else gap / 2.0
                bottom_pad = gap if abs((y + h) - screen_h) <= eps else gap / 2.0

                # Clamp so we don't collapse rectangles if gap is huge
                total_w_pad = min(left_pad + right_pad, max(w - 1.0, 0.0))
                total_h_pad = min(top_pad + bottom_pad, max(h - 1.0, 0.0))

                x_out = x + left_pad
                y_out = y + top_pad
                w_out = max(1.0, w - total_w_pad)
                h_out = max(1.0, h - total_h_pad)
            else:
                x_out = x
                y_out = y
                w_out = w
                h_out = h

            tiles[idx].set_geometry(
                int(round(x_out)),
                int(round(y_out)),
                int(round(w_out)),
                int(round(h_out)),
            )
            self.geometryChanged.emit(idx)

    def _find_tile_index_by_name(self, profile: ProfileModel, name: str) -> Optional[int]:
        tiles = profile.tiles
        for i, t in enumerate(tiles):
            if t.name == name:
                return i
        return None
