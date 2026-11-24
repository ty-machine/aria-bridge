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

## Running it

The supported way to run this is as a container in the homelab k3s cluster: `Dockerfile` here,
deployment and DNS in `ty-machine/homelab` (see `docs/runbooks/aria-bridge.md` there). The systemd,
LaunchDaemon and dnsmasq files under `ubuntu/` and `mac/` are the earlier host-based setup, kept for
reference. The local test in step 1 below still works as written.

## How it fits together

```
FB201B scale --(plain HTTP, "www.fitbit.com")--> [DNS redirect] --> interceptor box
                                                                    aria_interceptor.py
                                                                    (parses + logs reading,
                                                                     serves /latest/me)
                                                                            |
                                                                    pulled once a day by a
                                                                    Shortcuts Personal
                                                                    Automation on your iPhone
                                                                    ("Get Contents of URL" ->
                                                                     "Log Health Sample")
                                                                            |
                                                                    iPhone Health app
```

There is no native Health app for Mac — that was a wrong assumption in an
earlier version of this doc. `HealthKit.framework` and the `healthd`
daemon exist on macOS, but nothing exposes a "Log Health Sample" action
to Shortcuts without the (nonexistent) app, so the Health-logging step
happens entirely on your iPhone instead — see step 4.

**A family scale note:** the Aria's "guest" mode (`user_id=0`) doesn't
distinguish between people — everyone who steps on without a registered
profile shows up as the same guest. If more than one person (or kid) uses
the scale, `aria_interceptor.py` sorts readings into family bands purely
by weight (see `FAMILY_BANDS` near the top of the file — edit the ranges
to match your household). Every reading still gets logged to
`data/readings.csv`/`readings.jsonl` regardless of band, but the iPhone
automation hits `/latest/me` specifically, so only your own weigh-ins
ever reach your Apple Health — not your kids' or your partner's. Other
bands are retrievable the same way (`/latest/wife`, `/latest/toddler`,
etc.) if you ever want to build a second pipeline for someone else.

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
- The Health-logging piece has no file here — it's a Shortcuts Personal
  Automation you build directly on your iPhone (step 4 below). There used
  to be a Mac-side poller script for this; it's retired, see step 4 for
  why.

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

### 4. Build the Health-logging automation on your iPhone

There's no native Health app for Mac, so this whole step happens on your
iPhone instead of the Mac (an earlier version of this doc assumed
otherwise — that was wrong). Open **Shortcuts** on your iPhone, go to the
**Automation** tab, and create a new **Personal Automation**:

1. **Trigger: Time of Day** — set to whenever you typically weigh in
   (e.g. 7:00 AM), repeating **Daily**. iOS doesn't allow a background
   automation to poll every few minutes the way the old Mac LaunchAgent
   did, so this checks once a day instead — fine for a once-a-day
   weigh-in.
2. **Actions**, in order:
   - **Get Contents of URL** — GET request to
     `http://<interceptor-address>/latest/me` (the address in
     `ARIA_SERVER_URL` in your `mise.local.toml`).
   - **Get Dictionary from Input** — from the URL contents above.
   - **Get Dictionary Value** — key `logged_at`, from the dictionary.
   - **Format Date** — Input: the `logged_at` value from the step above,
     Custom Format `yyyy-MM-dd`. Call this result `ReadingDate`.
   - **Format Date** — Input: **Current Date**, Custom Format
     `yyyy-MM-dd`. Call this result `TodayDate`.
   - **If** `ReadingDate` equals `TodayDate`:
     - **Get Dictionary Value** — key `weight_kg`, from the original
       dictionary (not the formatted dates).
     - **Log Health Sample** — Type: *Weight*, Unit: *Kilograms*, Sample:
       the `weight_kg` value above. Leave Date blank (defaults to now).
   - **Otherwise**: leave empty — this skips logging when the
     interceptor's last reading isn't from today (nobody's weighed in
     yet, or the automation fired before you stepped on the scale).
3. Turn off **"Ask Before Running"** for this automation (toggle at the
   bottom when editing it), otherwise iOS shows a confirmation banner
   every time instead of running silently in the background.

Run it once by hand (open the automation, tap **Run**) to confirm it
prompts you to grant Health-write access, and that a "Weight" entry shows
up in the iPhone Health app afterward.

Note: `logged_at` is the interceptor box's local time, not the phone's —
fine as long as both are in the same timezone, which they will be for a
household setup like this.

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
