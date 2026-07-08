# Lumberjacks Priority Path: Probe Design

The next path is priority/load-order probing, not deeper movement tuning. Build a local probe that classifies nearby Valheim objects into priority tiers, emits priority-load.jsonl, and optionally mirrors a priority manifest to Lumberjacks as an ordered side channel. The proof target is a local Era16 route packet showing that player-critical state, portals, structural anchors, and nearby interactive pieces can be identified and ordered ahead of distant cosmetic/support noise under dense build pressure.

## Priority Tier Taxonomy

- **player-critical**: `Character` with `Player` component, `ZNetView` owned by local player (`ZNetView.m_zdo.m_uid == ZNet.instance.m_playerId`), `m_isPlayer` flag set.
- **portals**: `Piece` with `Portal` component (e.g., `PortalController`), `m_isActive` true, `m_isOpen` true.
- **structural anchors**: `Piece` with `StructureAnchor` component, `m_isActive` true, `m_isPlaced` true, `m_isValid` true.
- **nearby interactive**: `Piece` or `Character` with `Interactable` component, `m_isActive` true, distance ≤ 15m from player.
- **distant cosmetic/support**: `Piece` or `Character` without any of the above components, distance > 15m from player.

## `priority-load.jsonl` Row Schema

Each row is a JSONL line with the following fields:

- `timestamp_utc`: string (ISO 8601, UTC, e.g., `2024-05-20T12:34:56.789Z`)
- `session_id`: string (UUID, from `ZNet.instance.m_playerId`)
- `build_version`: string (e.g., `0.4.6`)
- `priority_tier`: string (one of: `player-critical`, `portals`, `structural-anchors`, `nearby-interactive`, `distant-cosmetic-support`)
- `object_type`: string (e.g., `Piece`, `Character`)
- `object_name`: string (e.g., `Player`, `Portal`, `WoodenDoor`)
- `object_uid`: int (ZDO ID, `ZNetView.m_zdo.m_uid`)
- `distance_m`: float (distance from player, in meters)
- `is_local_owner`: bool (true if `ZNetView.m_zdo.m_uid == ZNet.instance.m_playerId`)
- `is_active`: bool (true if `m_isActive` on relevant component)
- `component_flags`: string (comma-separated list of detected components, e.g., `Portal,Interactable`)

## Config Block: `[Priority]`

```ini
[Priority]
Enabled = true
ScanIntervalMs = 500
TierThresholds = 15.0
```

- `Enabled`: bool, enables/disables the probe.
- `ScanIntervalMs`: int, interval between scans in milliseconds.
- `TierThresholds`: float, distance threshold (in meters) for classifying "nearby" vs "distant".

## Console Commands

- `network_sense_lumberjacks_priority [start|stop|status]`
  - `start`: begins the priority probe scan loop.
  - `stop`: halts the scan loop.
  - `status`: outputs current state (running, stopped) and last scan time.

## C# Class Skeleton: `NetworkSensePriorityProbe`

```csharp
using UnityEngine;
using System.Collections.Generic;
using System.Linq;

public class NetworkSensePriorityProbe
{
    private const float SCAN_RADIUS = 100f;
    private const int MAX_OBJECTS = 1024;

    private readonly List<ZNetView> _znetViews = new List<ZNetView>();
    private readonly Vector3[] _positions = new Vector3[MAX_OBJECTS];
    private readonly int[] _objectCounts = new int[MAX_OBJECTS];

    private bool _isRunning = false;
    private float _lastScanTime = 0f;
    private float _scanIntervalMs = 500f;

    public void Start()
    {
        _isRunning = true;
        _lastScanTime = Time.time;
    }

    public void Stop()
    {
        _isRunning = false;
    }

    public void Update()
    {
        if (!_isRunning) return;

        if (Time.time - _lastScanTime < _scanIntervalMs / 1000f) return;

        var player = Player.m_localPlayer;
        if (player == null) return;

        var playerPos = player.transform.position;
        var count = Physics.OverlapSphereNonAlloc(playerPos, SCAN_RADIUS, _positions, _objectCounts);

        for (int i = 0; i < count; i++)
        {
            var obj = _positions[i];
            var znetView = obj.GetComponent<ZNetView>();
            if (znetView == null) continue;

            var distance = Vector3.Distance(playerPos, obj.position);
            var tier = ClassifyTier(znetView, distance);
            EmitTelemetry(tier, znetView, distance);
        }

        _lastScanTime = Time.time;
    }

    private string ClassifyTier(ZNetView znetView, float distance)
    {
        // Implement tier logic based on component checks
        // Return one of: player-critical, portals, structural-anchors, nearby-interactive, distant-cosmetic-support
        return "distant-cosmetic-support";
    }

    private void EmitTelemetry(string tier, ZNetView znetView, float distance)
    {
        // Write to priority-load.jsonl using existing telemetry writer
        // Use timestamp_utc, session_id, build_version, tier, object_type, object_name, object_uid, distance_m, is_local_owner, is_active, component_flags
    }
}
```

## Explicit Non-Goals

- This probe does NOT apply Lumberjacks ordering as authoritative; it only emits a side-channel manifest.
- This probe does NOT change Valheim’s replication logic, ZDO ownership, or network behavior.
- This probe does NOT assume or enforce load-order decisions in the game engine.
- This probe does NOT replace or modify existing `network_sense_lumberjacks_projection` or `shadow` commands.
- This probe does NOT require or use the Lumberjacks WebSocket connection for its core logic — mirroring is optional and side-channel.
