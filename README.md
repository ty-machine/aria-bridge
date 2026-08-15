# Aria Bridge — keep your FB201B talking, get its data into Apple Health

Your Fitbit Aria (FB201B) still weighs you fine; what's dead is Fitbit's
side of the pipe (Google discontinued official support for the original
Aria around 2022–23, then shut the whole fitbit.com web dashboard on
July 8, 2024). The scale still tries to phone home to `www.fitbit.com`
over plain HTTP, forever, whether or not anyone's listening. This kit
puts something back on the other end of that connection — one you
control — and forwards the data into Apple Health.

No certificate is involved anywhere in this: the scale's upload protocol
is unencrypted HTTP, so there's nothing to spoof or work around there.
The whole trick is DNS — telling your network that `www.fitbit.com` lives
at your Ubuntu box's IP instead of Google's.

## How it fits together

```
FB201B scale --(plain HTTP, "www.fitbit.com")--> [DNS redirect] --> Ubuntu box
                                                                    aria_interceptor.py
                                                                    (parses + logs reading,
                                                                     writes data/latest.json)
                                                                            |
                                                                     polled every 5 min by
                                                                            |
                                                                         your Mac
                                                                    poll_aria.sh + Shortcut
                                                                    "Log Health Sample"
                                                                            |
                                                                     macOS Health app
                                                                            |
                                                             iCloud Health sync (built-in)
                                                                            |
                                                                    iPhone Health app
```

**A family scale note:** the Aria's "guest" mode (`user_id=0`) doesn't
distinguish between people — everyone who steps on without a registered
profile shows up as the same guest. If more than one person (or kid) uses
the scale, `aria_interceptor.py` sorts readings into family bands purely
by weight (see `FAMILY_BANDS` near the top of the file — edit the ranges
to match your household). Every reading still gets logged to
`data/readings.csv`/`readings.jsonl` regardless of band, but the poller
hits `/latest/me` specifically, so only your own weigh-ins ever reach
your Apple Health — not your kids' or your partner's. Other bands are
retrievable the same way (`/latest/wife`, `/latest/toddler`, etc.) if you
ever want to build a second pipeline for someone else.

We don't relay anything to Fitbit's real servers, and we don't check the
scale's `auth_code` — the server just accepts any well-formed upload from
your own LAN and always replies "you're all set, no update needed."
That's sufficient because the goal here is Apple Health, not keeping a
Fitbit account alive.

## What's in this folder

- `aria_interceptor.py` — the server. Implements `/scale/register`,
  `/scale/setup`, and `/scale/upload` per the protocol documented in
  micolous/helvetic's `protocol.md`, corroborated by ads04r/aria-spoof and
  Wayne Piekarski's Dec 2024 write-up. Verifies the CRC-16/XMODEM
  checksum, decodes weight/impedance/user_id, logs to
  `data/readings.jsonl` and `data/readings.csv`, and exposes `/latest`
  and `/readings` for polling.
- `mock_scale_client.py` — sends a synthetic upload so you can test the
  server without touching DNS or the real scale. Already verified working
  end-to-end (CRC check, parsing, logging, `/latest`) in development.
- `aria-interceptor.service` — systemd unit for running the server on the
  Ubuntu box.
- `dnsmasq_aria.conf` — the DNS redirect config.
- `poll_aria.sh` + `com.aria.bridge.poller.plist` — the Mac-side piece
  that polls the interceptor and triggers the Health-logging Shortcut.

## Setup order

### 1. Test locally on your Mac first (no DNS changes yet)

```
pip3 install flask
cd aria_bridge
ARIA_PORT=8080 python3 aria_interceptor.py &
python3 mock_scale_client.py --host 127.0.0.1 --port 8080 --weight-kg 82.4
cat data/latest.json     # should show your fake 82.4kg reading
kill %1
```

If that works, the parsing/logging logic is solid — Ubuntu will run the
identical code.

### 2. Deploy to the Ubuntu box

```
scp -r aria_bridge/ you@ubuntu-box:/opt/aria_bridge
ssh you@ubuntu-box
sudo cp /opt/aria_bridge/aria-interceptor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aria-interceptor
sudo systemctl status aria-interceptor
```

The scale talks to port 80 (`http://www.fitbit.com/...`), so the service
runs as root to bind it. If you'd rather not run as root, set
`ARIA_PORT=8080` and NAT/redirect port 80→8080 at the router or with
`iptables`/`nft` instead.

Test again with the mock client, now pointed at the box's real IP:

```
python3 mock_scale_client.py --host <ubuntu-box-ip> --port 80 --weight-kg 82.4
curl http://<ubuntu-box-ip>/latest
```

### 3. Redirect DNS so the real scale finds your box

Edit `dnsmasq_aria.conf` with your box's actual IP, then install it per
the comments in that file (either on the Ubuntu box's own dnsmasq if your
router hands that box out as the DNS server, or directly in your router's
DNS-override screen).

If the scale is **already joined to a network you're not changing**, a
plain power cycle is enough to make it re-resolve `www.fitbit.com` against
the new DNS entry and start hitting your interceptor. Watch
`sudo journalctl -u aria-interceptor -f` on the Ubuntu box for incoming
requests, and check `data/latest.json`.

If you need it to join a **different** Wi-Fi network — new router, moved
house, changed the SSID/password, whatever — that's a separate step,
covered next.

### 3b. Changing which Wi-Fi network the scale connects to

Yes, this is possible and is just the scale's normal (documented) pairing
flow — it's not locked to the network it first met. Use `pair_scale.py`:

1. On the scale: pull both batteries, wait 10 seconds, reinsert them. The
   display cycles "HI :)" → firmware version → "SETUP ACTIVE". It's now
   broadcasting an open Wi-Fi network named `Aria XXXXXX` (last 3 bytes
   of its MAC, on a sticker underneath the scale if you want to confirm).
2. On your Mac (or any laptop), manually join that `Aria XXXXXX` network
   via the normal Wi-Fi menu. You'll lose internet on that machine while
   connected — expected, it's a bare AP served by the scale itself.
3. Run:
   ```
   python3 pair_scale.py --ssid "YourNetworkName"
   ```
   It'll prompt for the Wi-Fi password (not echoed, not passed as a CLI
   arg, so it never lands in shell history) and send it straight to the
   scale.
4. Reconnect your Mac to your normal network. The scale drops its setup
   AP within a few seconds and joins the network you gave it.

The registration token this sends along isn't checked for anything real
— the interceptor accepts any token — so it works whether or not you've
finished steps 1–3 above yet. If the interceptor and DNS redirect are
already live, registration completes cleanly right away; if not, the
scale still saves the new Wi-Fi credentials and joins the network, it'll
just quietly keep retrying registration until your interceptor is up.

### 4. Build the Health-logging Shortcut on your Mac

macOS Sonoma (14) or later has the Health app on Mac, which is what makes
this whole chain possible without an iOS app. Open **Shortcuts** on your
Mac and create a new shortcut named exactly `Aria Weight to Health`
(the poller script calls it by this name):

1. **Get Dictionary from Input** — leave input as "Shortcut Input."
2. **Get Dictionary Value** — Get value for key `weight_kg`, from the
   dictionary above.
3. **Log Health Sample** — Type: *Weight*, Unit: *Kilograms*, Sample:
   the dictionary value from step 2. Leave Date blank (it defaults to
   "now," which is close enough since this runs within minutes of the
   actual weigh-in).

Run it once manually with a test file to confirm it prompts you to grant
Health-write access, and that a "Weight" entry shows up in the Health app
afterward:

```
echo '{"weight_kg": 82.4}' > /tmp/test.json
shortcuts run "Aria Weight to Health" --input-path /tmp/test.json
```

Then, on both this Mac and your iPhone: Health app → your profile icon
(top right) → make sure Health data sync via iCloud is turned on. That's
the "cloud" leg — it's what actually moves the entry from your Mac's
Health app to your iPhone's.

### 5. Install the poller

Edit `poll_aria.sh`, set `SERVER` to your Ubuntu box's address, then:

```
mkdir -p ~/aria_bridge && cp poll_aria.sh com.aria.bridge.poller.plist ~/aria_bridge/
chmod +x ~/aria_bridge/poll_aria.sh
# edit the plist's ProgramArguments path to match, then:
cp ~/aria_bridge/com.aria.bridge.poller.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aria.bridge.poller.plist
```

It checks every 5 minutes for a newer reading than the last one it
logged, and only fires the Shortcut when there's something new — no
duplicate entries from repeated polling.

## Notes and caveats

- This only touches traffic between your own scale and your own server on
  your own network — no Fitbit account is accessed, spoofed, or logged
  into anywhere in this chain.
- Community background, for reference: `micolous/helvetic` (protocol
  docs + a fuller Django-based replacement server, WIP),
  `ads04r/aria-spoof` (PHP MITM approach, archived Aug 2025), and
  [Wayne Piekarski's write-up](https://www.waynepiekarski.net/blog/2024/12/fitbit-mitm.htm)
  (the most recent working version, Dec 2024, which additionally relays
  to Fitbit's real servers — this kit intentionally doesn't, since the
  destination here is Apple Health, not a Fitbit account).
- If you ever want the response to include real user profiles (so the
  scale's own tiny display shows a name instead of operating in guest
  mode), the `aria_user` struct is documented in the comments of
  `aria_interceptor.py`'s `build_upload_response()` — it's a bigger
  binary structure than what's implemented, so I kept it out for now
  since guest-mode weighing already captures every reading regardless of
  who's on the scale.
