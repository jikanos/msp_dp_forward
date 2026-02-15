# MSP DisplayPort UART → UDP Forwarder

Forward MSP (Betaflight) replies from UART to UDP.

Designed for:
- Betaflight MSP DisplayPort
- wfb-ng telemetry pipeline
- Ground Station OSD rendering

## Requirements

sudo apt install python3-serial

## Usage

sudo ./msp_dp_forward.py /dev/ttyAMA0 115200 192.168.31.89 14560

Arguments:

1. Serial device (default: /dev/ttyAMA0)
2. Baudrate (default: 115200)
3. UDP target IP
4. UDP target port (default: 14560)

## Debug

Check incoming UDP traffic:

sudo tcpdump -n -i any udp port 14560
