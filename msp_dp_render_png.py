#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from msp_proto import parse_msp_v1
from displayport import (
    DP_CLEAR_SCREEN,
    DP_DRAW_SCREEN,
    DP_HEARTBEAT,
    DP_RELEASE,
    DP_WRITE_STRING,
)

MSP_DISPLAYPORT = 0xB6


@dataclass
class MSPDPCanvas:
    cols: int = 60
    rows: int = 22
    frame: int = 0
    dirty: bool = True
    grid: list[list[Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.grid = [[" " for _ in range(self.cols)] for _ in range(self.rows)]
        self.dirty = True

    def write_string(self, row: int, col: int, data: bytes) -> None:
        if row < 0 or row >= self.rows or col < 0 or col >= self.cols:
            return
        if not data:
            return

        payload = data[1:]
        if b"\x00" in payload:
            payload = payload.split(b"\x00", 1)[0]

        x = col
        for value in payload:
            if x >= self.cols:
                break
            self.grid[row][x] = value
            x += 1

        self.dirty = True

    def draw(self) -> None:
        self.frame += 1
        self.dirty = True


class OSDRenderer:
    def __init__(
        self,
        width: int,
        height: int,
        cols: int,
        rows: int,
        charset_path: str | None,
    ) -> None:
        self.width = width
        self.height = height
        self.cols = cols
        self.rows = rows
        self.cell_width = max(1, width // cols)
        self.cell_height = max(1, height // rows)
        self.font = ImageFont.load_default()
        self.charset: Image.Image | None = None
        self.tile_width = 0
        self.tile_height = 0
        self.tiles_per_row = 0

        if charset_path:
            self._load_charset(Path(charset_path))

    def _load_charset(self, charset_path: Path) -> None:
        charset = Image.open(charset_path).convert("RGBA")
        self.charset = charset
        # Typical OSD charsets are 16x16 tiles for 256 glyphs.
        self.tiles_per_row = 16
        tiles_per_col = 16
        self.tile_width = max(1, charset.width // self.tiles_per_row)
        self.tile_height = max(1, charset.height // tiles_per_col)

    def _glyph_from_charset(self, value: int) -> Image.Image | None:
        if self.charset is None:
            return None

        glyph_count_per_col = max(1, self.charset.height // self.tile_height)
        max_glyphs = self.tiles_per_row * glyph_count_per_col
        index = value % max_glyphs
        tile_x = (index % self.tiles_per_row) * self.tile_width
        tile_y = (index // self.tiles_per_row) * self.tile_height
        return self.charset.crop((tile_x, tile_y, tile_x + self.tile_width, tile_y + self.tile_height))

    @staticmethod
    def _value_to_text(value: Any) -> str:
        if isinstance(value, str):
            if value:
                return value[0]
            return " "
        if isinstance(value, int):
            if 32 <= value <= 126:
                return chr(value)
            return " "
        return " "

    def render(self, canvas: MSPDPCanvas, output_path: Path) -> None:
        image = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        for row in range(canvas.rows):
            for col in range(canvas.cols):
                pixel_x = col * self.cell_width
                pixel_y = row * self.cell_height
                cell_value = canvas.grid[row][col]

                glyph_index = None
                if isinstance(cell_value, int):
                    glyph_index = cell_value
                elif isinstance(cell_value, str) and cell_value:
                    glyph_index = ord(cell_value[0])

                if glyph_index is not None:
                    glyph = self._glyph_from_charset(glyph_index)
                    if glyph is not None:
                        glyph_scaled = glyph.resize((self.cell_width, self.cell_height), Image.Resampling.NEAREST)
                        image.alpha_composite(glyph_scaled, dest=(pixel_x, pixel_y))
                        continue

                text = self._value_to_text(cell_value)
                if text == " ":
                    continue

                bbox = draw.textbbox((0, 0), text, font=self.font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
                text_x = pixel_x + max(0, (self.cell_width - text_w) // 2)
                text_y = pixel_y + max(0, (self.cell_height - text_h) // 2)
                draw.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=self.font)

        image.save(output_path)


def run(
    bind: str,
    port: int,
    cols: int,
    rows: int,
    width: int,
    height: int,
    fps: float,
    output: str,
    charset: str | None,
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind, port))
    sock.settimeout(0.2)

    canvas = MSPDPCanvas(cols=cols, rows=rows)
    renderer = OSDRenderer(
        width=width,
        height=height,
        cols=cols,
        rows=rows,
        charset_path=charset,
    )

    min_frame_dt = 1.0 / max(1.0, fps)
    last_write_ts = 0.0

    while True:
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue

        frame = parse_msp_v1(data)
        if not frame or not frame.csum_ok or frame.cmd != MSP_DISPLAYPORT or not frame.payload:
            continue

        sub = frame.payload[0]

        if sub == DP_HEARTBEAT:
            continue
        if sub in (DP_RELEASE, DP_CLEAR_SCREEN):
            canvas.clear()
            continue
        if sub == DP_WRITE_STRING and len(frame.payload) >= 4:
            row = frame.payload[1]
            col = frame.payload[2]
            rest = frame.payload[3:]
            canvas.write_string(row, col, rest)
            continue
        if sub == DP_DRAW_SCREEN:
            canvas.draw()
            now = time.time()
            if canvas.dirty and (now - last_write_ts) >= min_frame_dt:
                renderer.render(canvas, Path(output))
                canvas.dirty = False
                last_write_ts = now


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=14560)
    parser.add_argument("--cols", type=int, default=60)
    parser.add_argument("--rows", type=int, default=22)
    parser.add_argument("--width", type=int, default=720)
    parser.add_argument("--height", type=int, default=576)
    parser.add_argument("--fps", type=float, default=20.0)
    parser.add_argument("--output", default="osd.png")
    parser.add_argument("--charset", default=None)
    args = parser.parse_args()

    run(
        bind=args.bind,
        port=args.port,
        cols=args.cols,
        rows=args.rows,
        width=args.width,
        height=args.height,
        fps=args.fps,
        output=args.output,
        charset=args.charset,
    )


if __name__ == "__main__":
    main()
