#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from displayport import (
    DP_CLEAR_SCREEN,
    DP_DRAW_SCREEN,
    DP_HEARTBEAT,
    DP_RELEASE,
    DP_WRITE_STRING,
)
from msp_proto import parse_msp_v1

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

        # In MSP DisplayPort WRITE_STRING payload, the first byte is an attribute.
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

        # Overlay output size is always video size (pixel-perfect compositor input).
        self.cell_width = max(1, width // cols)
        self.cell_height = max(1, height // rows)

        self.font = ImageFont.load_default()
        self.charset: Image.Image | None = None
        self.tile_width = 0
        self.tile_height = 0
        self.tiles_per_row = 0
        self.tiles_per_col = 0

        if charset_path:
            self._load_charset(Path(charset_path))

    def _load_charset(self, charset_path: Path) -> None:
        charset = Image.open(charset_path).convert("RGBA")

        # Supported layouts:
        #  A) 16x16 tiles (256 glyphs)
        #  B) 256x1 tiles (single row)
        if charset.width % 16 == 0 and charset.height % 16 == 0:
            self.tiles_per_row = 16
            self.tiles_per_col = 16
            self.tile_width = charset.width // 16
            self.tile_height = charset.height // 16
        elif charset.width % 256 == 0:
            self.tiles_per_row = 256
            self.tiles_per_col = 1
            self.tile_width = charset.width // 256
            self.tile_height = charset.height
        else:
            raise ValueError(
                "Unsupported charset layout. Expected 16x16 tiles or 256x1 tiles."
            )

        self.charset = charset

    def _glyph_from_charset(self, value: int) -> Image.Image | None:
        if self.charset is None:
            return None

        glyph = value & 0xFF
        tile_x = (glyph % self.tiles_per_row) * self.tile_width
        tile_y = (glyph // self.tiles_per_row) * self.tile_height

        if tile_y + self.tile_height > self.charset.height:
            return None

        return self.charset.crop(
            (tile_x, tile_y, tile_x + self.tile_width, tile_y + self.tile_height)
        )

    @staticmethod
    def _value_to_text(value: Any) -> str:
        if isinstance(value, int):
            if 32 <= value <= 126:
                return chr(value)
            return " "
        if isinstance(value, str) and value:
            char = value[0]
            return char if char.isprintable() else " "
        return " "

    @staticmethod
    def _save_atomic(image: Image.Image, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        image.save(tmp_path)
        tmp_path.replace(output_path)

    def render(self, canvas: MSPDPCanvas, output_path: Path) -> None:
        image = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        for row in range(self.rows):
            for col in range(self.cols):
                x = col * self.cell_width
                y = row * self.cell_height
                cell_value = canvas.grid[row][col]

                glyph_index = cell_value if isinstance(cell_value, int) else None
                if glyph_index is not None:
                    glyph = self._glyph_from_charset(glyph_index)
                    if glyph is not None:
                        if glyph.size != (self.cell_width, self.cell_height):
                            glyph = glyph.resize(
                                (self.cell_width, self.cell_height),
                                Image.Resampling.NEAREST,
                            )
                        image.alpha_composite(glyph, dest=(x, y))
                        continue

                text = self._value_to_text(cell_value)
                if text == " ":
                    continue

                bbox = draw.textbbox((0, 0), text, font=self.font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
                text_x = x + max(0, (self.cell_width - text_w) // 2)
                text_y = y + max(0, (self.cell_height - text_h) // 2)
                draw.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=self.font)

        self._save_atomic(image, output_path)


def run(
    bind: str,
    port: int,
    cols: int,
    rows: int,
    width: int,
    height: int,
    fps: float,
    out: str,
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
    output_path = Path(out)

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
                renderer.render(canvas, output_path)
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
    parser.add_argument("--charset", default=None)
    parser.add_argument("--fps", type=float, default=20.0)
    parser.add_argument("--out", default="osd.png")
    # Backward-compatible alias.
    parser.add_argument("--output", dest="out_compat", default=None)
    args = parser.parse_args()

    out_path = args.out_compat or args.out

    run(
        bind=args.bind,
        port=args.port,
        cols=args.cols,
        rows=args.rows,
        width=args.width,
        height=args.height,
        fps=args.fps,
        out=out_path,
        charset=args.charset,
    )


if __name__ == "__main__":
    main()
