# Lumberjacks Priority Path: Probe Design

The next path is priority/load-order probing, not deeper movement tuning. Build a local probe that classifies nearby Valheim objects into priority tiers, emits priority-load.jsonl, and optionally mirrors a priority manifest to Lumberjacks as an ordered side channel. The proof target is a local Era16 route packet showing that player-critical state, portals, structural anchors, and nearby interactive pieces can be identified and ordered ahead of distant cosmetic/support noise under dense build pressure.

## Priority Tier Taxonomy

- **player-critical**: `Character` with `Player` component; `ZNetView` owned by local player; `ZDO` with `m_owner` == `ZDOID` of local player.
- **portals**: `Piece` with `m_name` containing "Portal"; `ZNetView` with `m_syncMode` == `SyncMode.Allways` or `SyncMode.Owner`; `Piece` has `m_portal` flag set.
- **structural anchors**: `Piece` with `m_name` containing "Anchor" or "Foundation"; `ZNetView` with `m_syncMode` == `SyncMode.Allways`; `Piece` has `m_isStructural` flag set.
- **nearby interactive**: `Piece` with `m_name` containing "Lever", "Button", "Chest", "Workbench"; `ZNetView` with `m_syncMode` == `SyncMode.Allways` or `SyncMode.Owner`; distance < 15m from player.
- **distant cosmetic/support**: `Piece` with `m_name` containing "Fence", "Wall", "Tree", "Lamp", "Decoration"; `ZNetView` with `m_syncMode` == `SyncMode.None` or `SyncMode.Owner`; distance >= 15m from player.

## `priority-load.jsonl` Row Schema

Each row is a JSONL record with the following fields:

- `timestamp_utc`: string (ISO 8601, UTC)
- `session_id`: string (UUID from `NetworkSensePerfProbe`)
- `build_version`: string (e.g. "0.4.6")
- `priority_tier`: string (one of: "player-critical", "portals", "structural-anchors", "nearby-interactive", "distant-cosmetic-support")
- `entity_id`: string (ZDOID as hex string)
- `entity_type`: string (e.g. "Piece", "Character")
- `distance_m`: float (distance from player center)
- `is_owner_local`: bool
- `is_synced`: bool (true if `ZNetView` has `m_syncMode` != `SyncMode.None`)
- `piece_name`: string (value of `m_name` on `Piece`)
- `piece_flags`: string (comma-separated list of: "portal", "structural", "anchor", "interactive")

## Config Block: `[Priority]`

```ini
[Priority]
Enabled = true
ScanIntervalMs = 500
TierThresholds = 10,5,3,1,0
```

- `Enabled`: bool; if false, probe is disabled.
- `ScanIntervalMs`: int; frequency of scan loop in milliseconds.
- `TierThresholds`: comma-separated integers; number of entities per tier to emit (e.g. top 10 player-critical, top 5 portals, etc). 0 disables emission for that tier.

## Console Commands

- `network_sense_lumberjacks_priority start` — start the priority probe.
- `network_sense_lumberjacks_priority stop` — stop the priority probe.
- `network_sense_lumberjacks_priority status` — print current probe state and config.

## C# Class Skeleton

```csharp
public class NetworkSensePriorityProbe
{
    private bool _isEnabled;
    private int _scanIntervalMs;
    private int[] _tierThresholds;
    private float _scanRadius = 30f;
    private readonly Vector3[] _buffer = new Vector3[1024];
    private readonly List<ZDO> _candidates = new List<ZDO>();

    public void Start()
    {
        _isEnabled = true;
        // Schedule periodic scan
        Timing.RunCoroutine(ScanLoop());
    }

    public void Stop()
    {
        _isEnabled = false;
    }

    private IEnumerator ScanLoop()
    {
        while (_isEnabled)
        {
            var playerPos = Player.m_localPlayer?.transform.position ?? Vector3.zero;
            var count = Physics.OverlapSphereNonAlloc(playerPos, _scanRadius, _buffer);

            // Clear and reuse candidate list
            _candidates.Clear();
            for (int i = 0; i < count; i++)
            {
                var obj = _buffer[i];
                var zdo = ZNetScene.instance?.GetZDO(obj);
                if (zdo != null)
                {
                    _candidates.Add(zdo);
                }
            }

            // Classify and emit
            EmitPriorityLoadEvents(_candidates);

            yield return Timing.WaitForSeconds(_scanIntervalMs / 1000f);
        }
    }

    private void EmitPriorityLoadEvents(List<ZDO> candidates)
    {
        // Classification logic per tier
        // Emit JSONL rows
        // Apply tier thresholds
    }
}
```

## Non-Goals

- This probe does NOT apply Lumberjacks ordering as authoritative; it only emits a side-channel manifest.
- This probe does NOT change Valheim replication behavior, ZDO ownership, or network sync logic.
- This probe does NOT assume or enforce load-order decisions in-game; it is diagnostic and observational.
- This probe does NOT require Lumberjacks to be connected; it operates locally.
- This probe does NOT optimize or tune movement; that is a separate spike.
