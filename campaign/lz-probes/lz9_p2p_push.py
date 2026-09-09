"""LZ9 - push-only peer traffic between the two B70s, driven through raw Level Zero.

Edges (each runs in its own supervised child process; a hang is a finding, not a crash):
  E0  inventory: drivers, devices, queue groups, canAccessPeer / P2P flags both ways
  E1  PUSH copy: A's copy-engine queue executes memcpy(dst=B.buf, src=A.buf)
  E2  PULL copy: B's copy-engine queue executes memcpy(dst=B.buf, src=A.buf)
  E3  PUSH on the compute queue group instead of the copy-only group
  E4  size ladder on whichever of E1/E3 succeeded (4 KiB .. 256 MiB) with bandwidth

A copy is judged by reading the destination back through the DESTINATION card's own
queue into host memory and comparing the pattern - never by trusting the return code.

Usage:
  python lz9_p2p_push.py run            # orchestrate all edges, write receipts
  python lz9_p2p_push.py edge E1 --size 4096 [--queue copy|compute] [--dir push|pull]
"""
from __future__ import annotations

import argparse
import ctypes as C
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ZE_RESULT_NAMES = {
    0x0: "SUCCESS",
    0x1: "NOT_READY",
    0x70000001: "ERROR_DEVICE_LOST",
    0x70000002: "ERROR_OUT_OF_HOST_MEMORY",
    0x70000003: "ERROR_OUT_OF_DEVICE_MEMORY",
    0x70000004: "ERROR_MODULE_BUILD_FAILURE",
    0x70000005: "ERROR_MODULE_LINK_FAILURE",
    0x70000006: "ERROR_DEVICE_REQUIRES_RESET",
    0x70000007: "ERROR_DEVICE_IN_LOW_POWER_STATE",
    0x78000001: "ERROR_INSUFFICIENT_PERMISSIONS",
    0x78000002: "ERROR_NOT_AVAILABLE",
    0x78000003: "ERROR_DEPENDENCY_UNAVAILABLE",
    0x78000004: "ERROR_UNINITIALIZED",
    0x78000005: "ERROR_UNSUPPORTED_VERSION",
    0x78000006: "ERROR_UNSUPPORTED_FEATURE",
    0x78000007: "ERROR_INVALID_ARGUMENT",
    0x78000008: "ERROR_INVALID_NULL_HANDLE",
    0x78000009: "ERROR_HANDLE_OBJECT_IN_USE",
    0x7800000A: "ERROR_INVALID_NULL_POINTER",
    0x7800000B: "ERROR_INVALID_SIZE",
    0x7800000C: "ERROR_UNSUPPORTED_SIZE",
    0x7800000D: "ERROR_UNSUPPORTED_ALIGNMENT",
    0x7800000E: "ERROR_INVALID_SYNCHRONIZATION_OBJECT",
    0x7800000F: "ERROR_INVALID_ENUMERATION",
    0x78000010: "ERROR_UNSUPPORTED_ENUMERATION",
    0x78000011: "ERROR_UNSUPPORTED_IMAGE_FORMAT",
    0x78000012: "ERROR_INVALID_NATIVE_BINARY",
    0x78000013: "ERROR_INVALID_GLOBAL_NAME",
    0x78000014: "ERROR_INVALID_KERNEL_NAME",
    0x78000015: "ERROR_INVALID_FUNCTION_NAME",
    0x78000016: "ERROR_INVALID_GROUP_SIZE_DIMENSION",
    0x78000017: "ERROR_INVALID_GLOBAL_WIDTH_DIMENSION",
    0x78000018: "ERROR_INVALID_KERNEL_ARGUMENT_INDEX",
    0x78000019: "ERROR_INVALID_KERNEL_ARGUMENT_SIZE",
    0x7800001A: "ERROR_INVALID_KERNEL_ATTRIBUTE_VALUE",
    0x7800001B: "ERROR_INVALID_MODULE_UNLINKED",
    0x7800001C: "ERROR_INVALID_COMMAND_LIST_TYPE",
    0x7800001D: "ERROR_OVERLAPPING_REGIONS",
    0x7800001E: "WARNING_ACTION_REQUIRED",
    0x7FFFFFFF: "ERROR_UNKNOWN",
}

ST_DEVICE_PROPERTIES = 0x3
ST_COMMAND_QUEUE_GROUP_PROPERTIES = 0x6
ST_DEVICE_P2P_PROPERTIES = 0xB
ST_CONTEXT_DESC = 0xD
ST_COMMAND_QUEUE_DESC = 0xE
ST_COMMAND_LIST_DESC = 0xF
ST_DEVICE_MEM_ALLOC_DESC = 0x15
ST_HOST_MEM_ALLOC_DESC = 0x16
QG_COMPUTE = 1
QG_COPY = 2


class DeviceProperties(C.Structure):
    _fields_ = [
        ("stype", C.c_int), ("pNext", C.c_void_p), ("type", C.c_int),
        ("vendorId", C.c_uint32), ("deviceId", C.c_uint32), ("flags", C.c_uint32),
        ("subdeviceId", C.c_uint32), ("coreClockRate", C.c_uint32),
        ("maxMemAllocSize", C.c_uint64), ("maxHardwareContexts", C.c_uint32),
        ("maxCommandQueuePriority", C.c_uint32), ("numThreadsPerEU", C.c_uint32),
        ("physicalEUSimdWidth", C.c_uint32), ("numEUsPerSubslice", C.c_uint32),
        ("numSubslicesPerSlice", C.c_uint32), ("numSlices", C.c_uint32),
        ("timerResolution", C.c_uint64), ("timestampValidBits", C.c_uint32),
        ("kernelTimestampValidBits", C.c_uint32), ("uuid", C.c_uint8 * 16),
        ("name", C.c_char * 256),
    ]


class QueueGroupProperties(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("flags", C.c_uint32),
                ("maxMemoryFillPatternSize", C.c_size_t), ("numQueues", C.c_uint32)]


class P2PProperties(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("flags", C.c_uint32)]


class ContextDesc(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("flags", C.c_uint32)]


class CommandQueueDesc(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("ordinal", C.c_uint32),
                ("index", C.c_uint32), ("flags", C.c_uint32), ("mode", C.c_int), ("priority", C.c_int)]


class CommandListDesc(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("commandQueueGroupOrdinal", C.c_uint32),
                ("flags", C.c_uint32)]


class DeviceMemAllocDesc(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("flags", C.c_uint32), ("ordinal", C.c_uint32)]


class HostMemAllocDesc(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("flags", C.c_uint32)]


def rc_name(rc: int) -> str:
    return ZE_RESULT_NAMES.get(rc & 0xFFFFFFFF, f"0x{rc & 0xFFFFFFFF:08x}")


class ZE:
    def __init__(self) -> None:
        if os.environ.get("ZE_AFFINITY_MASK"):
            raise SystemExit("refusing to run with ZE_AFFINITY_MASK set (known deadlock landmine)")
        self.lib = C.CDLL("ze_loader.dll")
        L = self.lib
        for name, res, args in [
            ("zeInit", C.c_int, [C.c_uint32]),
            ("zeDriverGet", C.c_int, [C.POINTER(C.c_uint32), C.POINTER(C.c_void_p)]),
            ("zeDeviceGet", C.c_int, [C.c_void_p, C.POINTER(C.c_uint32), C.POINTER(C.c_void_p)]),
            ("zeDeviceGetProperties", C.c_int, [C.c_void_p, C.POINTER(DeviceProperties)]),
            ("zeDeviceGetCommandQueueGroupProperties", C.c_int, [C.c_void_p, C.POINTER(C.c_uint32), C.POINTER(QueueGroupProperties)]),
            ("zeDeviceCanAccessPeer", C.c_int, [C.c_void_p, C.c_void_p, C.POINTER(C.c_uint8)]),
            ("zeDeviceGetP2PProperties", C.c_int, [C.c_void_p, C.c_void_p, C.POINTER(P2PProperties)]),
            ("zeContextCreate", C.c_int, [C.c_void_p, C.POINTER(ContextDesc), C.POINTER(C.c_void_p)]),
            ("zeContextDestroy", C.c_int, [C.c_void_p]),
            ("zeCommandQueueCreate", C.c_int, [C.c_void_p, C.c_void_p, C.POINTER(CommandQueueDesc), C.POINTER(C.c_void_p)]),
            ("zeCommandQueueDestroy", C.c_int, [C.c_void_p]),
            ("zeCommandQueueExecuteCommandLists", C.c_int, [C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p), C.c_void_p]),
            ("zeCommandQueueSynchronize", C.c_int, [C.c_void_p, C.c_uint64]),
            ("zeCommandListCreate", C.c_int, [C.c_void_p, C.c_void_p, C.POINTER(CommandListDesc), C.POINTER(C.c_void_p)]),
            ("zeCommandListDestroy", C.c_int, [C.c_void_p]),
            ("zeCommandListClose", C.c_int, [C.c_void_p]),
            ("zeCommandListReset", C.c_int, [C.c_void_p]),
            ("zeCommandListAppendMemoryCopy", C.c_int, [C.c_void_p, C.c_void_p, C.c_void_p, C.c_size_t, C.c_void_p, C.c_uint32, C.c_void_p]),
            ("zeMemAllocDevice", C.c_int, [C.c_void_p, C.POINTER(DeviceMemAllocDesc), C.c_size_t, C.c_size_t, C.c_void_p, C.POINTER(C.c_void_p)]),
            ("zeMemAllocHost", C.c_int, [C.c_void_p, C.POINTER(HostMemAllocDesc), C.c_size_t, C.c_size_t, C.POINTER(C.c_void_p)]),
            ("zeMemFree", C.c_int, [C.c_void_p, C.c_void_p]),
        ]:
            fn = getattr(L, name)
            fn.restype = res
            fn.argtypes = args

    def check(self, rc: int, what: str) -> None:
        if rc != 0:
            raise RuntimeError(f"{what} -> {rc_name(rc)}")

    def devices(self):
        self.check(self.lib.zeInit(0), "zeInit")
        n = C.c_uint32(0)
        self.check(self.lib.zeDriverGet(C.byref(n), None), "zeDriverGet count")
        drivers = (C.c_void_p * n.value)()
        self.check(self.lib.zeDriverGet(C.byref(n), drivers), "zeDriverGet")
        found = []
        for di in range(n.value):
            m = C.c_uint32(0)
            self.check(self.lib.zeDeviceGet(drivers[di], C.byref(m), None), "zeDeviceGet count")
            devs = (C.c_void_p * m.value)()
            self.check(self.lib.zeDeviceGet(drivers[di], C.byref(m), devs), "zeDeviceGet")
            for k in range(m.value):
                p = DeviceProperties()
                p.stype = ST_DEVICE_PROPERTIES
                self.check(self.lib.zeDeviceGetProperties(devs[k], C.byref(p)), "zeDeviceGetProperties")
                found.append({
                    "driver_index": di, "driver": drivers[di], "device": devs[k],
                    "name": p.name.decode(errors="replace"), "deviceId": hex(p.deviceId),
                    "flags": p.flags, "uuid": bytes(p.uuid).hex(),
                })
        return found

    def queue_groups(self, dev):
        n = C.c_uint32(0)
        self.check(self.lib.zeDeviceGetCommandQueueGroupProperties(dev, C.byref(n), None), "qg count")
        arr = (QueueGroupProperties * n.value)()
        for i in range(n.value):
            arr[i].stype = ST_COMMAND_QUEUE_GROUP_PROPERTIES
        self.check(self.lib.zeDeviceGetCommandQueueGroupProperties(dev, C.byref(n), arr), "qg props")
        return [{"ordinal": i, "flags": arr[i].flags, "numQueues": arr[i].numQueues} for i in range(n.value)]

    def peer(self, a, b):
        v = C.c_uint8(0)
        rc = self.lib.zeDeviceCanAccessPeer(a, b, C.byref(v))
        p = P2PProperties()
        p.stype = ST_DEVICE_P2P_PROPERTIES
        rc2 = self.lib.zeDeviceGetP2PProperties(a, b, C.byref(p))
        return {"canAccessPeer": int(v.value), "rc": rc_name(rc), "p2p_flags": hex(p.flags), "p2p_rc": rc_name(rc2)}


def pick_b70s(ze: ZE):
    devs = [d for d in ze.devices() if "B70" in d["name"]]
    if len(devs) != 2:
        raise SystemExit(f"expected exactly two B70s, found {[d['name'] for d in devs]}")
    devs.sort(key=lambda d: d["uuid"])
    return devs


def ordinal_for(groups, want_copy_only: bool) -> int:
    if want_copy_only:
        for g in groups:
            if g["flags"] & QG_COPY and not g["flags"] & QG_COMPUTE:
                return g["ordinal"]
        raise SystemExit("no COPY-only queue group")
    for g in groups:
        if g["flags"] & QG_COMPUTE:
            return g["ordinal"]
    raise SystemExit("no COMPUTE queue group")


class MemoryAccessProperties(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("hostAllocCapabilities", C.c_uint32),
                ("deviceAllocCapabilities", C.c_uint32), ("sharedSingleDeviceAllocCapabilities", C.c_uint32),
                ("sharedCrossDeviceAllocCapabilities", C.c_uint32), ("sharedSystemAllocCapabilities", C.c_uint32)]


class ExternalMemoryProperties(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("memoryAllocationImportTypes", C.c_uint32),
                ("memoryAllocationExportTypes", C.c_uint32), ("imageImportTypes", C.c_uint32),
                ("imageExportTypes", C.c_uint32)]


def edge_inventory() -> dict:
    ze = ZE()
    L = ze.lib
    for name, res, args in [
        ("zeDeviceGetMemoryAccessProperties", C.c_int, [C.c_void_p, C.POINTER(MemoryAccessProperties)]),
        ("zeDeviceGetExternalMemoryProperties", C.c_int, [C.c_void_p, C.POINTER(ExternalMemoryProperties)]),
    ]:
        fn = getattr(L, name); fn.restype = res; fn.argtypes = args
    allv = ze.devices()
    out = {"devices": [{k: v for k, v in d.items() if k not in ("driver", "device")} for d in allv]}
    b70 = pick_b70s(ze)
    a, b = b70[0]["device"], b70[1]["device"]
    rows = []
    for d in b70:
        ma = MemoryAccessProperties(); ma.stype = 0x8
        rc1 = L.zeDeviceGetMemoryAccessProperties(d["device"], C.byref(ma))
        ex = ExternalMemoryProperties(); ex.stype = 0xC
        rc2 = L.zeDeviceGetExternalMemoryProperties(d["device"], C.byref(ex))
        rows.append({
            "uuid": d["uuid"], "queue_groups": ze.queue_groups(d["device"]),
            "memory_access": {"rc": rc_name(rc1), "host": hex(ma.hostAllocCapabilities), "device": hex(ma.deviceAllocCapabilities),
                              "shared_single": hex(ma.sharedSingleDeviceAllocCapabilities),
                              "shared_cross_device": hex(ma.sharedCrossDeviceAllocCapabilities),
                              "shared_system": hex(ma.sharedSystemAllocCapabilities)},
            "external_memory": {"rc": rc_name(rc2), "import": hex(ex.memoryAllocationImportTypes),
                                "export": hex(ex.memoryAllocationExportTypes)},
        })
    out["b70"] = rows
    out["peer"] = {"A->B": ze.peer(a, b), "B->A": ze.peer(b, a)}
    return out


def edge_copy(direction: str, queue_kind: str, size: int, repeats: int) -> dict:
    """direction=push: executor = A (source card). pull: executor = B (destination card).
    The copy is always src=A.buf -> dst=B.buf; only WHO executes it changes."""
    ze = ZE()
    L = ze.lib
    b70 = pick_b70s(ze)
    A, B = b70[0], b70[1]
    executor = A if direction == "push" else B
    ctx = C.c_void_p()
    cdesc = ContextDesc(); cdesc.stype = ST_CONTEXT_DESC
    ze.check(L.zeContextCreate(A["driver"], C.byref(cdesc), C.byref(ctx)), "zeContextCreate")
    result = {"direction": direction, "queue": queue_kind, "size": size, "executor_uuid": executor["uuid"]}
    try:
        def dev_alloc(dev):
            d = DeviceMemAllocDesc(); d.stype = ST_DEVICE_MEM_ALLOC_DESC; d.ordinal = 0
            p = C.c_void_p()
            ze.check(L.zeMemAllocDevice(ctx, C.byref(d), size, 4096, dev, C.byref(p)), "zeMemAllocDevice")
            return p

        def host_alloc():
            h = HostMemAllocDesc(); h.stype = ST_HOST_MEM_ALLOC_DESC
            p = C.c_void_p()
            ze.check(L.zeMemAllocHost(ctx, C.byref(h), size, 4096, C.byref(p)), "zeMemAllocHost")
            return p

        def queue_and_list(dev, kind):
            groups = ze.queue_groups(dev)
            ordinal = ordinal_for(groups, kind == "copy")
            qd = CommandQueueDesc(); qd.stype = ST_COMMAND_QUEUE_DESC; qd.ordinal = ordinal; qd.mode = 0
            q = C.c_void_p()
            ze.check(L.zeCommandQueueCreate(ctx, dev, C.byref(qd), C.byref(q)), "zeCommandQueueCreate")
            ld = CommandListDesc(); ld.stype = ST_COMMAND_LIST_DESC; ld.commandQueueGroupOrdinal = ordinal
            cl = C.c_void_p()
            ze.check(L.zeCommandListCreate(ctx, dev, C.byref(ld), C.byref(cl)), "zeCommandListCreate")
            return q, cl, ordinal

        def run(q, cl, dst, src, what) -> tuple[int, float]:
            rc = L.zeCommandListAppendMemoryCopy(cl, dst, src, size, None, 0, None)
            if rc != 0:
                return rc, 0.0
            ze.check(L.zeCommandListClose(cl), f"close {what}")
            arr = (C.c_void_p * 1)(cl)
            t0 = time.perf_counter()
            rc = L.zeCommandQueueExecuteCommandLists(q, 1, arr, None)
            if rc != 0:
                return rc, 0.0
            rc = L.zeCommandQueueSynchronize(q, C.c_uint64(0xFFFFFFFFFFFFFFFF))
            dt = time.perf_counter() - t0
            ze.check(L.zeCommandListReset(cl), f"reset {what}")
            return rc, dt

        a_buf = dev_alloc(A["device"]); b_buf = dev_alloc(B["device"])
        h_src = host_alloc(); h_dst = host_alloc()
        pattern = bytes([0xA5, 0x5A, 0xC3, 0x3C]) * (size // 4)
        C.memmove(h_src, pattern, size)
        C.memset(h_dst, 0x00, size)

        # stage 1: seed A.buf with the pattern (A's own queue), seed B.buf with zeros (B's own queue)
        qa, cla, _ = queue_and_list(A["device"], "copy")
        qb, clb, _ = queue_and_list(B["device"], "copy")
        rc, _ = run(qa, cla, a_buf, h_src, "seed A"); ze.check(rc, "seed A")
        zeros = host_alloc(); C.memset(zeros, 0, size)
        rc, _ = run(qb, clb, b_buf, zeros, "seed B"); ze.check(rc, "seed B")
        result["seed"] = "ok"

        # stage 2: THE EDGE - one card's queue moves A.buf -> B.buf directly
        qx, clx, ordinal = queue_and_list(executor["device"], queue_kind)
        result["executor_queue_ordinal"] = ordinal
        rcs = []; times = []
        for i in range(repeats):
            rc, dt = run(qx, clx, b_buf, a_buf, "peer copy")
            rcs.append(rc_name(rc)); times.append(dt)
            if rc != 0:
                break
        result["peer_copy_rc"] = rcs
        result["peer_copy_seconds"] = times
        if all(r == "SUCCESS" for r in rcs) and times:
            best = min(times)
            result["peer_copy_gbps_best"] = round(size / best / 1e9, 3) if best > 0 else None

        # stage 3: read B.buf back through B's OWN queue and judge the bytes
        rc, _ = run(qb, clb, h_dst, b_buf, "readback B"); ze.check(rc, "readback B")
        got = C.string_at(h_dst, size)
        if got == pattern:
            verdict = "PATTERN_ARRIVED"
        elif got == b"\x00" * size:
            verdict = "DEST_UNCHANGED"
        elif got == b"\xff" * size:
            verdict = "ALL_FF_UNSUPPORTED_REQUEST"
        else:
            ok = sum(1 for i in range(0, size, 4096) if got[i:i+4096] == pattern[i:i+4096])
            verdict = f"PARTIAL_{ok}_of_{size // 4096}_pages"
        result["verdict"] = verdict
        result["first_16_bytes"] = got[:16].hex()
        for p in (a_buf, b_buf, h_src, h_dst, zeros):
            L.zeMemFree(ctx, p)
    finally:
        L.zeContextDestroy(ctx)
    return result


class IpcMemHandle(C.Structure):
    _fields_ = [("data", C.c_char * 64)]


def edge_ipc(size: int) -> dict:
    """E5: export B.buf as an IPC handle and open it on A's device inside the same context,
    then have A's copy queue write A.buf into the opened pointer. Judges bytes via B's queue."""
    ze = ZE()
    L = ze.lib
    for name, res, args in [
        ("zeMemGetIpcHandle", C.c_int, [C.c_void_p, C.c_void_p, C.POINTER(IpcMemHandle)]),
        ("zeMemOpenIpcHandle", C.c_int, [C.c_void_p, C.c_void_p, IpcMemHandle, C.c_uint32, C.POINTER(C.c_void_p)]),
        ("zeMemCloseIpcHandle", C.c_int, [C.c_void_p, C.c_void_p]),
    ]:
        fn = getattr(L, name); fn.restype = res; fn.argtypes = args
    b70 = pick_b70s(ze)
    A, B = b70[0], b70[1]
    ctx = C.c_void_p()
    cdesc = ContextDesc(); cdesc.stype = ST_CONTEXT_DESC
    ze.check(L.zeContextCreate(A["driver"], C.byref(cdesc), C.byref(ctx)), "zeContextCreate")
    result = {"edge": "E5_ipc_open_on_A", "size": size}
    try:
        def dev_alloc(dev):
            d = DeviceMemAllocDesc(); d.stype = ST_DEVICE_MEM_ALLOC_DESC; d.ordinal = 0
            p = C.c_void_p()
            ze.check(L.zeMemAllocDevice(ctx, C.byref(d), size, 4096, dev, C.byref(p)), "zeMemAllocDevice")
            return p

        def host_alloc():
            h = HostMemAllocDesc(); h.stype = ST_HOST_MEM_ALLOC_DESC
            p = C.c_void_p()
            ze.check(L.zeMemAllocHost(ctx, C.byref(h), size, 4096, C.byref(p)), "zeMemAllocHost")
            return p

        def queue_and_list(dev):
            ordinal = ordinal_for(ze.queue_groups(dev), True)
            qd = CommandQueueDesc(); qd.stype = ST_COMMAND_QUEUE_DESC; qd.ordinal = ordinal
            q = C.c_void_p(); ze.check(L.zeCommandQueueCreate(ctx, dev, C.byref(qd), C.byref(q)), "queue")
            ld = CommandListDesc(); ld.stype = ST_COMMAND_LIST_DESC; ld.commandQueueGroupOrdinal = ordinal
            cl = C.c_void_p(); ze.check(L.zeCommandListCreate(ctx, dev, C.byref(ld), C.byref(cl)), "list")
            return q, cl

        def run(q, cl, dst, src):
            rc = L.zeCommandListAppendMemoryCopy(cl, dst, src, size, None, 0, None)
            if rc != 0:
                return rc
            ze.check(L.zeCommandListClose(cl), "close")
            arr = (C.c_void_p * 1)(cl)
            rc = L.zeCommandQueueExecuteCommandLists(q, 1, arr, None)
            if rc != 0:
                return rc
            rc = L.zeCommandQueueSynchronize(q, C.c_uint64(0xFFFFFFFFFFFFFFFF))
            ze.check(L.zeCommandListReset(cl), "reset")
            return rc

        a_buf = dev_alloc(A["device"]); b_buf = dev_alloc(B["device"])
        h_src = host_alloc(); h_dst = host_alloc(); zeros = host_alloc()
        pattern = bytes([0xA5, 0x5A, 0xC3, 0x3C]) * (size // 4)
        C.memmove(h_src, pattern, size); C.memset(h_dst, 0, size); C.memset(zeros, 0, size)
        qa, cla = queue_and_list(A["device"]); qb, clb = queue_and_list(B["device"])
        ze.check(run(qa, cla, a_buf, h_src), "seed A"); ze.check(run(qb, clb, b_buf, zeros), "seed B")

        handle = IpcMemHandle()
        rc = L.zeMemGetIpcHandle(ctx, b_buf, C.byref(handle))
        result["get_ipc_handle_rc"] = rc_name(rc)
        opened = C.c_void_p()
        if rc == 0:
            rc = L.zeMemOpenIpcHandle(ctx, A["device"], handle, 0, C.byref(opened))
            result["open_ipc_on_A_rc"] = rc_name(rc)
            result["opened_ptr_equals_b_buf"] = bool(opened.value == b_buf.value)
            if rc == 0:
                rc = run(qa, cla, opened, a_buf)
                result["push_via_opened_rc"] = rc_name(rc)
                ze.check(run(qb, clb, h_dst, b_buf), "readback B")
                got = C.string_at(h_dst, size)
                result["verdict"] = ("PATTERN_ARRIVED" if got == pattern else
                                     "DEST_UNCHANGED" if got == b"\x00" * size else
                                     "ALL_FF_UNSUPPORTED_REQUEST" if got == b"\xff" * size else "PARTIAL")
                result["first_16_bytes"] = got[:16].hex()
                L.zeMemCloseIpcHandle(ctx, opened)
        for p in (a_buf, b_buf, h_src, h_dst, zeros):
            L.zeMemFree(ctx, p)
    finally:
        L.zeContextDestroy(ctx)
    return result


class ExternalMemoryExportDesc(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("flags", C.c_uint32)]


class ExternalMemoryExportWin32(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("flags", C.c_uint32), ("handle", C.c_void_p)]


class ExternalMemoryImportWin32(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("flags", C.c_uint32), ("handle", C.c_void_p), ("name", C.c_void_p)]


class MemAllocProps(C.Structure):
    _fields_ = [("stype", C.c_int), ("pNext", C.c_void_p), ("type", C.c_int), ("id", C.c_uint64), ("pageSize", C.c_uint64)]


ST_EXTERNAL_MEMORY_EXPORT_DESC = 0x18
ST_MEMORY_ALLOCATION_PROPERTIES = 0x17
ST_EXTERNAL_MEMORY_IMPORT_WIN32 = 0x22
ST_EXTERNAL_MEMORY_EXPORT_WIN32 = 0x23
EXT_OPAQUE_WIN32 = 0x4


def edge_extmem(size: int, repeats: int = 3) -> dict:
    """E8: the Windows-native cross-adapter route. Allocate B.buf with an OPAQUE_WIN32 export
    descriptor, fetch its NT handle, import that handle as a device allocation on A, and have
    A's copy queue write A.buf into the imported pointer. Judge via B's own readback; time it
    so the placement (peer VRAM vs a system-memory mirror) shows in the bandwidth."""
    ze = ZE()
    L = ze.lib
    for name, res, args in [
        ("zeMemGetAllocProperties", C.c_int, [C.c_void_p, C.c_void_p, C.POINTER(MemAllocProps), C.POINTER(C.c_void_p)]),
    ]:
        fn = getattr(L, name); fn.restype = res; fn.argtypes = args
    b70 = pick_b70s(ze)
    A, B = b70[0], b70[1]
    ctx = C.c_void_p()
    cdesc = ContextDesc(); cdesc.stype = ST_CONTEXT_DESC
    ze.check(L.zeContextCreate(A["driver"], C.byref(cdesc), C.byref(ctx)), "zeContextCreate")
    result = {"edge": "E8_extmem_win32_import_on_A", "size": size}
    try:
        def host_alloc():
            h = HostMemAllocDesc(); h.stype = ST_HOST_MEM_ALLOC_DESC
            p = C.c_void_p()
            ze.check(L.zeMemAllocHost(ctx, C.byref(h), size, 4096, C.byref(p)), "zeMemAllocHost")
            return p

        def queue_and_list(dev):
            ordinal = ordinal_for(ze.queue_groups(dev), True)
            qd = CommandQueueDesc(); qd.stype = ST_COMMAND_QUEUE_DESC; qd.ordinal = ordinal
            q = C.c_void_p(); ze.check(L.zeCommandQueueCreate(ctx, dev, C.byref(qd), C.byref(q)), "queue")
            ld = CommandListDesc(); ld.stype = ST_COMMAND_LIST_DESC; ld.commandQueueGroupOrdinal = ordinal
            cl = C.c_void_p(); ze.check(L.zeCommandListCreate(ctx, dev, C.byref(ld), C.byref(cl)), "list")
            return q, cl

        def run(q, cl, dst, src):
            rc = L.zeCommandListAppendMemoryCopy(cl, dst, src, size, None, 0, None)
            if rc != 0:
                return rc, 0.0
            ze.check(L.zeCommandListClose(cl), "close")
            arr = (C.c_void_p * 1)(cl)
            t0 = time.perf_counter()
            rc = L.zeCommandQueueExecuteCommandLists(q, 1, arr, None)
            if rc == 0:
                rc = L.zeCommandQueueSynchronize(q, C.c_uint64(0xFFFFFFFFFFFFFFFF))
            dt = time.perf_counter() - t0
            ze.check(L.zeCommandListReset(cl), "reset")
            return rc, dt

        # A.buf: plain device allocation on A
        d = DeviceMemAllocDesc(); d.stype = ST_DEVICE_MEM_ALLOC_DESC; d.ordinal = 0
        a_buf = C.c_void_p()
        ze.check(L.zeMemAllocDevice(ctx, C.byref(d), size, 4096, A["device"], C.byref(a_buf)), "alloc A")
        # B.buf: device allocation on B with an export descriptor chained
        exp = ExternalMemoryExportDesc(); exp.stype = ST_EXTERNAL_MEMORY_EXPORT_DESC; exp.flags = EXT_OPAQUE_WIN32
        d2 = DeviceMemAllocDesc(); d2.stype = ST_DEVICE_MEM_ALLOC_DESC; d2.ordinal = 0
        d2.pNext = C.cast(C.pointer(exp), C.c_void_p)
        b_buf = C.c_void_p()
        rc = L.zeMemAllocDevice(ctx, C.byref(d2), size, 4096, B["device"], C.byref(b_buf))
        result["alloc_B_with_export_rc"] = rc_name(rc)
        ze.check(rc, "alloc B (export)")
        # fetch the NT handle
        win = ExternalMemoryExportWin32(); win.stype = ST_EXTERNAL_MEMORY_EXPORT_WIN32; win.flags = EXT_OPAQUE_WIN32
        props = MemAllocProps(); props.stype = ST_MEMORY_ALLOCATION_PROPERTIES
        props.pNext = C.cast(C.pointer(win), C.c_void_p)
        owner = C.c_void_p()
        rc = L.zeMemGetAllocProperties(ctx, b_buf, C.byref(props), C.byref(owner))
        result["get_alloc_props_rc"] = rc_name(rc)
        result["alloc_type"] = props.type
        result["nt_handle"] = hex(win.handle or 0)
        result["owner_is_B"] = bool(owner.value == B["device"])
        h_src = host_alloc(); h_dst = host_alloc(); zeros = host_alloc()
        pattern = bytes([0xA5, 0x5A, 0xC3, 0x3C]) * (size // 4)
        C.memmove(h_src, pattern, size); C.memset(h_dst, 0, size); C.memset(zeros, 0, size)
        qa, cla = queue_and_list(A["device"]); qb, clb = queue_and_list(B["device"])
        ze.check(run(qa, cla, a_buf, h_src)[0], "seed A"); ze.check(run(qb, clb, b_buf, zeros)[0], "seed B")
        if rc == 0 and win.handle:
            imp = ExternalMemoryImportWin32(); imp.stype = ST_EXTERNAL_MEMORY_IMPORT_WIN32
            imp.flags = EXT_OPAQUE_WIN32; imp.handle = win.handle; imp.name = None
            d3 = DeviceMemAllocDesc(); d3.stype = ST_DEVICE_MEM_ALLOC_DESC; d3.ordinal = 0
            d3.pNext = C.cast(C.pointer(imp), C.c_void_p)
            a_view = C.c_void_p()
            rc = L.zeMemAllocDevice(ctx, C.byref(d3), size, 4096, A["device"], C.byref(a_view))
            result["import_on_A_rc"] = rc_name(rc)
            if rc == 0:
                result["imported_ptr_equals_b_buf"] = bool(a_view.value == b_buf.value)
                rcs = []; times = []
                for _ in range(repeats):
                    rc2, dt = run(qa, cla, a_view, a_buf)
                    rcs.append(rc_name(rc2)); times.append(dt)
                    if rc2 != 0:
                        break
                result["push_via_import_rc"] = rcs
                if all(r == "SUCCESS" for r in rcs) and min(times) > 0:
                    result["push_gbps_best"] = round(size / min(times) / 1e9, 3)
                ze.check(run(qb, clb, h_dst, b_buf)[0], "readback B")
                got = C.string_at(h_dst, size)
                result["verdict_B_readback"] = ("PATTERN_ARRIVED" if got == pattern else
                                                "DEST_UNCHANGED" if got == b"\x00" * size else
                                                "ALL_FF" if got == b"\xff" * size else "PARTIAL")
                # and what does A see when it reads the imported view back? (pull through the import)
                C.memset(h_dst, 0, size)
                rc3, _ = run(qa, cla, h_dst, a_view)
                result["A_reads_import_rc"] = rc_name(rc3)
                if rc3 == 0:
                    got2 = C.string_at(h_dst, size)
                    result["A_reads_import_verdict"] = ("PATTERN" if got2 == pattern else "ZEROS" if got2 == b"\x00" * size else "OTHER")
                L.zeMemFree(ctx, a_view)
        for p in (a_buf, b_buf, h_src, h_dst, zeros):
            L.zeMemFree(ctx, p)
    finally:
        L.zeContextDestroy(ctx)
    return result


def health() -> dict:
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8082/health", timeout=5) as r:
            return {"llama_server": json.loads(r.read().decode())}
    except Exception as exc:  # noqa: BLE001
        return {"llama_server": f"UNREACHABLE: {exc}"}


def child(args: list[str], timeout: float) -> dict:
    cmd = [sys.executable, __file__, *args]
    t0 = time.perf_counter()
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                            env={k: v for k, v in os.environ.items() if k != "ZE_AFFINITY_MASK"})
    except subprocess.TimeoutExpired:
        return {"status": "HANG_KILLED", "timeout_s": timeout}
    dt = time.perf_counter() - t0
    out = {"status": "exit_%d" % cp.returncode, "wall_s": round(dt, 3)}
    try:
        out["result"] = json.loads(cp.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        out["stdout_tail"] = cp.stdout[-2000:]
    if cp.returncode != 0:
        out["stderr_tail"] = cp.stderr[-2000:]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("edge")
    e.add_argument("name", choices=["E0", "COPY", "IPC", "EXTMEM"])
    e.add_argument("--dir", default="push", choices=["push", "pull"])
    e.add_argument("--queue", default="copy", choices=["copy", "compute"])
    e.add_argument("--size", type=int, default=4096)
    e.add_argument("--repeats", type=int, default=1)
    r = sub.add_parser("run")
    r.add_argument("--out", default=None)
    r.add_argument("--ladder", default="4096,1048576,67108864,268435456")
    args = ap.parse_args()

    if args.cmd == "edge":
        if args.name == "E0":
            print(json.dumps(edge_inventory()))
        elif args.name == "IPC":
            print(json.dumps(edge_ipc(args.size)))
        elif args.name == "EXTMEM":
            print(json.dumps(edge_extmem(args.size, args.repeats)))
        else:
            print(json.dumps(edge_copy(args.dir, args.queue, args.size, args.repeats)))
        return 0

    receipts = {"probe": "LZ9-p2p-push", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "edges": {}}
    receipts["health_before"] = health()
    receipts["edges"]["E0_inventory"] = child(["edge", "E0"], 60)
    receipts["edges"]["E1_push_copy_4k"] = child(["edge", "COPY", "--dir", "push", "--queue", "copy", "--size", "4096"], 90)
    receipts["health_after_E1"] = health()
    receipts["edges"]["E2_pull_copy_4k"] = child(["edge", "COPY", "--dir", "pull", "--queue", "copy", "--size", "4096"], 90)
    receipts["health_after_E2"] = health()
    receipts["edges"]["E3_push_compute_4k"] = child(["edge", "COPY", "--dir", "push", "--queue", "compute", "--size", "4096"], 90)
    receipts["health_after_E3"] = health()

    def arrived(key: str) -> bool:
        return receipts["edges"][key].get("result", {}).get("verdict") == "PATTERN_ARRIVED"

    ladder_on = None
    if arrived("E1_push_copy_4k"):
        ladder_on = ("push", "copy")
    elif arrived("E3_push_compute_4k"):
        ladder_on = ("push", "compute")
    elif arrived("E2_pull_copy_4k"):
        ladder_on = ("pull", "copy")
    if ladder_on:
        for size in [int(s) for s in args.ladder.split(",")]:
            key = f"E4_{ladder_on[0]}_{ladder_on[1]}_{size}"
            receipts["edges"][key] = child(["edge", "COPY", "--dir", ladder_on[0], "--queue", ladder_on[1],
                                            "--size", str(size), "--repeats", "5"], 180)
            receipts[f"health_after_{key}"] = health()
    else:
        receipts["E4_ladder"] = "skipped - no copy edge delivered the pattern"
    receipts["health_after"] = health()
    text = json.dumps(receipts, indent=1)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
