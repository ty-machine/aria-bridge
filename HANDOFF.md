# Aria Bridge — project handoff

Paste this whole document as your first message in a new Claude Code
session (or save it as `CLAUDE.md` in the project folder so it loads
automatically). It covers everything learned and built so far, current
status, and exactly what's left.

## Status update, November 24, 2026 (supersedes the migration plan below)

The Mac mini migration was never finished, and the plan changed: the interceptor is moving into the
homelab k3s cluster, as a container, instead of onto a dedicated host.

- **Where it runs.** The `Dockerfile` and `.github/workflows/image.yml` here build
  `ghcr.io/ty-machine/aria-bridge` (private package). The deployment, address (`10.0.0.4`), storage,
  DNS records and the whole cutover procedure live in the `ty-machine/homelab` repository:
  `docs/runbooks/aria-bridge.md`. That runbook is the operating procedure. This repo is the code and
  the protocol notes.
- **DNS correction.** "The UDM's Local DNS Record feature does not override this domain" was wrong
  for `www.fitbit.com`: a static A record answers it (checked with `dig @10.0.0.1`). The behavior we
  saw was on `1.scale.www.fitbit.com`, which is a CNAME upstream, so the UDM returns the real chain
  unless that name has its own A record. No dnsmasq box is needed. Do not point household DNS at a
  host that is not the UDM.
- **Retired.** `ubuntu/` and `mac/` (systemd unit, LaunchDaemon, dnsmasq snippet, redeploy scripts)
  are kept for reference only. The container replaces them.
- **Data.** The old box's data (56 readings, newest 2026-08-25) was backed up on 2026-11-24 and is
  restored into the cluster volume at cutover. The interceptor code is unchanged by the move.
- **Not diagnosed.** Why uploads stopped on 2026-08-25.

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
- **CRC checksum bug, found and fixed 2026-08-16.** The original
  implementation used init=0xFFFF, which is actually CRC-16/CCITT-FALSE
  (a different, commonly-confused variant that shares XMODEM's
  polynomial 0x1021) — not true CRC-16/XMODEM, which uses init=0x0000.
  This was *not* just cosmetic: `build_upload_response()` uses the same
  function to CRC our replies to the scale, and the scale likely
  validates that CRC before considering an upload synced. This lines up
  exactly with the symptom Ty reported — "no sync" shown on every single
  upload, always resending the *entire* cache, never clearing it — the
  scale probably never accepted our (wrong-CRC) responses as valid.
  Confirmed against a real captured upload
  (`data/crc_samples/1786908095_560bytes.bin`, pulled from the ubuntu
  box's `raw_uploads/`, gitignored — not committed): brute-forced ~30
  standard CRC-16 variants, and init=0x0000 (true XMODEM) is the only
  one that reproduces the scale's CRC exactly; the old init=0xFFFF does
  not. Fixed in `aria_interceptor.py`. **Not yet redeployed or verified
  against the real scale** — needs `ubuntu/redeploy.sh` run, then a real
  weigh-in to confirm "no sync" actually clears.
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
- **There is no native Health app for Mac.** An earlier session assumed
  otherwise ("macOS Sonoma (14)+ has Health on Mac") and the whole
  original design was built on that — wrong, don't repeat it. Verified
  directly on Ty's Mac (2026-08-14, macOS 26.4.1, Apple Silicon):
  `system_profiler SPApplicationsDataType` shows no Health.app or
  Fitness.app anywhere, and Shortcuts has no "Log Health Sample" action
  available. `HealthKit.framework` and the `healthd` daemon *are*
  present and running, but nothing exposes the logging action to
  Shortcuts without the (nonexistent) app. A Spotlight search for
  "Health" surfaces an iPhone Mirroring shim, not a real Mac app — easy
  to mistake for the real thing at a glance. Fix: do the Health-logging
  step on the iPhone instead, via a Shortcuts **Personal Automation**
  (real "Log Health Sample" support there) — see README.md step 4. This
  replaced the whole Mac poller/Shortcut design (`poll_aria.sh`,
  `com.aria.bridge.poller.plist`, both deleted from the repo).
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
- No file for Health-logging — it's a Shortcuts Personal Automation
  built directly on Ty's iPhone (README.md step 4), since there's no
  native Health app for Mac (see "Key findings"). The old
  `poll_aria.sh` + `com.aria.bridge.poller.plist` Mac-poller pair that
  used to do this has been deleted from the repo.
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
6. **Update `ARIA_SERVER_URL` in `mise.local.toml`** to the Mac Mini's
   IP, and update the URL typed into the iPhone Shortcuts automation's
   "Get Contents of URL" action to match (that field is manual, mise
   doesn't reach it).
7. **Confirm the scale itself picks back up** — no re-pairing should be
   needed since only the DNS target moved, not the Wi-Fi network. Watch
   `tail -f /var/log/aria_interceptor.log` on the Mac Mini and step on
   the scale.

## Not yet confirmed / verify these

- Whether the iPhone Shortcuts Personal Automation (README.md step 4:
  Get Contents of URL -> date-match check -> Log Health Sample) has
  actually been built on Ty's iPhone. Not started as of 2026-08-14 —
  this session ended right as the Mac Health app dead-end was found and
  the iPhone approach was designed; the automation itself hasn't been
  built yet.
- Whether "Ask Before Running" has been turned off for that automation,
  without which it'll sit waiting for a confirmation tap instead of
  running silently on its daily schedule.
- An actual end-to-end real weigh-in landing in Apple Health has not yet
  been confirmed. What *has* been verified (2026-08-14, read-only test
  against the live `.240` interceptor): `poll_aria.sh` — before it was
  retired — successfully fetched a real reading from `/latest/me` and
  attempted to log it, failing only because the Shortcut didn't exist.
  So the interceptor and its data are known-good; only the Health-logging
  leg (now iPhone-side) remains unverified.

## Possible future work (not started, only discussed)

- Data durability: currently flat files (`readings.jsonl`/`.csv`) on a
  single disk, no backups. A periodic cron/launchd job copying `data/`
  elsewhere was discussed but not built.
- Native per-person recognition via the `aria_user` response struct
  (scale would do its own weight-matching instead of our server-side
  heuristic, possibly show names on its display) — explicitly deferred
  as not worth the added protocol-reverse-engineering risk right now.
  Real struct layout (81 bytes packed, from `micolous/helvetic`'s
  protocol.md) is in the 2026-08-16 conversation if this gets picked
  back up; worth noting the scale's cache already carries at least one
  non-guest `user_id` (`42592252`) from before Fitbit's backend died,
  which might be reusable instead of inventing new IDs.
- ~~Pinning down the real CRC algorithm this firmware uses~~ — done,
  see "Key findings."
