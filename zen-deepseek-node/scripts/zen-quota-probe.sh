#!/usr/bin/env bash
# Pre-dispatch quota check for the opencode Zen DeepSeek free tier.
# Exit 0  = quota available, node is qualified for this dispatch.
# Exit 1  = quota exhausted or endpoint unreachable, node should be
#           treated as unqualified for this dispatch (not a hard error).
#
# Called by the conductor immediately before scheduling work onto
# zen-deepseek-1, same spirit as am4-fleet-node's hermes_health check.

set -u

: "${ZEN_DEEPSEEK_API_KEY:?ZEN_DEEPSEEK_API_KEY not set}"
BASE_URL="https://opencode.ai/zen/v1"

http_status=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer ${ZEN_DEEPSEEK_API_KEY}" \
  "${BASE_URL}/models" \
  --max-time 5)

case "$http_status" in
  200)
    echo "qualified"
    exit 0
    ;;
  429|402)
    echo "unqualified: quota_exhausted (http ${http_status})"
    exit 1
    ;;
  *)
    echo "unqualified: probe_failed (http ${http_status})"
    exit 1
    ;;
esac
