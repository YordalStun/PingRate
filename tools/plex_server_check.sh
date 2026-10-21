#!/bin/sh
# PingRate – read-only check for Plex2Shell (CVE-2026-96652/54/55/56) on a Plex server (Linux / Docker host).
# It changes nothing. Run it on the machine (or inside the container) that runs Plex:
#     sh plex_server_check.sh [plex-container-name]
# A finding means "worth a closer look", not proof of compromise – always confirm before acting.

CONTAINER="${1:-plex}"
BAD_SHA="a654ffc9ea1d3af1cb07feeefe8ee078a0faaaf60b31aa20cfbe082d6c22696c 91e8bb8e9a2e76967a456dec1467fe55b9cde78ece6c293e5ded94ea5a80bf77"
BAD_IPS="94.154.43.88 91.92.40.202 45.194.92.222"
found=0
note() { printf '%s\n' "$*"; }
flag() { found=$((found+1)); printf '  [!] %s\n' "$*"; }

run() { # run inside the container if docker is available and the container exists, else on the host
  if command -v docker >/dev/null 2>&1 && docker inspect "$CONTAINER" >/dev/null 2>&1; then docker exec "$CONTAINER" sh -c "$1" 2>/dev/null; else sh -c "$1" 2>/dev/null; fi
}

note "== PingRate Plex2Shell check =="
note "-- Plex version (fixed in 1.43.3.10861)"
v=$(run "grep -o 'version=\"[0-9.]*' /usr/lib/plexmediaserver/Resources/*.xml 2>/dev/null | head -1")
[ -z "$v" ] && v=$(run "'/usr/lib/plexmediaserver/Plex Media Server' --version 2>/dev/null | head -1")
[ -z "$v" ] && v=$(curl -s --max-time 3 http://127.0.0.1:32400/identity | grep -o 'version="[^"]*' | head -1)
note "   ${v:-unknown (check Settings > General in Plex)}"

note "-- Suspicious files (on this host and in the container)"
for f in /var/tmp/kworker /var/tmp/kworker.sh /tmp/.px_7c1e /tmp/.pxb/curl; do
  if [ -e "$f" ] || run "[ -e $f ]"; then flag "found $f"; fi
done

note "-- Fake kernel-worker processes running from disk (real kworker threads have no exe path)"
for pid in $(pgrep -f kworker 2>/dev/null); do
  exe=$(readlink "/proc/$pid/exe" 2>/dev/null)
  case "$exe" in ""|*"(deleted)"*) [ -n "$exe" ] && flag "pid $pid exe=$exe";; *) flag "pid $pid runs from $exe";; esac
done
run "ps -eo pid,args | grep -i '[k]worker' | grep -v '\\[kworker' | grep '/'" | while read -r l; do flag "in container: $l"; done

note "-- Known malicious file hashes in /var/tmp and /tmp"
for f in $(find /var/tmp /tmp -maxdepth 3 -type f 2>/dev/null | head -500); do
  h=$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)
  for b in $BAD_SHA; do [ "$h" = "$b" ] && flag "$f matches known malware hash"; done
done

note "-- Unexpected .so files in Plex's Codecs directory (exploit drops a shared library there)"
run "find / -path '*plexmediaserver*' -o -path '*Plex Media Server/Codecs*' 2>/dev/null | grep -i codecs | grep '\\.so$' | grep -vE 'libav|libsw|libpostproc|libx26|libvpx|libopus|libmp3|libva|libfdk' | head -20" | while read -r l; do flag "check: $l"; done

note "-- Plex logs: exploit signatures"
logs=$(run "find / -name 'Plex Media Server*.log' 2>/dev/null | head -20")
for l in $logs; do
  run "grep -E 'TranscoderH264Options=dump-yuv|path=https?://[0-9]' \"$l\" | head -3" | while read -r x; do flag "$l: $x"; done
done

note "-- Connections to known attacker IPs"
for ip in $BAD_IPS; do
  (ss -tn 2>/dev/null || netstat -tn 2>/dev/null) | grep -q "$ip" && flag "connection involving $ip"
done

note "-- Upload now (a bot floods outgoing traffic; ~100:1 upload:download is a red flag)"
if [ -r /proc/net/dev ]; then
  a=$(awk 'NR>2 && $1 !~ /^lo/ {rx+=$2; tx+=$10} END {print rx, tx}' /proc/net/dev); sleep 5
  b=$(awk 'NR>2 && $1 !~ /^lo/ {rx+=$2; tx+=$10} END {print rx, tx}' /proc/net/dev)
  set -- $a $b
  rx=$(( ($3-$1)/5*8/1000 )); tx=$(( ($4-$2)/5*8/1000 ))
  note "   download ${rx} kbit/s, upload ${tx} kbit/s"
  [ "$tx" -gt 20000 ] && [ "$tx" -gt $((rx*10)) ] && flag "high, lopsided upload while idle – check what is sending it (iftop / nethogs)"
fi

echo
if [ "$found" -eq 0 ]; then note "Nothing suspicious found. Still update Plex to 1.43.3.10861+ if you haven't."
else note "$found item(s) worth a closer look. If confirmed: isolate the machine, update Plex, rotate your Plex token/passwords, and rebuild the container from a clean image."; fi
