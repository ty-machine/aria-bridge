#!/usr/bin/env python3
"""
Fitbit Aria (FB201B) traffic interceptor.

Stands in for www.fitbit.com on your LAN (via DNS redirection) and speaks
just enough of the scale's binary upload protocol to accept readings and
log them locally. Protocol reverse-engineered by the community — primarily
documented in micolous/helvetic's protocol.md, corroborated by
ads04r/aria-spoof and Wayne Piekarski's 2024 write-up.

This does NOT relay anything to Fitbit's real servers and does NOT check
the scale's auth_code — we simply accept any well-formed upload from a
device on your own network and always report back "you're all good, no
users configured, no firmware update." That's enough for the scale to
keep weighing you and keep uploading every time, without needing a live
Fitbit account or the (now largely dead) Fitbit cloud.

Endpoints implemented:
  GET  /scale/register   -> 200 OK (scale re-registers on wifi rejoin)
  GET  /scale/setup      -> 200 OK (only hit during a from-scratch re-pair)
  POST /scale/upload     -> parses the binary measurement blob, logs it,
                             returns a minimal valid response envelope
  GET  /latest            -> JSON of the most recent reading (for polling)
  GET  /readings          -> JSON array of all readings on file
"""
import csv
import json
import logging
import os
import struct
import time
from pathlib import Path

from flask import Flask, request, Response, jsonify

app = Flask(__name__)
log = logging.getLogger("aria")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATA_DIR = Path(os.environ.get("ARIA_DATA_DIR", Path(__file__).parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
JSONL_LOG = DATA_DIR / "readings.jsonl"
CSV_LOG = DATA_DIR / "readings.csv"
LATEST_FILE = DATA_DIR / "latest.json"
RAW_DIR = DATA_DIR / "raw_uploads"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# CRC-16/XMODEM (poly 0x1021, init 0x0000, no reflect, no final xor).
#
# An earlier version of this used init=0xFFFF, which is actually
# CRC-16/CCITT-FALSE -- a different, commonly-confused variant that
# shares XMODEM's polynomial. That produced a CRC mismatch on every real
# upload (see HANDOFF.md). Confirmed against a real captured upload
# (2026-08-16, /opt/aria_bridge/data/raw_uploads on the ubuntu box):
# init=0x0000 reproduces the scale's CRC exactly; init=0xFFFF does not.
# --------------------------------------------------------------------------
def crc16_xmodem(data: bytes) -> int:
    crc = 0x0000
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


# --------------------------------------------------------------------------
# Request parsing (aria_upload_request_envelope3 / _body3), little-endian.
# --------------------------------------------------------------------------
HEADER_FMT = "<I I 6s 16s I I I I"  # version, battery, mac, auth_code, fw, unknown2, timestamp, count
HEADER_SIZE = struct.calcsize(HEADER_FMT)

MEASUREMENT_FMT = "<I I I I I I I I"  # id2, impedance, weight_g, timestamp, user_id, fat1, covariance, fat2
MEASUREMENT_SIZE = struct.calcsize(MEASUREMENT_FMT)


class BadUpload(Exception):
    pass


# --------------------------------------------------------------------------
# Family member classification (weight-based heuristic).
#
# Guest mode (user_id=0) lumps every unregistered person on the scale
# together, so user_id alone can't tell family members apart. These weight
# bands (in lb) are a rough proxy instead -- adjust as needed (e.g. nudge
# "toddler" up as a kid grows). Only the "me" band gets pushed to Apple
# Health, by the iPhone Shortcuts automation that polls /latest/me --
# everyone else still gets logged to the CSV/JSON for reference, just not
# pushed to your personal Health app.
#
# (label, min_lb inclusive, max_lb exclusive)
# --------------------------------------------------------------------------
FAMILY_BANDS = [
    ("infant",  0,   20),
    ("toddler", 20,  70),
    ("wife",    70,  190),
    ("me",      190, 400),
]


def classify_person(weight_lb: float) -> str:
    for label, lo, hi in FAMILY_BANDS:
        if lo <= weight_lb < hi:
            return label
    return "unknown"


def parse_upload(body: bytes) -> dict:
    if len(body) < HEADER_SIZE + 2:
        raise BadUpload(f"body too short ({len(body)} bytes)")

    payload, crc_bytes = body[:-2], body[-2:]
    (received_crc,) = struct.unpack("<H", crc_bytes)
    computed_crc = crc16_xmodem(payload)
    crc_ok = received_crc == computed_crc
    if not crc_ok:
        log.warning("CRC mismatch: received=%04x computed=%04x (continuing anyway)",
                    received_crc, computed_crc)

    (protocol_version, battery_percent, mac_raw, auth_code_raw, firmware_version,
     unknown2, timestamp, measurement_count) = struct.unpack(HEADER_FMT, payload[:HEADER_SIZE])

    mac = mac_raw.hex()
    auth_code = auth_code_raw.decode("ascii", errors="replace").strip("\x00")

    bytes_after_header = len(payload) - HEADER_SIZE
    implied_count_from_size = bytes_after_header / MEASUREMENT_SIZE if MEASUREMENT_SIZE else 0
    log.info(
        "Header parsed: body_len=%d protocol_version=%s battery=%s%% mac=%s fw=%s "
        "unknown2=%s timestamp=%s measurement_count(from header)=%s "
        "bytes_after_header=%d (=%.2f measurements @ %d bytes each)",
        len(payload), protocol_version, battery_percent, mac, firmware_version,
        unknown2, timestamp, measurement_count, bytes_after_header,
        implied_count_from_size, MEASUREMENT_SIZE,
    )
    if measurement_count > 20 or implied_count_from_size != round(implied_count_from_size):
        log.warning(
            "measurement_count (%d) looks implausible or doesn't evenly divide the "
            "remaining %d bytes into %d-byte measurements -- our byte-offset "
            "assumptions likely don't match this firmware. Raw hex of full payload:\n%s",
            measurement_count, bytes_after_header, MEASUREMENT_SIZE, payload.hex(),
        )

    offset = HEADER_SIZE
    measurements = []
    for i in range(measurement_count):
        chunk = payload[offset:offset + MEASUREMENT_SIZE]
        if len(chunk) < MEASUREMENT_SIZE:
            log.warning(
                "Stopped after %d/%d measurements: only %d bytes left at offset %d "
                "(need %d). Remaining bytes as hex: %s",
                i, measurement_count, len(chunk), offset, MEASUREMENT_SIZE, chunk.hex(),
            )
            break
        (id2, impedance, weight_g, m_timestamp, user_id,
         fat1, covariance, fat2) = struct.unpack(MEASUREMENT_FMT, chunk)
        measurements.append({
            "weight_g": weight_g,
            "weight_kg": round(weight_g / 1000.0, 2),
            "weight_lb": round(weight_g / 1000.0 * 2.20462, 2),
            "impedance": impedance,
            "timestamp": m_timestamp,
            "user_id": user_id,
            "fat1": fat1,
            "covariance": covariance,
            "fat2": fat2,
        })
        offset += MEASUREMENT_SIZE

    if not measurements:
        log.warning("No measurements parsed out of this upload. Full raw payload hex:\n%s",
                    payload.hex())

    return {
        "protocol_version": protocol_version,
        "battery_percent": battery_percent,
        "mac": mac,
        "auth_code": auth_code,
        "firmware_version": firmware_version,
        "timestamp": timestamp,
        "crc_ok": crc_ok,
        "measurements": measurements,
    }


# --------------------------------------------------------------------------
# Response construction (aria_upload_envelope3 / _response_body3).
# We report: no users configured (guest mode, user_id 0), no update.
# --------------------------------------------------------------------------
def build_upload_response() -> bytes:
    now = int(time.time())
    units = 2       # kilograms
    status = 0x32   # "configured" -- keeps the scale happy, no setup nag
    unknown1 = 0x01
    user_count = 0
    update_available = 0x03  # no
    unknown2 = 3
    unknown3 = 0

    body = struct.pack("<I B B B I", now, units, status, unknown1, user_count)
    # (no user records, since user_count == 0)
    body += struct.pack("<I", update_available)
    # (no update_msg, since update_available == 0x03 / no)
    body += struct.pack("<I I", unknown2, unknown3)

    crc = crc16_xmodem(body)
    trailer = struct.pack("<H B B", crc, 0x66, 0x00)
    return body + trailer


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
def log_reading(parsed: dict):
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    for m in parsed["measurements"]:
        person = classify_person(m["weight_lb"])
        record = {
            "logged_at": now_iso,
            "mac": parsed["mac"],
            "firmware_version": parsed["firmware_version"],
            "battery_percent": parsed["battery_percent"],
            "crc_ok": parsed["crc_ok"],
            "person": person,
            **m,
        }
        with open(JSONL_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")

        write_header = not CSV_LOG.exists()
        with open(CSV_LOG, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(record.keys()))
            if write_header:
                writer.writeheader()
            writer.writerow(record)

        LATEST_FILE.write_text(json.dumps(record, indent=2))
        latest_person_file = DATA_DIR / f"latest_{person}.json"
        latest_person_file.write_text(json.dumps(record, indent=2))

        log.info("Logged reading: person=%s user_id=%s weight_kg=%.2f weight_lb=%.2f",
                  person, m["user_id"], m["weight_kg"], m["weight_lb"])


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.route("/scale/register", methods=["GET"])
def scale_register():
    log.info("Register: %s", dict(request.args))
    return Response(status=200)


@app.route("/scale/setup", methods=["GET"])
def scale_setup():
    log.info("Setup: %s", dict(request.args))
    return Response(status=200)


@app.route("/scale/upload", methods=["POST"])
def scale_upload():
    body = request.get_data()

    raw_path = RAW_DIR / f"{int(time.time())}_{len(body)}bytes.bin"
    raw_path.write_bytes(body)
    log.info("Saved raw upload (%d bytes) to %s", len(body), raw_path)

    try:
        parsed = parse_upload(body)
    except BadUpload as e:
        log.error("Failed to parse upload (%d bytes): %s -- raw hex:\n%s", len(body), e, body.hex())
        return Response(status=400)

    log_reading(parsed)

    resp_bytes = build_upload_response()
    return Response(resp_bytes, status=200, headers={
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(resp_bytes)),
    })


@app.route("/latest", methods=["GET"])
def latest():
    if not LATEST_FILE.exists():
        return jsonify({"error": "no readings yet"}), 404
    return Response(LATEST_FILE.read_text(), mimetype="application/json")


@app.route("/latest/<person>", methods=["GET"])
def latest_for_person(person):
    """Most recent reading for one family-band label (see FAMILY_BANDS),
    e.g. /latest/me. This is what the iPhone Shortcuts automation should
    point at, so only your own weigh-ins reach Apple Health."""
    person_file = DATA_DIR / f"latest_{person}.json"
    if not person_file.exists():
        return jsonify({"error": f"no readings yet for '{person}'"}), 404
    return Response(person_file.read_text(), mimetype="application/json")


@app.route("/readings", methods=["GET"])
def readings():
    if not JSONL_LOG.exists():
        return jsonify([])
    out = [json.loads(line) for line in JSONL_LOG.read_text().splitlines() if line.strip()]
    person = request.args.get("person")
    if person:
        out = [r for r in out if r.get("person") == person]
    return jsonify(out)


if __name__ == "__main__":
    port = int(os.environ.get("ARIA_PORT", "80"))
    log.info("Aria interceptor listening on 0.0.0.0:%d, data dir: %s", port, DATA_DIR)
    app.run(host="0.0.0.0", port=port)
