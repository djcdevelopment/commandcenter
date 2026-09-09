// LZ9-E11: a self-contained port of Intel's own black-box test
// level_zero/core/test/black_box_tests/zello_p2p_copy.cpp (compute-runtime, MIT),
// built against the installed Level Zero SDK 1.32.0 with the vendor headers only.
//
// Same logic as the vendor sample: one context over all devices of the first driver,
// device i FILLS its peer's buffer (a peer WRITE), barrier, then COPIES the peer's
// buffer back to host memory (a peer READ), then validates the bytes.
// The vendor sample std::terminate()s when zeDeviceCanAccessPeer is false, so it never
// touches the fabric on this box. `--force` skips that guard and reports every return
// code instead, so the driver's answer to a peer fill and a peer read is recorded.
#include <level_zero/ze_api.h>

#include <cstdio>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

static const char *rcName(ze_result_t rc) {
    switch (rc) {
    case ZE_RESULT_SUCCESS: return "SUCCESS";
    case ZE_RESULT_ERROR_DEVICE_LOST: return "ERROR_DEVICE_LOST";
    case ZE_RESULT_ERROR_OUT_OF_HOST_MEMORY: return "ERROR_OUT_OF_HOST_MEMORY";
    case ZE_RESULT_ERROR_OUT_OF_DEVICE_MEMORY: return "ERROR_OUT_OF_DEVICE_MEMORY";
    case ZE_RESULT_ERROR_UNSUPPORTED_FEATURE: return "ERROR_UNSUPPORTED_FEATURE";
    case ZE_RESULT_ERROR_INVALID_ARGUMENT: return "ERROR_INVALID_ARGUMENT";
    case ZE_RESULT_ERROR_INVALID_NULL_POINTER: return "ERROR_INVALID_NULL_POINTER";
    case ZE_RESULT_ERROR_UNINITIALIZED: return "ERROR_UNINITIALIZED";
    default: return "OTHER";
    }
}

#define CHECK(call)                                                                        \
    do {                                                                                   \
        ze_result_t rc_ = (call);                                                          \
        if (rc_ != ZE_RESULT_SUCCESS) {                                                    \
            std::printf("FATAL %s -> %s (0x%x) at line %d\n", #call, rcName(rc_), rc_, __LINE__); \
            return 2;                                                                      \
        }                                                                                  \
    } while (0)

int main(int argc, char **argv) {
    bool force = false;
    for (int i = 1; i < argc; ++i) if (std::string(argv[i]) == "--force") force = true;
    const size_t allocSize = 4096;

    CHECK(zeInit(0));
    uint32_t driverCount = 0;
    CHECK(zeDriverGet(&driverCount, nullptr));
    std::vector<ze_driver_handle_t> drivers(driverCount);
    CHECK(zeDriverGet(&driverCount, drivers.data()));

    // pick the driver that owns the discrete B70s (two devices named "B70")
    ze_driver_handle_t driver = nullptr;
    std::vector<ze_device_handle_t> devices;
    for (auto d : drivers) {
        uint32_t n = 0;
        CHECK(zeDeviceGet(d, &n, nullptr));
        std::vector<ze_device_handle_t> devs(n);
        CHECK(zeDeviceGet(d, &n, devs.data()));
        std::vector<ze_device_handle_t> b70;
        for (auto dev : devs) {
            ze_device_properties_t p = {ZE_STRUCTURE_TYPE_DEVICE_PROPERTIES};
            CHECK(zeDeviceGetProperties(dev, &p));
            if (std::strstr(p.name, "B70")) b70.push_back(dev);
        }
        if (b70.size() == 2) { driver = d; devices = b70; break; }
    }
    if (!driver) { std::printf("FATAL: no driver with exactly two B70s\n"); return 2; }

    ze_context_desc_t cdesc = {ZE_STRUCTURE_TYPE_CONTEXT_DESC};
    ze_context_handle_t context = nullptr;
    CHECK(zeContextCreate(driver, &cdesc, &context));

    const uint32_t deviceCount = 2;
    for (uint32_t i = 0; i < deviceCount; ++i) {
        for (uint32_t j = 0; j < deviceCount; ++j) {
            if (i == j) continue;
            ze_device_p2p_properties_t p2p = {ZE_STRUCTURE_TYPE_DEVICE_P2P_PROPERTIES};
            CHECK(zeDeviceGetP2PProperties(devices[i], devices[j], &p2p));
            ze_bool_t can = false;
            CHECK(zeDeviceCanAccessPeer(devices[i], devices[j], &can));
            std::printf("device %u -> %u: canAccessPeer=%u p2pFlags=0x%x\n", i, j, (unsigned)can, (unsigned)p2p.flags);
            if (!can && !force) {
                std::printf("VENDOR_GUARD: Device %u cannot access %u -> the vendor sample std::terminate()s here; rerun with --force to record the driver's answer\n", i, j);
                return 3;
            }
        }
    }

    struct Dev { ze_command_queue_handle_t q; ze_command_list_handle_t l; void *src; void *dst; std::vector<uint8_t> back; };
    std::vector<Dev> dev(deviceCount);
    for (uint32_t i = 0; i < deviceCount; ++i) {
        dev[i].back.assign(allocSize, 0);
        ze_command_queue_desc_t qd = {ZE_STRUCTURE_TYPE_COMMAND_QUEUE_DESC};
        qd.ordinal = 0; qd.mode = ZE_COMMAND_QUEUE_MODE_ASYNCHRONOUS; qd.priority = ZE_COMMAND_QUEUE_PRIORITY_NORMAL;
        CHECK(zeCommandQueueCreate(context, devices[i], &qd, &dev[i].q));
        ze_command_list_desc_t ld = {ZE_STRUCTURE_TYPE_COMMAND_LIST_DESC};
        ld.commandQueueGroupOrdinal = 0;
        CHECK(zeCommandListCreate(context, devices[i], &ld, &dev[i].l));
        ze_device_mem_alloc_desc_t md = {ZE_STRUCTURE_TYPE_DEVICE_MEM_ALLOC_DESC};
        md.ordinal = 0;
        CHECK(zeMemAllocDevice(context, &md, allocSize, 1, devices[i], &dev[i].src));
        CHECK(zeMemAllocDevice(context, &md, allocSize, 1, devices[i], &dev[i].dst));
    }

    bool allOk = true;
    for (uint32_t i = 0; i < deviceCount; ++i) {
        uint32_t target = (i == 0) ? deviceCount - 1 : i - 1;
        uint8_t value = static_cast<uint8_t>(i + 10);
        // peer WRITE: device i fills device target's dst buffer
        ze_result_t rcFill = zeCommandListAppendMemoryFill(dev[i].l, dev[target].dst, &value, sizeof(value), allocSize, nullptr, 0, nullptr);
        std::printf("device %u FILL peer %u dst (peer write)  -> %s\n", i, target, rcName(rcFill));
        ze_result_t rcBar = ZE_RESULT_SUCCESS, rcCopy = ZE_RESULT_SUCCESS, rcClose = ZE_RESULT_SUCCESS, rcExec = ZE_RESULT_SUCCESS, rcSync = ZE_RESULT_SUCCESS;
        if (rcFill == ZE_RESULT_SUCCESS) {
            rcBar = zeCommandListAppendBarrier(dev[i].l, nullptr, 0, nullptr);
        }
        // peer READ: device i copies device target's dst buffer to host
        rcCopy = zeCommandListAppendMemoryCopy(dev[i].l, dev[i].back.data(), dev[target].dst, allocSize, nullptr, 0, nullptr);
        std::printf("device %u COPY peer %u dst -> host (peer read) -> %s\n", i, target, rcName(rcCopy));
        rcClose = zeCommandListClose(dev[i].l);
        if (rcClose == ZE_RESULT_SUCCESS) {
            rcExec = zeCommandQueueExecuteCommandLists(dev[i].q, 1, &dev[i].l, nullptr);
            std::printf("device %u execute -> %s\n", i, rcName(rcExec));
            if (rcExec == ZE_RESULT_SUCCESS) {
                rcSync = zeCommandQueueSynchronize(dev[i].q, std::numeric_limits<uint64_t>::max());
                std::printf("device %u synchronize -> %s\n", i, rcName(rcSync));
            }
        }
        bool ok = (rcFill == ZE_RESULT_SUCCESS && rcCopy == ZE_RESULT_SUCCESS && rcExec == ZE_RESULT_SUCCESS && rcSync == ZE_RESULT_SUCCESS);
        if (ok) {
            size_t good = 0;
            for (size_t k = 0; k < allocSize; ++k) good += (dev[i].back[k] == value);
            std::printf("device %u readback: %zu/%zu bytes == 0x%02x (first byte 0x%02x)\n", i, good, allocSize, value, dev[i].back[0]);
            ok = (good == allocSize);
        }
        allOk = allOk && ok;
    }

    for (uint32_t i = 0; i < deviceCount; ++i) {
        zeCommandQueueDestroy(dev[i].q); zeCommandListDestroy(dev[i].l);
        zeMemFree(context, dev[i].src); zeMemFree(context, dev[i].dst);
    }
    zeContextDestroy(context);
    std::printf("Zello P2P Copy (port): %s\n", allOk ? "PASSED" : "FAILED");
    return allOk ? 0 : 1;
}
