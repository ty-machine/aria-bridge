# Aria Bridge — project handoff

Paste this whole document as your first message in a new Claude Code
session (or save it as `CLAUDE.md` in the project folder so it loads
automatically). It covers everything learned and built so far, current
status, and exactly what's left.

## Goal

Keep a discontinued Fitbit Aria (FB201B) Wi-Fi smart scale working after
Fitbit/Google killed the backend it depends on, and get weigh-in data
into Apple Health.

## Key findings (don't re-research these)

- **FB201B = the original Fitbit Aria Wi-Fi Smart Scale** (black, 2012).
- **It's not a certificate problem.** The scale's upload protocol is
  plain, unencrypted HTTP to `www.fitbit.com` — nothing to spoof or
  work around at the TLS layer. Fitbit stopped official software support
  for this model around 2022–23, and Google shut down the entire
  fitbit.com web dashboard on July 8, 2024. The scale just keeps trying
  to phone home to a domain that no longer answers the way it expects.
- **Protocol**, reverse-engineered by the community (`micolous/helvetic`'s
  `protocol.md`, corroborated by `ads04r/aria-spoof` and
  [Wayne Piekarski's Dec 2024 write-up](https://www.waynepiekarski.net/blog/2024/12/fitbit-mitm.htm)):
  little-endian binary blobs over HTTP. `GET /scale/register`,
  `GET /scale/setup` (pairing only), `POST /scale/upload` (the actual
  measurement data). Full struct layout is documented in comments at the
  top of `aria_interceptor.py`.
- **CRC checksum**: implemented as CRC-16/XMODEM (poly 0x1021, seed
  0xFFFF) per the community docs, but it does NOT match what this
  scale's actual firmware (v39) computes — every real upload shows a CRC
  mismatch. We deliberately don't reject on CRC failure, so it's
  harmless, but the exact algorithm this firmware uses is still unknown.
  Not worth chasing further unless you want strict validation for its
  own sake.
- **Measurement struct layout is confirmed correct** — verified against
  real hardware: a real backlog upload of 17 cached measurements parsed
  100% cleanly with plausible weights (see "Real data captured" below).
- **Setup/pairing quirk**: the scale's embedded HTTP server sends a
  broken `Transfer-Encoding: chunked` response (claims chunked, doesn't
  send valid chunk framing) when you POST to it directly during pairing.
  Python's `http.client`/`urllib` will crash trying to parse it even
  though the request itself succeeded. `pair_scale.py` works around this
  with a raw socket instead of `urllib` — reuse that pattern if you ever
  talk to the scale's local setup endpoint (`192.168.240.1`) directly
  again.
- **Guest mode doesn't distinguish people.** `user_id=0` (unregistered
  "guest") is shared by everyone who steps on without a registered
  profile — a real backlog dump showed the household's dad, toddler, and
  infant all logged as the same guest ID. `aria_interceptor.py` works
  around this with a weight-based classifier (`FAMILY_BANDS`), not
  anything from the scale itself.
- **The real protocol could support native per-person recognition** (the
  `aria_user` response struct has `min_weight_tolerance`/
  `max_weight_tolerance` fields, suggesting the original backend sent
  down profiles and the scale matched weight to them itself, possibly
  showing names on its display) — this is NOT implemented. Explicitly
  deferred; current approach (server-side weight bucketing) was judged
  good enough.
- **Copy-pasting config out of a chat UI is risky.** A dnsmasq config
  line got corrupted into markdown hyperlink syntax (`[text](url)`) once
  when copied from a rendered chat response. Always type config directly
  or use a `heredoc` (`cat <<'EOF' ... EOF`) rather than copy/pasting
  from rendered text.
- **Testing DNS with `dig @<server-ip>` from the same machine that runs
  the DNS server can give a false pass** — it can succeed even when a
  firewall would block other devices on the LAN from reaching it. Always
  test from a *different* device.
- **Don't run this on a multi-purpose box.** It was briefly deployed on
  `master03`, a Kubernetes cluster node, and pointing the whole
  household's DNS at it caused intermittent "sites fail to load" —
  almost certainly kube-proxy iptables rule processing / cluster
  resource contention adding just enough latency to blow past client DNS
  timeouts. Moved to a dedicated Mac Mini instead. Any shared/critical
  box is the wrong place for this.

## Network specifics

- Router: **Ubiquiti UDM SE**. Its built-in "Local DNS Record" / Policy
  Engine DNS override feature does **not** actually override domains
  that already have a real upstream answer (confirmed via `dig` showing
  the real Fitbit CNAME chain despite a record being configured) — don't
  waste time on that path again. Use `dnsmasq` + the UDM's **DHCP Name
  Server** setting (Settings → Networks → [network] → DHCP Name Server)
  instead.
- Home Wi-Fi SSID: `TheGrid`.
- Scale's registered `serialNumber` (its MAC, no colons): `20f85ee9a257`.
  Firmware version: `39`.
- Old box (being phased out for this): `master03`, hostname on the LAN,
  a Kubernetes cluster node — do not deploy DNS/interceptor infra back
  onto it.
- New dedicated box: a Mac Mini, Apple Silicon, SSH (Remote Login)
  already enabled, Homebrew already installed.
- Family weight bands in `FAMILY_BANDS` (`aria_interceptor.py`), in lb:
  infant 0–20, toddler 20–70, wife 70–190, me (Ty) 190–400. Adjust as
  kids grow.

## Real data already captured

A real backlog upload from the physical scale (2026-08-14) parsed 17
measurements cleanly, confirming the byte layout is correct. Values
spanned two adults and two kids' historical weigh-ins (the scale had
been caching locally while it had no working backend to talk to). The
scale's internal clock was stuck at Feb 29 2016 (last legitimate time
sync) — our response now hands it the real current time on every upload,
which should self-correct going forward.

## What's built (all in the delivered `aria_bridge.zip` / `aria_bridge/` folder)

- `aria_interceptor.py` — Flask server standing in for `www.fitbit.com`.
  Parses `/scale/upload`, logs to `data/readings.jsonl` +
  `data/readings.csv`, classifies each reading into a `FAMILY_BANDS`
  bucket, saves raw upload bytes to `data/raw_uploads/` for forensics,
  serves `/latest`, `/latest/<person>`, `/readings` (optionally
  `?person=`).
- `mock_scale_client.py` — synthetic upload sender for testing without
  touching DNS or the real scale.
- `pair_scale.py` — re-pairs the scale to a new Wi-Fi network (raw
  socket, works around the chunked-response bug).
- `aria-interceptor.service` — systemd unit (Linux boxes).
- `com.aria.interceptor.plist` — LaunchDaemon (macOS boxes, e.g. the Mac
  Mini).
- `dnsmasq_aria.conf` — reference DNS override config (see README for
  the fuller version with explicit upstream forwarders, which is what
  actually needs to be deployed).
- `poll_aria.sh` + `com.aria.bridge.poller.plist` — runs on Ty's
  everyday Mac (NOT the Mac Mini), polls `/latest/me` on the interceptor,
  triggers the `Aria Weight to Health` Shortcut on new readings via
  `shortcuts run`.
- `README.md` — full setup instructions, including the Mac Mini
  migration section.
- (Delivered separately, not in the zip) `redeploy.sh` — pushes updated
  code to the Ubuntu-style box over rsync+ssh, restarts the systemd
  service. `redeploy_mac_mini.sh` — same idea, targets `launchd` on the
  Mac Mini instead.

## Current status: mid-migration off `master03` onto the Mac Mini

In progress when this handoff was written. Outstanding steps, in order:

1. **Confirm `master03` is fully cleaned up**: `aria-interceptor` and
   `dnsmasq` services stopped+disabled, `/etc/dnsmasq.d/aria.conf`
   removed, and the UDM SE's DHCP Name Server reverted to **Auto**.
   Verify the household network is calm again before continuing.
2. **Reserve a fixed IP for the Mac Mini** in UniFi (Client Devices →
   Mac Mini → Fixed IP).
3. **Install + configure dnsmasq via Homebrew on the Mac Mini** per the
   README's "Migrating from a shared box to a dedicated one" section —
   `brew install dnsmasq`, replace `$(brew --prefix)/etc/dnsmasq.conf`
   with the listen-address/no-resolv/server=/address= config, `sudo brew
   services start dnsmasq`. Verify with `dig @<mac-mini-ip>` for both
   `www.fitbit.com` and a normal domain — **from a different device**,
   not the Mini itself.
4. **Run `redeploy_mac_mini.sh`** (edit `SSH_USER`/`SSH_HOST` at the top
   first) to deploy the interceptor and install it as a LaunchDaemon.
   Confirm with `mock_scale_client.py` against the Mini's real IP before
   trusting it with the real scale.
5. **Point the UDM's DHCP Name Server at the Mac Mini's fixed IP**
   (consider adding `1.1.1.1` as a second entry as a fallback this time).
6. **Update `SERVER` in `poll_aria.sh`** (on Ty's everyday Mac, not the
   Mini) to the Mac Mini's IP.
7. **Confirm the scale itself picks back up** — no re-pairing should be
   needed since only the DNS target moved, not the Wi-Fi network. Watch
   `tail -f /var/log/aria_interceptor.log` on the Mac Mini and step on
   the scale.

## Not yet confirmed / verify these

- Whether the `Aria Weight to Health` Shortcut has actually been created
  in the Shortcuts app on Ty's everyday Mac (instructions given in
  README section 4, completion unconfirmed).
- Whether `poll_aria.sh` has been installed as a LaunchAgent
  (`com.aria.bridge.poller.plist`) and is actually running on a 5-minute
  schedule (README section 5, completion unconfirmed).
- An actual end-to-end real weigh-in landing in Apple Health has not yet
  been confirmed — everything up to `/latest/me` returning correct data
  has been verified; the Shortcut/Health leg has not been.

## Possible future work (not started, only discussed)

- Data durability: currently flat files (`readings.jsonl`/`.csv`) on a
  single disk, no backups. A periodic cron/launchd job copying `data/`
  elsewhere was discussed but not built.
- Native per-person recognition via the `aria_user` response struct
  (scale would do its own weight-matching instead of our server-side
  heuristic, possibly show names on its display) — explicitly deferred
  as not worth the added protocol-reverse-engineering risk right now.
- Pinning down the real CRC algorithm this firmware uses (cosmetic only,
  current mismatch is harmless).
