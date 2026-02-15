#!/usr/bin/env python3
from __future__ import annotations

import argparse
import curses
import socket
import time

from msp_proto import parse_msp_v1
from displayport import (
    Canvas,
    DP_HEARTBEAT, DP_RELEASE, DP_CLEAR_SCREEN, DP_WRITE_STRING, DP_DRAW_SCREEN
)

MSP_DISPLAYPORT = 0xB6

def run(stdscr, bind: str, port: int, cols: int, rows: int):
    stdscr.nodelay(True)
    curses.curs_set(0)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((bind, port))
    s.settimeout(0.2)

    canvas = Canvas(cols=cols, rows=rows)

    last_render = 0.0
    pkts = 0
    bad = 0
    last_info = time.time()
    dp_counts = {DP_HEARTBEAT:0, DP_CLEAR_SCREEN:0, DP_WRITE_STRING:0, DP_DRAW_SCREEN:0, DP_RELEASE:0}

    def render(force: bool = False):
        nonlocal last_render
        now = time.time()
        if not force and (now - last_render) < 0.05 and not canvas.dirty:
            return
        last_render = now
        canvas.dirty = False

        # заголовок
        stdscr.erase()
        hdr = f"MSP DisplayPort TUI  udp://{bind}:{port}  size={cols}x{rows}  frame={canvas.frame}  pkts={pkts} bad={bad}"
        stdscr.addstr(0, 0, hdr[:max(0, cols-1)])

        # экран
        for r in range(rows):
            line = "".join(canvas.grid[r])
            stdscr.addstr(1 + r, 0, line[:cols])

        # статусная строка
        info = f"HB:{dp_counts[DP_HEARTBEAT]} CLR:{dp_counts[DP_CLEAR_SCREEN]} WSTR:{dp_counts[DP_WRITE_STRING]} DRAW:{dp_counts[DP_DRAW_SCREEN]}  (q=quit)"
        stdscr.addstr(1 + rows, 0, info[:max(0, cols-1)])

        stdscr.refresh()

    while True:
        # клавиши
        ch = stdscr.getch()
        if ch in (ord('q'), ord('Q')):
            break

        try:
            data, _ = s.recvfrom(4096)
        except socket.timeout:
            render()
            continue

        f = parse_msp_v1(data)
        if not f:
            continue
        if not f.csum_ok:
            bad += 1
            continue
        if f.cmd != MSP_DISPLAYPORT:
            continue

        pkts += 1
        if not f.payload:
            continue

        sub = f.payload[0]
        dp_counts[sub] = dp_counts.get(sub, 0) + 1

        if sub == DP_HEARTBEAT:
            # ничего не делаем
            pass
        elif sub == DP_RELEASE:
            canvas.clear()
        elif sub == DP_CLEAR_SCREEN:
            canvas.clear()
        elif sub == DP_WRITE_STRING:
            # payload: sub, row, col, attr, string...
            if len(f.payload) >= 4:
                row = f.payload[1]
                col = f.payload[2]
                rest = f.payload[3:]  # attr + string...
                canvas.write_string(row, col, rest)
        elif sub == DP_DRAW_SCREEN:
            canvas.draw()
            # обычно это "commit" — можно рендерить сразу
            render(force=True)

        # раз в секунду обновим даже если DRAW нет
        if time.time() - last_info > 1.0:
            last_info = time.time()
            render()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=14560)
    ap.add_argument("--cols", type=int, default=60)
    ap.add_argument("--rows", type=int, default=22)
    args = ap.parse_args()

    curses.wrapper(run, args.bind, args.port, args.cols, args.rows)

if __name__ == "__main__":
    main()
