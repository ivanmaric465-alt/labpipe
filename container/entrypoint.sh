#!/bin/bash
# labpipe container entrypoint: firewall + host-filtering proxy as root, then drop to the host user.
# Fails closed: any error stops the container.
set -euo pipefail

die() { echo "labpipe-entrypoint: ERROR: $*" >&2; exit 1; }
trap 'die "command failed at line $LINENO (firewall/proxy not set up; refusing to start)"' ERR

[ "$(id -u)" = 0 ] || die "must start as root (uid 0), got uid $(id -u)"
: "${LABPIPE_UID:?LABPIPE_UID not set}"
: "${LABPIPE_GID:?LABPIPE_GID not set}"
[[ "$LABPIPE_UID" =~ ^[0-9]+$ && "$LABPIPE_GID" =~ ^[0-9]+$ ]] || die "LABPIPE_UID/LABPIPE_GID must be numeric"
[ "$LABPIPE_UID" != 0 ] || die "LABPIPE_UID must not be 0"
HOSTS="${LABPIPE_ALLOWED_HOSTS:-}"

PROXY_USER=tinyproxy
PROXY_UID=$(id -u "$PROXY_USER")

# --- 1. firewall (IPv4 and IPv6): default DROP, loopback, established, proxy-owned traffic
for ipt in iptables ip6tables; do
  "$ipt" -F OUTPUT
  "$ipt" -A OUTPUT -o lo -j ACCEPT
  "$ipt" -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  "$ipt" -A OUTPUT -m owner --uid-owner "$PROXY_UID" -j ACCEPT
  "$ipt" -P OUTPUT DROP
  "$ipt" -S OUTPUT | grep -q '^-P OUTPUT DROP' || die "$ipt: OUTPUT policy is not DROP"
done

# --- 2. proxy with host allow-list
mkdir -p /run/labpipe /var/log/tinyproxy
chown "$PROXY_USER:$PROXY_USER" /run/labpipe /var/log/tinyproxy
FILTER=/run/labpipe/tinyproxy.filter
: > "$FILTER"
IFS=',' read -ra arr <<< "$HOSTS"
for h in "${arr[@]}"; do
  h="${h//[[:space:]]/}"
  [ -n "$h" ] || continue
  [[ "$h" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "invalid host in LABPIPE_ALLOWED_HOSTS: '$h'"
  esc="${h//./\\.}"
  echo "(^|\\.)${esc}\$" >> "$FILTER"
done
[ -s "$FILTER" ] || echo '^$' >> "$FILTER"   # empty list: matches no host
chmod 644 "$FILTER"

setpriv --reuid="$PROXY_USER" --regid="$PROXY_USER" --init-groups --inh-caps=-all --no-new-privs -- \
  tinyproxy -d -c /etc/labpipe/tinyproxy.conf >> /var/log/tinyproxy/stdout.log 2>&1 &
PROXY_PID=$!
for _ in $(seq 1 50); do
  (exec 3<>/dev/tcp/127.0.0.1/3128) 2>/dev/null && break
  kill -0 "$PROXY_PID" 2>/dev/null || { cat /var/log/tinyproxy/stdout.log >&2 || true; die "proxy exited"; }
  sleep 0.1
done
(exec 3<>/dev/tcp/127.0.0.1/3128) 2>/dev/null || die "proxy not listening on 127.0.0.1:3128"

# --- 3. home volume
mkdir -p /home/labpipe
chown "$LABPIPE_UID:$LABPIPE_GID" /home/labpipe

# --- 4. drop privileges
trap - ERR
if setpriv --bounding-set=-all true 2>/dev/null; then BSET=(--bounding-set=-all); else BSET=(); fi
exec setpriv --reuid="$LABPIPE_UID" --regid="$LABPIPE_GID" --clear-groups --inh-caps=-all "${BSET[@]}" --no-new-privs -- "$@"
