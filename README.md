# PingRate

Windows network monitor + diagnostics: PingPlotter-style route view with per-hop loss/latency, multi-target tabs,
and a one-click (or automatic) diagnostics mode that finds out *why* your connection misbehaves.

## Features
* **Live monitoring** – add any domain/IP; each gets its own tab with a real-time latency graph, loss, jitter, min/avg/max.
* **Route view** – continuous per-hop probing. The banner says in plain English where loss starts
  ("Packet loss starts at hop 6 (…)") and rows are marked *LOSS STARTS HERE / Affected*. Routers that merely
  rate-limit ping while later hops are fine are flagged *harmless* so you aren't misled.
* **Diagnostics mode** – triggered by hand (🩺 button) or automatically after N seconds without a reply:
  router check, internet ping, DNS, TCP, **two-way UDP burst + NAT type + per-source-port test** (the
  "I can see/hear others but they can't hear me" case), website load timing (DNS/connect/TLS/first byte), captive-portal
  check, MTU, speed test with **bufferbloat**, video-streaming stutter simulation, and an optional **echo server test**
  that separates upload loss from download loss. Ends with a written verdict you can copy/save.
* **History** – every probe (destination and each hop) is stored locally as per-minute data for 8 days. The
  📈 History window shows a week of loss and latency per target, a list of outages, per-hop loss over the period, and
  every past diagnostics run with its full report.
* **Test trends** – every diagnostics run stores its numbers (speed up/down, bufferbloat, UDP loss, router/internet ping,
  DNS, website load, video start/stalls, …) **forever**, plotted over time (24 h … all time) with a pass/warn/fail
  timeline per test. Only the long text reports expire after 30 days. Set *Settings → Run tests every N minutes* to build
  this history automatically in the background (speed/video are skipped unless you opt in, to save data).
* **Pattern detection** – flags repeating drop cycles (>3 drops of 3+ failed pings per hour, regular spacing, same
  time of day on several days) and upload problems (upload near the link's capacity, or far above download), using the
  router's own counters via UPnP (no setup) or SNMP (Settings). Also scans your LAN for Plex servers older than
  1.43.3.10861 (Plex2Shell), lists ports your router forwards, and checks this PC against known attacker IPs/domains
  (extend via `%APPDATA%\PingRate\iocs.json`). Results come with a calm, ranked list of *possible causes* – ordinary
  ones first (big uploads, router restart, Wi‑Fi, ISP line, bufferbloat…); a compromised device is listed as a
  "less common, cheap to rule out" possibility unless there is hard evidence. Run `tools/plex_server_check.sh` on the
  Plex server for host-side checks (read-only).
* **Full output** – the 📄 button saves one text file with everything (system/adapter info, settings, live route +
  raw probes, the 7-day history, diagnostics results, JSON summary) prefixed with instructions, ready to paste to an AI
  or send to support. It can run fresh diagnostics first.
* Settings are stored in `%APPDATA%\PingRate\settings.json`; history in `%APPDATA%\PingRate\history.db`.

## Optional: echo server (per-direction loss)
Run `PingRateEchoServer.exe` (or `python tools/echo_server.py`) on a machine outside your network
(VPS / friend's PC): `PingRateEchoServer.exe 40404`, open **UDP 40404**, then enter `host:40404` in Settings → Echo server.

## Build the exe
`build.bat` on Windows (or let the GitHub Action build it – download the `PingRate-windows` artifact).
Run from source: `pip install -r requirements.txt && python run.py`. `python run.py --demo` shows a simulated network.
