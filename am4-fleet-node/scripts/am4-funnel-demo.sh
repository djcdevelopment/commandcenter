#!/usr/bin/env bash
# am4-funnel-demo — publish a short-lived static demo on AM4's Tailscale Funnel.
#
# AM4 already serves several long-lived public routes. This script adds and
# removes *demo* routes additively, and gives every one of them a mandatory
# expiry so a feedback loop cannot quietly become permanent public hosting.
#
#   am4-funnel-demo publish <slug> <src> [--ttl 48h]
#   am4-funnel-demo list
#   am4-funnel-demo unpublish <slug>
#   am4-funnel-demo extend <slug> <ttl>
#
# <src> is a file or directory ON AM4. A file is published as index.html.
#
# Design notes:
#   - Each demo gets its own directory, its own nginx container, and its own
#     loopback port, so teardown of one can never disturb another.
#   - `tailscale funnel reset` is NEVER invoked. It is not path-scoped and would
#     destroy every public route on this host at once. Routes are removed one at
#     a time with `--set-path=<p> off`.
#   - Expiry is a transient systemd timer, so it survives this shell and is
#     visible in `systemctl list-timers`.

set -euo pipefail

DEMO_ROOT=/opt/am4-demos
STATE_DIR=/var/lib/am4-funnel-demo
PORT_MIN=9020
PORT_MAX=9059
IMAGE=nginx:alpine
UNIT_PREFIX=am4-demo-expire
DEFAULT_TTL=48h
SELF=/usr/local/bin/am4-funnel-demo

# Public routes that belong to other projects. Refusing these by name is the
# backstop that keeps a demo from shadowing the gallery, the IRC portal, or the
# member guide.
RESERVED_PATHS=("/" "/join" "/guide" "/lab")

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
info() { printf '  %s\n' "$*"; }

need_root() {
  [[ ${EUID} -eq 0 ]] || die "must run as root (use sudo)"
}

require_cmds() {
  local c
  for c in tailscale docker systemd-run systemctl curl; do
    command -v "$c" >/dev/null 2>&1 || die "missing required command: $c"
  done
}

validate_slug() {
  local slug=$1
  [[ $slug =~ ^[a-z0-9][a-z0-9-]{1,30}$ ]] \
    || die "slug must be lowercase alphanumeric/dashes, 2-31 chars: '$slug'"
  local r
  for r in "${RESERVED_PATHS[@]}"; do
    [[ "/$slug" == "$r" ]] && die "'/$slug' is a reserved public route"
  done
  return 0
}

# Reject a slug whose funnel path already exists but was not created by us.
# Prevents clobbering a route this script does not own.
# NOTE on pipelines in this script: `set -o pipefail` combined with a consumer
# that exits early (`head -1`, `grep -q`) makes the *producer* die of SIGPIPE and
# the whole pipeline report failure. That silently inverts these checks. So each
# one captures its input into a variable first and matches in-shell.
assert_path_free_or_ours() {
  local slug=$1 status
  status=$(tailscale funnel status 2>/dev/null || true)
  if grep -qE "(^|[[:space:]])/$slug([[:space:]]|\$)" <<<"$status"; then
    [[ -f "$STATE_DIR/$slug.port" ]] \
      || die "/$slug is already published by something this script does not own; refusing"
  fi
}

pick_port() {
  local p out
  for (( p=PORT_MIN; p<=PORT_MAX; p++ )); do
    out=$(ss -ltn "sport = :$p" 2>/dev/null || true)
    if [[ $out != *LISTEN* ]]; then
      printf '%s' "$p"; return 0
    fi
  done
  die "no free loopback port in ${PORT_MIN}-${PORT_MAX}"
}

# Resolve this node's public Funnel hostname. Never fatal: a demo that is
# already published must not fail the run just because we cannot pretty-print
# its URL.
funnel_host() {
  local status host
  status=$(tailscale funnel status 2>/dev/null || true)
  host=$(awk 'match($0, /https:\/\/[a-z0-9.-]+\.ts\.net/) {print substr($0, RSTART+8, RLENGTH-8); exit}' <<<"$status" || true)
  printf '%s' "${host:-<this-node>.ts.net}"
}

nginx_conf() {
  # Keep nginx's stock mime.types; only make .md readable in-browser instead of
  # downloading, since these demos are usually docs plus one HTML page.
  cat <<'CONF'
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;
    autoindex off;
    location ~ \.md$ { default_type text/plain; charset utf-8; }
}
CONF
}

cmd_publish() {
  local slug=${1:-} src=${2:-}; shift 2 || true
  local ttl=$DEFAULT_TTL
  while [[ $# -gt 0 ]]; do
    case $1 in
      --ttl) ttl=${2:?--ttl needs a value}; shift 2 ;;
      *) die "unknown flag: $1" ;;
    esac
  done
  [[ -n $slug && -n $src ]] || die "usage: publish <slug> <src> [--ttl 48h]"
  validate_slug "$slug"
  [[ -e $src ]] || die "source not found on AM4: $src"
  assert_path_free_or_ours "$slug"

  local dir=$DEMO_ROOT/$slug
  local container=am4demo-$slug
  local port; port=$(pick_port)

  printf 'Publishing /%s (ttl %s)\n' "$slug" "$ttl"
  info "funnel map before:"
  tailscale funnel status 2>/dev/null | sed 's/^/    /' || true

  mkdir -p "$dir" "$STATE_DIR"
  rm -rf "${dir:?}/"*
  if [[ -d $src ]]; then
    cp -r "$src"/. "$dir"/
    [[ -f $dir/index.html ]] || die "directory has no index.html: $src"
  else
    cp "$src" "$dir/index.html"
    cp "$src" "$dir/$(basename "$src")"
  fi
  nginx_conf > "$dir/.default.conf"
  chmod -R a+rX "$dir"

  docker rm -f "$container" >/dev/null 2>&1 || true
  docker run -d --name "$container" --restart unless-stopped \
    -p "127.0.0.1:$port:80" \
    -v "$dir:/usr/share/nginx/html:ro" \
    -v "$dir/.default.conf:/etc/nginx/conf.d/default.conf:ro" \
    "$IMAGE" >/dev/null

  # Prove the backend serves before exposing anything publicly. nginx needs a
  # moment after `docker run` returns, so poll rather than checking once — a
  # single immediate check races the container and reports a false failure.
  local code=000 attempt
  for (( attempt=1; attempt<=20; attempt++ )); do
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$port/" 2>/dev/null) || code=000
    [[ $code == 200 ]] && break
    sleep 0.5
  done
  if [[ $code != 200 ]]; then
    docker logs --tail 20 "$container" 2>&1 | sed 's/^/    /' >&2 || true
    docker rm -f "$container" >/dev/null 2>&1 || true
    rm -rf "${dir:?}"
    die "backend returned HTTP $code on 127.0.0.1:$port — nothing was published"
  fi
  info "backend healthy on 127.0.0.1:$port (HTTP 200, ${attempt} attempt(s))"

  printf '%s' "$port" > "$STATE_DIR/$slug.port"
  tailscale funnel --bg --yes --set-path="/$slug" "http://127.0.0.1:$port" >/dev/null

  # Mandatory expiry. Transient timer, visible in systemctl list-timers.
  systemctl stop "$UNIT_PREFIX-$slug.timer" >/dev/null 2>&1 || true
  systemd-run --collect --on-active="$ttl" --unit="$UNIT_PREFIX-$slug" \
    "$SELF" unpublish "$slug" >/dev/null
  date -Is > "$STATE_DIR/$slug.published"
  printf '%s' "$ttl" > "$STATE_DIR/$slug.ttl"

  printf '\nPublished: https://%s/%s/\n' "$(funnel_host)" "$slug"
  info "expires: $(timer_next "$slug") (ttl $ttl)"
  info "funnel map after:"
  tailscale funnel status 2>/dev/null | sed 's/^/    /' || true
}

cmd_unpublish() {
  local slug=${1:-}
  [[ -n $slug ]] || die "usage: unpublish <slug>"
  validate_slug "$slug"

  printf 'Unpublishing /%s\n' "$slug"
  # One path only. Never `funnel reset`.
  tailscale funnel --set-path="/$slug" off >/dev/null 2>&1 || true
  docker rm -f "am4demo-$slug" >/dev/null 2>&1 || true
  rm -rf "${DEMO_ROOT:?}/$slug"
  rm -f "$STATE_DIR/$slug.port" "$STATE_DIR/$slug.published" "$STATE_DIR/$slug.ttl"
  systemctl stop "$UNIT_PREFIX-$slug.timer" >/dev/null 2>&1 || true
  systemctl reset-failed "$UNIT_PREFIX-$slug.service" >/dev/null 2>&1 || true
  info "removed route, container, files, and expiry timer"
  info "funnel map now:"
  tailscale funnel status 2>/dev/null | sed 's/^/    /' || true
}

cmd_extend() {
  local slug=${1:-} ttl=${2:-}
  [[ -n $slug && -n $ttl ]] || die "usage: extend <slug> <ttl>"
  validate_slug "$slug"
  [[ -f $STATE_DIR/$slug.port ]] || die "no demo named '$slug'"
  systemctl stop "$UNIT_PREFIX-$slug.timer" >/dev/null 2>&1 || true
  systemctl reset-failed "$UNIT_PREFIX-$slug.service" >/dev/null 2>&1 || true
  systemd-run --collect --on-active="$ttl" --unit="$UNIT_PREFIX-$slug" \
    "$SELF" unpublish "$slug" >/dev/null
  printf '%s' "$ttl" > "$STATE_DIR/$slug.ttl"
  printf '/%s now expires in %s\n' "$slug" "$ttl"
}

# `--on-active` timers are monotonic, so NextElapseUSecRealtime is empty for
# them. Read the resolved wall-clock time out of list-timers instead.
timer_next() {
  local slug=$1 raw out
  raw=$(systemctl list-timers "$UNIT_PREFIX-$slug.timer" --no-pager --no-legend 2>/dev/null || true)
  out=$(awk 'NR==1 {print $2" "$3" "$4}' <<<"$raw" || true)
  printf '%s' "${out:-unknown}"
}

cmd_list() {
  printf 'Demo routes managed by this script:\n'
  local found=0 f slug port
  if [[ -d $STATE_DIR ]]; then
    for f in "$STATE_DIR"/*.port; do
      [[ -e $f ]] || continue
      found=1
      slug=$(basename "$f" .port); port=$(cat "$f")
      printf '  /%-16s port %s  published %s  expires %s\n' \
        "$slug" "$port" \
        "$(cat "$STATE_DIR/$slug.published" 2>/dev/null || echo '?')" \
        "$(timer_next "$slug")"
    done
  fi
  [[ $found -eq 1 ]] || printf '  (none)\n'
  printf '\nFull funnel map (demo and non-demo):\n'
  tailscale funnel status 2>/dev/null | sed 's/^/  /' || true
}

main() {
  require_cmds
  local cmd=${1:-}; shift || true
  case $cmd in
    publish)   need_root; cmd_publish "$@" ;;
    unpublish) need_root; cmd_unpublish "$@" ;;
    extend)    need_root; cmd_extend "$@" ;;
    list)      cmd_list "$@" ;;
    *) cat >&2 <<USAGE
am4-funnel-demo — short-lived public demos on AM4's Tailscale Funnel

  am4-funnel-demo publish <slug> <src> [--ttl 48h]
  am4-funnel-demo list
  am4-funnel-demo unpublish <slug>
  am4-funnel-demo extend <slug> <ttl>

<src> is a file or directory on AM4. Every demo expires automatically.
Reserved routes (never touched): ${RESERVED_PATHS[*]}
USAGE
      exit 2 ;;
  esac
}

main "$@"
