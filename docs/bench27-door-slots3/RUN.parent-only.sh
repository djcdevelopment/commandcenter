#!/usr/bin/env bash
set -euo pipefail
# Parent only, AFTER documented deployment, sole-owner and guard preflight. No engine restart/fence.
systemd-run --user --unit=bench27-door-slots3 --collect --property=RuntimeMaxSec=1800 --property=TimeoutStopSec=30 --property=KillMode=control-group --setenv=HEARTH_SOURCE=/home/derek/work/commandcenter-linux-flash --setenv=HEARTH_BACKENDS=/home/derek/hearth-production/backends-linux.toml /home/derek/.venvs/hearth-private/bin/python /home/derek/work/lab-rnd/research/bench27_campaign.py run --sequence /home/derek/work/worktrees/bench27-am4-sizing/docs/bench27-door-slots3/slots3.sequence.json --out /home/derek/work/lab-rnd/research/evidence/bench27-slots3-20261005/paired-c3/sequence
