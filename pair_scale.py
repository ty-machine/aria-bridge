#!/usr/bin/env python3
"""
pair_scale.py — re-pair a Fitbit Aria (FB201B) to a Wi-Fi network.

This talks to the scale itself over its own temporary setup network, per
the documented Aria protocol (micolous/helvetic protocol.md, step 3):

    GET http://192.168.240.1/scale/setup?ssid=...&custom_password=...
    Cookie: token=<anything>

The "token" here isn't checked by the scale in any meaningful way for
this step — it's just carried along and later echoed back to
`/scale/register` on whatever host "www.fitbit.com" resolves to for the
scale. If you've already got the interceptor server + DNS redirect from
this kit running, that registration call will land there and succeed
automatically. If you haven't set that up yet, that's fine too — this
step still saves the Wi-Fi credentials to the scale and it will join your
network; registration just won't complete until the interceptor is live
(the scale should keep retrying quietly in the background without
bricking anything, matching how it's already been running unregistered).

HOW TO USE THIS SCRIPT
-----------------------
1. On the scale: pull the batteries, wait 10 seconds, put them back in.
   The display should show "HI :)" then its firmware version then
   "SETUP ACTIVE" — that means it's broadcasting an open Wi-Fi network
   named something like "Aria XXXXXX" (X's = last 3 bytes of its MAC
   address, printed on a sticker underneath the scale if you need to
   confirm which network is it).

2. On the machine you're running this script from (needs Wi-Fi you
   control manually — join it the normal way via your Wi-Fi menu):
   connect to that "Aria XXXXXX" network. You'll likely lose internet
   access on this machine while connected to it — that's expected, it's
   a bare local AP served by the scale.

3. Run this script:

       python3 pair_scale.py --ssid "YourHomeWifiName"

   It'll prompt you for the Wi-Fi password (not shown on screen, and not
   passed as a command-line argument, so it doesn't end up in your shell
   history).

4. Once it reports success, reconnect this machine to your normal Wi-Fi.
   The scale should drop the "Aria XXXXXX" network within a few seconds
   and join the network you gave it.
"""
import argparse
import getpass
import socket
import sys
import urllib.parse
import uuid

SCALE_HOST = "192.168.240.1"
SCALE_PORT = 80
SCALE_SETUP_PATH = "/scale/setup"


def pair(ssid: str, password: str, token: str) -> None:
    """
    Sends the setup GET over a raw socket instead of urllib/http.client.

    The scale's embedded HTTP server declares "Transfer-Encoding: chunked"
    but doesn't actually send valid chunk framing, which makes Python's
    strict http.client parser blow up with IncompleteRead/ValueError while
    reading the body -- even though the request itself succeeded and the
    status line came back 200. Since we don't need the body for anything,
    we just grab whatever bytes show up, pull the status line off the
    front, and ignore the rest.
    """
    params = urllib.parse.urlencode({"ssid": ssid, "custom_password": password})
    request_line = f"GET {SCALE_SETUP_PATH}?{params} HTTP/1.1\r\n"
    headers = (
        f"Host: {SCALE_HOST}\r\n"
        f"Cookie: token={token}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    raw_request = (request_line + headers).encode("ascii")

    print(f"Sending Wi-Fi credentials to the scale at http://{SCALE_HOST}{SCALE_SETUP_PATH} ...")
    try:
        with socket.create_connection((SCALE_HOST, SCALE_PORT), timeout=8) as sock:
            sock.sendall(raw_request)
            sock.settimeout(5)
            chunks = []
            try:
                while True:
                    data = sock.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
            except socket.timeout:
                pass  # scale never closes cleanly -- that's fine, we have what we need
            raw_response = b"".join(chunks)
    except OSError as e:
        print(f"Could not reach the scale at {SCALE_HOST} ({e}).")
        print("Are you actually connected to the scale's 'Aria XXXXXX' Wi-Fi network right now?")
        sys.exit(1)

    if not raw_response:
        print("Connected, but got no response at all. The scale may not be in setup mode.")
        sys.exit(1)

    status_line = raw_response.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    print(f"Scale responded: {status_line}")

    if " 200 " not in status_line and not status_line.endswith(" 200"):
        print("That's not a 200 -- the scale likely rejected the request. Double-check you're")
        print("connected to its 'Aria XXXXXX' setup network, then try again.")
        sys.exit(1)

    print()
    print(f"Credentials accepted. The scale is now trying to join '{ssid}'.")
    print("Reconnect this machine to your normal Wi-Fi now.")
    print(f"(token used, in case you need it again: {token})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ssid", required=True, help="The Wi-Fi network name you want the scale to join")
    ap.add_argument("--password", help="Wi-Fi password (omit this and you'll be prompted securely instead)")
    ap.add_argument("--token", help="Registration token to hand the scale (default: random, since it isn't validated)")
    args = ap.parse_args()

    password = args.password or getpass.getpass(f"Wi-Fi password for '{args.ssid}': ")
    token = args.token or uuid.uuid4().hex[:16]

    pair(args.ssid, password, token)


if __name__ == "__main__":
    main()
