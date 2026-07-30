#!/usr/bin/env bash
# Attributed host-memory sample for AM4. Deployed to /home/derek/baseline/.
# Run by b70-memsample.timer (every 5 min). Read-only; always exits 0.
#
# WHY THIS EXISTS: on 2026-07-30 the box showed ~18 GiB of host RAM that
# belonged to no process, and the evidence was wiped by a reboot before it
# could be attributed. `free` alone cannot tell you WHERE memory went, and RSS
# cannot see it -- kernel/driver allocations and zram's compressed pages are
# invisible to both. This records the breakdown continuously so the next
# occurrence is diagnosable instead of gone.
#
# THE KEY FIELD IS `unattributed_gib`: used minus everything we can name.
# Healthy baseline on this box is ~1.0-1.5 GiB. The 2026-07-30 incident was
# ~18 GiB. Sustained growth = a kernel/driver leak, and the prime suspect is
# host memory pinned by SIGKILL'd SYCL/Level-Zero contexts (82 OOM-killed
# llama-server loads that day). See B70-CARD-MANAGEMENT.md.
set -u

LOG=/home/derek/baseline/b70-memsample.log
MAXLINES=3000

m() { awk -v k="$1" '$1 == k":" {print $2; exit}' /proc/meminfo; }

total=$(m MemTotal); free_=$(m MemFree); buffers=$(m Buffers); cached=$(m Cached)
srecl=$(m SReclaimable); anon=$(m AnonPages); sunrecl=$(m SUnreclaim)
kstack=$(m KernelStack); ptab=$(m PageTables); sptab=$(m SecPageTables)
percpu=$(m Percpu); vmalloc=$(m VmallocUsed); shmem=$(m Shmem)
swaptotal=$(m SwapTotal); swapfree=$(m SwapFree)

# zram consumes real RAM for its compressed pages (mm_stat field 3 = mem_used).
# It is demand-allocated, NOT a fixed reservation of the device's disksize.
zram_used_b=0
for d in /sys/block/zram*; do
  [ -r "$d/mm_stat" ] || continue
  v=$(awk '{print $3}' "$d/mm_stat" 2>/dev/null)
  [ -n "${v:-}" ] && zram_used_b=$((zram_used_b + v))
done
zram_used=$((zram_used_b / 1024))   # kB

# GPU driver book-keeping visible via drm fdinfo (xe). Undercounts SYCL/L0
# allocations, so treat it as a floor, not the truth.
drm_vram=0; drm_sys=0
for p in $(pgrep -x llama-server 2>/dev/null); do
  for f in /proc/"$p"/fdinfo/*; do
    [ -r "$f" ] || continue
    grep -q '^drm-driver' "$f" 2>/dev/null || continue
    v=$(awk '/^drm-resident-vram0:/ {print $2; exit}' "$f" 2>/dev/null); drm_vram=$((drm_vram + ${v:-0}))
    s=$(awk '/^drm-total-system:/  {print $2; exit}' "$f" 2>/dev/null); drm_sys=$((drm_sys + ${s:-0}))
  done
done

used=$((total - free_ - buffers - cached - srecl))
named=$((anon + sunrecl + kstack + ptab + sptab + percpu + zram_used))
unattr=$((used - named))

g() { awk -v k="${1:-0}" 'BEGIN {printf "%.2f", k/1048576}'; }

printf '{"ts":"%s","total_gib":%s,"used_gib":%s,"cached_gib":%s,"available_gib":%s,"anon_gib":%s,"slab_unrecl_gib":%s,"pagetables_gib":%s,"percpu_gib":%s,"vmalloc_gib":%s,"shmem_gib":%s,"zram_used_gib":%s,"drm_resident_vram_gib":%s,"drm_total_system_gib":%s,"swap_used_gib":%s,"unattributed_gib":%s}\n' \
  "$(date -Is)" "$(g "$total")" "$(g "$used")" "$(g "$cached")" "$(g "$(m MemAvailable)")" \
  "$(g "$anon")" "$(g "$sunrecl")" "$(g $((ptab + sptab)))" "$(g "$percpu")" "$(g "$vmalloc")" \
  "$(g "$shmem")" "$(g "$zram_used")" "$(g "$drm_vram")" "$(g "$drm_sys")" \
  "$(g $((swaptotal - swapfree)))" "$(g "$unattr")" >> "$LOG" 2>/dev/null

# Shout into the journal if the unattributed pool goes pathological (>6 GiB).
if [ "$unattr" -gt 6291456 ]; then
  printf 'AM4 MEMORY ANOMALY: %s GiB of host RAM belongs to no process (healthy ~1.2 GiB). used=%s GiB avail=%s GiB zram=%s GiB\n' \
    "$(g "$unattr")" "$(g "$used")" "$(g "$(m MemAvailable)")" "$(g "$zram_used")" \
    | systemd-cat -t b70-alert -p err 2>/dev/null
fi

# Bound the log.
lines=$(wc -l < "$LOG" 2>/dev/null || echo 0)
if [ "${lines:-0}" -gt "$MAXLINES" ]; then
  tail -n "$MAXLINES" "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null
fi

exit 0
