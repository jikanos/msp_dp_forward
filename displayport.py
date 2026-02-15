from __future__ import annotations

from dataclasses import dataclass
from typing import List

# MSP_DISPLAYPORT (0xB6) subcommands
DP_HEARTBEAT    = 0x00
DP_RELEASE      = 0x01
DP_CLEAR_SCREEN = 0x02
DP_WRITE_STRING = 0x03
DP_DRAW_SCREEN  = 0x04

def _safe_ascii(b: int) -> str:
    # OSD шрифт не ASCII, но для TUI покажем печатные байты как есть
    if 32 <= b <= 126:
        return chr(b)
    # часто встречаются спец-символы OSD (0x00..0x1F, 0x80..)
    # для отладки показываем точку
    return "·"

@dataclass
class Canvas:
    cols: int = 60
    rows: int = 22

    def __post_init__(self):
        self.clear()
        self.frame = 0
        self.dirty = True

    def clear(self):
        self.grid: List[List[str]] = [[" " for _ in range(self.cols)] for _ in range(self.rows)]
        self.dirty = True

    def write_string(self, row: int, col: int, data: bytes):
        if row < 0 or row >= self.rows or col < 0 or col >= self.cols:
            return

        # data = attribute + bytes of string (NULL-terminated or length-limited)
        if not data:
            return
        attr = data[0]  # пока игнорируем (шрифт/мигание)
        sbytes = data[1:]

        # строка может быть NULL-terminated (как в доках) или просто "до конца payload"
        if b"\x00" in sbytes:
            sbytes = sbytes.split(b"\x00", 1)[0]

        x = col
        for b in sbytes:
            if x >= self.cols:
                break
            self.grid[row][x] = _safe_ascii(b)
            x += 1

        self.dirty = True

    def draw(self):
        self.frame += 1
        self.dirty = True
