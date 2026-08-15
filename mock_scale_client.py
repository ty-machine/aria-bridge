#!/usr/bin/env python3
"""
Sends a synthetic Aria /scale/upload request to the interceptor so you can
verify the server end-to-end WITHOUT touching your router's DNS or your
real scale. Run this against the interceptor on localhost first.

Usage:
    python3 mock_scale_client.py [--host 127.0.0.1] [--port 8080] [--weight-kg 82.4]
"""
import argparse
import struct
import time
import urllib.request

from aria_interceptor import crc16_xmodem, HEADER_FMT, HEADER_SIZE, MEASUREMENT_FMT


def build_upload(weight_kg: float, user_id: int = 0, battery: int = 90) -> bytes:
    mac = bytes.fromhex("aabbccddeeff")
    auth_code = b"TESTAUTHCODE0000"[:16]
    now = int(time.time())

    header = struct.pack(
        HEADER_FMT,
        3,              # protocol_version
        battery,        # battery_percent
        mac,            # mac_address
        auth_code,      # auth_code
        39,             # firmware_version
        33,             # unknown2
        now,            # timestamp
        1,              # measurement_count
    )

    weight_g = int(weight_kg * 1000)
    measurement = struct.pack(
        MEASUREMENT_FMT,
        2,          # id2
        550,        # impedance
        weight_g,   # weight_g
        now,        # timestamp
        user_id,    # user_id (0 = guest)
        0, 0, 0,    # fat1, covariance, fat2
    )

    body = header + measurement
    crc = crc16_xmodem(body)
    return body + struct.pack("<H", crc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--weight-kg", type=float, default=82.4)
    ap.add_argument("--user-id", type=int, default=0)
    args = ap.parse_args()

    payload = build_upload(args.weight_kg, args.user_id)
    url = f"http://{args.host}:{args.port}/scale/upload"
    req = urllib.request.Request(url, data=payload, method="POST",
                                  headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        print(f"POST {url} -> {resp.status}")
        print(f"Response body: {len(resp.read())} bytes")

    latest_url = f"http://{args.host}:{args.port}/latest"
    with urllib.request.urlopen(latest_url, timeout=5) as resp:
        print("Server's /latest now reports:")
        print(resp.read().decode())


if __name__ == "__main__":
    main()
