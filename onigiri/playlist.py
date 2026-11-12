"""Scene playlist timing, order, and drag-reorder helpers."""

from __future__ import annotations

import random

_UNITS = {"seconds": 1, "minutes": 60, "hours": 3600}


def interval_ms(value: int, unit: str) -> int:
    try:
        amount = int(value)
    except (TypeError, ValueError):
        amount = 1
    amount = max(1, min(999, amount))
    seconds = amount * _UNITS.get(str(unit), 60)
    return seconds * 1000


def next_index(count: int, current: int, mode: str, rng: random.Random | None = None) -> int:
    if count <= 1:
        return 0
    if str(mode) == "random":
        picker = rng or random
        other = [index for index in range(count) if index != current]
        return picker.choice(other)
    return (current + 1) % count


def move_id(ids: list[str], source: str, insert_at: int) -> list[str]:
    if source not in ids:
        return list(ids)
    current = ids.index(source)
    rest = [item for item in ids if item != source]
    index = insert_at - 1 if insert_at > current else insert_at
    index = max(0, min(index, len(rest)))
    rest.insert(index, source)
    return rest
