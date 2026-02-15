#!/usr/bin/env python3
import sys
import time
import socket
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyAMA0"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
UDP_HOST = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
UDP_PORT = int(sys.argv[4]) if len(sys.argv) > 4 else 14560

# MSPv1 packet: $M< [len] [cmd] [payload...] [csum]
def msp_v1(cmd: int, payload: bytes = b"") -> bytes:
    hdr = b"$M<"
    ln = len(payload)
    chk = (ln ^ cmd) & 0xFF
    for b in payload:
        chk ^= b
    return hdr + bytes([ln, cmd]) + payload + bytes([chk])

def read_one_msp_reply(ser: serial.Serial, timeout_s: float = 0.5) -> bytes | None:
    end = time.time() + timeout_s
    buf = bytearray()

    while time.time() < end:
        b = ser.read(1)
        if not b:
            continue
        buf += b

        # Look for "$M>"
        if len(buf) >= 3 and buf[-3:] == b"$M>":
            ln_b = ser.read(1)
            cmd_b = ser.read(1)
            if not ln_b or not cmd_b:
                return None
            ln = ln_b[0]
            payload = ser.read(ln)
            csum = ser.read(1)
            if not csum:
                return None
            return b"$M>" + ln_b + cmd_b + payload + csum

        # Prevent runaway buffer growth on noise
        if len(buf) > 4096:
            buf = buf[-64:]

    return None

def tx_and_forward(sock, ser, udp_addr, cmd, payload=b"", label=""):
    ser.reset_input_buffer()
    ser.write(msp_v1(cmd, payload))
    ser.flush()

    rep = read_one_msp_reply(ser, timeout_s=0.7)
    if rep:
        sock.sendto(rep, udp_addr)
        print(f"{label} reply {len(rep)} bytes -> UDP")
        return True
    else:
        print(f"{label} no reply")
        return False

def main():
    udp_addr = (UDP_HOST, UDP_PORT)
    print(f"UART {PORT}@{BAUD} -> UDP {UDP_HOST}:{UDP_PORT}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    with serial.Serial(PORT, BAUD, timeout=0.02) as ser:
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        # Prove MSP link and generate UDP right away
        tx_and_forward(sock, ser, udp_addr, 0x01, b"", "API_VERSION")
        tx_and_forward(sock, ser, udp_addr, 0x03, b"", "FC_VERSION")

        # DisplayPort probing/polling candidates (BF versions differ)
        dp_candidates = [
            ("DP_CMD_BC", 0xBC, b""),
            ("DP_CMD_BD", 0xBD, b"\x00"),
            ("DP_CMD_BE", 0xBE, b"\x00"),
        ]

        idx = 0
        while True:
            name, cmd, pl = dp_candidates[idx % len(dp_candidates)]
            idx += 1

            ser.write(msp_v1(cmd, pl))
            ser.flush()

            # Forward any replies that arrive in a small time window
            t0 = time.time()
            got = 0
            while time.time() - t0 < 0.05:
                rep = read_one_msp_reply(ser, timeout_s=0.05)
                if rep:
                    sock.sendto(rep, udp_addr)
                    got += 1

            if got:
                print(f"{name}: forwarded {got} replies")
            time.sleep(0.02)

if __name__ == "__main__":
    main()

