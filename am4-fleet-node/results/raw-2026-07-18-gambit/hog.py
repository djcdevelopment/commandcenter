#!/usr/bin/env python3
"""hog.py — controlled host-RAM pressure for gambit sweep D.

Allocates and touches N GiB in 256 MiB chunks, holds DUR seconds, exits.
ALWAYS run with OOMScoreAdjust=1000 so the kernel sacrifices THIS process,
never llama-server. Usage: hog.py <GiB> <hold_seconds>
"""
import sys
import time

gib = int(sys.argv[1])
dur = int(sys.argv[2])
CHUNK = 256 * 1024 * 1024
bufs = []
for i in range(gib * 4):
    b = bytearray(CHUNK)
    for o in range(0, CHUNK, 4096):
        b[o] = 1
    bufs.append(b)
    if (i + 1) % 4 == 0:
        print(f"held {(i + 1) // 4} GiB", flush=True)
print(f"holding {gib} GiB for {dur}s", flush=True)
time.sleep(dur)
print("released", flush=True)
