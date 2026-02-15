from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class MSPv1Frame:
    direction: bytes  # b"$M>" / b"$M<" / b"$M!"
    cmd: int
    payload: bytes
    csum_ok: bool

def parse_msp_v1(buf: bytes) -> MSPv1Frame | None:
    # MSPv1: $M> len cmd payload csum
    if len(buf) < 6:
        return None
    direction = buf[0:3]
    if direction not in (b"$M>", b"$M<", b"$M!"):
        return None

    ln = buf[3]
    cmd = buf[4]
    need = 3 + 1 + 1 + ln + 1
    if len(buf) < need:
        return None

    payload = buf[5:5 + ln]
    csum = buf[5 + ln]

    chk = (ln ^ cmd) & 0xFF
    for b in payload:
        chk ^= b
    ok = (chk & 0xFF) == csum

    return MSPv1Frame(direction=direction, cmd=cmd, payload=payload, csum_ok=ok)
