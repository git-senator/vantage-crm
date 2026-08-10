#!/usr/bin/env bash
#
# Let only Cloudflare reach this origin on 80/443.
#
# Cloudflare in front of the site is worth nothing while the machine behind it
# still answers the whole internet: anyone who learns the origin address talks
# to Caddy directly and leaves the WAF, the bot filter and the rate limits at
# the door. Two of our own DNS records used to publish that address outright.
# This script is the other half of the fix — the DNS records are proxied, and
# the origin now refuses anyone who did not come through Cloudflare.
#
# Why DOCKER-USER and not ufw: Caddy publishes its ports through Docker, and
# Docker's published ports are DNAT'd into the FORWARD path, which never meets
# ufw's INPUT rules. `ufw status` can show 80/443 denied while the container
# happily serves the world. DOCKER-USER is the chain Docker documents for
# exactly this, and it is consulted before Docker's own rules.
#
# Scope is deliberately narrow: only tcp/80 and tcp/443 are ever dropped. SSH,
# and every other port, is left to ufw. A bug here cannot lock anyone out of
# the machine.
#
# Run by vantage-origin-firewall.service at boot and by its weekly timer.
# To undo: `iptables -F DOCKER-USER && ip6tables -F DOCKER-USER`.

set -euo pipefail

PORTS="80,443"
CACHE_DIR="/etc/vantage"
V4_CACHE="${CACHE_DIR}/cloudflare-ips-v4"
V6_CACHE="${CACHE_DIR}/cloudflare-ips-v6"

log() { printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die() { log "ABORT: $*" >&2; exit 1; }

# Fetch a range list, but only trust it if it still looks like one.
#
# The failure that matters is not a network error — it is a fetch that
# "succeeds" and returns a captive-portal page or an empty body. Writing that
# to the allow-list would leave a firewall that drops everyone including
# Cloudflare, i.e. the site goes dark. So: validate, and fall back to the last
# list known to be good rather than to nothing.
fetch_ranges() {
  local url="$1" cache="$2" min="$3" body

  body="$(curl -fsS --max-time 20 "$url" 2>/dev/null || true)"

  if [ -n "$body" ] &&
     [ "$(printf '%s\n' "$body" | grep -cE '^[0-9a-fA-F:.]+/[0-9]+$')" -ge "$min" ] &&
     ! printf '%s\n' "$body" | grep -qvE '^[0-9a-fA-F:.]+/[0-9]+$|^$'; then
    printf '%s\n' "$body" | grep -E '^[0-9a-fA-F:.]+/[0-9]+$' > "$cache"
    log "fetched $(wc -l < "$cache") ranges from $url"
  elif [ -s "$cache" ]; then
    log "WARNING: $url unusable, keeping the cached list ($(wc -l < "$cache") ranges)"
  else
    die "$url unusable and no cached list — refusing to build an empty allow-list"
  fi

  cat "$cache"
}

apply() {
  local ipt="$1" ranges="$2" localnets="$3"

  # Docker creates DOCKER-USER for the address families it manages. Where it
  # has not (an IPv6 stack Docker was not asked to handle), create the chain
  # and hook it into FORWARD ourselves, so the rules below are not inert.
  if ! "$ipt" -S DOCKER-USER >/dev/null 2>&1; then
    "$ipt" -N DOCKER-USER
  fi
  if ! "$ipt" -C FORWARD -j DOCKER-USER >/dev/null 2>&1; then
    "$ipt" -I FORWARD 1 -j DOCKER-USER
  fi

  # Rebuild from scratch so re-running is idempotent. The chain holds nothing
  # but our rules; Docker creates it empty and never writes to it itself.
  "$ipt" -F DOCKER-USER

  # Replies on connections already allowed through.
  "$ipt" -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN

  # Container-to-container and host-to-container traffic. Without this the
  # app cannot reach its own file storage through the public hostname.
  local net
  for net in $localnets; do
    "$ipt" -A DOCKER-USER -s "$net" -j RETURN
  done

  local cidr
  for cidr in $ranges; do
    "$ipt" -A DOCKER-USER -s "$cidr" -p tcp -m multiport --dports "$PORTS" -j RETURN
  done

  # Anyone else asking for the web ports came around Cloudflare. Drop rather
  # than reject: a scanner learns nothing from silence.
  "$ipt" -A DOCKER-USER -p tcp -m multiport --dports "$PORTS" -j DROP

  log "$ipt: $(printf '%s\n' "$ranges" | wc -w) Cloudflare ranges allowed, everything else dropped on $PORTS"
}

mkdir -p "$CACHE_DIR"

V4="$(fetch_ranges https://www.cloudflare.com/ips-v4 "$V4_CACHE" 10)"
V6="$(fetch_ranges https://www.cloudflare.com/ips-v6 "$V6_CACHE" 4)"

apply iptables  "$V4" "127.0.0.0/8 172.16.0.0/12 10.0.0.0/8"
apply ip6tables "$V6" "::1/128 fc00::/7"

log "origin is now reachable on ${PORTS} only through Cloudflare"
