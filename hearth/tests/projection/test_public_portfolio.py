import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hearth.projection.call_mix_dashboard import FAMILY_ORDER
from hearth.projection.public_portfolio import (
    AGENT_LANE_BY_ADAPTER,
    AGENT_LANE_LABELS,
    AGENT_LANE_ORDER,
    EXECUTION_FAMILY,
    EXECUTION_FAMILY_LABELS,
    EXECUTION_FAMILY_ORDER,
    FAMILY_IDS,
    FORBIDDEN_SOURCE_KEYS,
    MACRO_IDS,
    PUBLIC_KEYS,
    SCHEMA_PATH,
    PublicProjectionError,
    _walk_keys,
    build_snapshot,
    validate_public_snapshot,
    write_snapshot,
)


def _caller(identifier: str) -> dict:
    return {"id": identifier, "runner_class": "local", "node": "10.0.0.8"}


def _principal(identifier: str) -> dict:
    return {"type": "agent", "id": identifier, "authenticated": True}


def _gateway_event(tool: str, ts: str, **overrides) -> dict:
    event = {
        "schema": "hearth-event.v1",
        "event_id": "private-event-id",
        "ts": ts,
        "caller": _caller("secret-host"),
        "tool": tool,
        "args_preview": r'{"path":"C:\\Users\\derek\\private.txt"}',
        "result_digest": "private-result",
        "ok": True,
        "error": None,
        "duration_ms": 10,
        "cost": {"tokens_in": None, "tokens_out": None, "watt_s": None},
        "task_id": "private-task",
    }
    event.update(overrides)
    return event


def _execution_event(sequence: int, event_type: str, ts: str, **overrides) -> dict:
    event = {
        "schema": "hearth-execution-event.v1",
        "sequence": sequence,
        "event_id": f"evt_{sequence:032x}",
        "timestamp": ts,
        "event_type": event_type,
        "request_id": f"req_{'a' * 32}",
        "job_id": f"job_{'b' * 32}",
        "invocation_id": None,
        "principal": None,
        "source": None,
        "operation": None,
        "desired": None,
        "observed": None,
        "artifacts": [],
        "reason": r"failed at C:\Users\derek\secret.txt",
    }
    event.update(overrides)
    return event


def _execution_job(
    sequence: int,
    *,
    day: str,
    minute: int,
    operation,
    terminal: str,
    principal: dict | None = None,
    source: dict | None = None,
) -> list[dict]:
    """One accepted request plus its terminal event, as a valid replayable pair."""
    request_id = f"req_{sequence:032x}"
    job_id = f"job_{sequence:032x}"
    return [
        _execution_event(
            sequence,
            "request.accepted",
            f"{day}T10:{minute:02d}:00Z",
            request_id=request_id,
            job_id=job_id,
            operation=operation,
            principal=principal,
            source=source,
        ),
        _execution_event(
            sequence + 1,
            terminal,
            f"{day}T10:{minute:02d}:30Z",
            request_id=request_id,
            job_id=job_id,
        ),
    ]


# Every identifier the fixture puts on the private side of the boundary. The
# privacy test treats each one as a forbidden substring of the public bytes.
FIXTURE_IDENTIFIERS = (
    "secret-host",
    "10.0.0.8",
    "private-event-id",
    "private-result",
    "private-task",
    "secret.txt",
    "secret-key",
    "Users",
    "claude-frontier",
    "codex-cli",
    "dmos-poc",
    "bf6-dispatcher",
    "botherder-am4",
    "omen-worker-1",
    "mechnet-watchdog",
    "__unauthenticated__",
    "docker-open-notebook-facade",
    "bf6-hatchet",
    "cluster.rebalance",
)


def _reseal(snapshot: dict) -> dict:
    """Re-derive the content digest so a mutation is judged by the schema, not the hash."""
    payload = {key: value for key, value in snapshot.items() if key not in {"integrity", "snapshot_id"}}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {**payload, "snapshot_id": f"sha256:{digest}", "integrity": {"content_sha256": digest}}


def _row(rows: list[dict], row_id: str) -> dict:
    return next(row for row in rows if row["id"] == row_id)


class PublicPortfolioProjectionTests(unittest.TestCase):
    maxDiff = None

    def _gateway_events(self) -> list[dict]:
        return [
            # Door status, unmapped caller -> "other" lane.
            _gateway_event("kernel_status", "2026-08-31T01:00:00Z"),
            *[
                _gateway_event(
                    "local_generate",
                    f"2026-09-0{1 + (index % 2)}T02:00:00Z",
                    caller=_caller("claude-frontier"),
                    backend="omen-arc",
                    cost={"tokens_in": 100, "tokens_out": 20, "watt_s": None},
                )
                for index in range(12)
            ],
            _gateway_event(
                "mechnet_watchdog.rung_state",
                "2026-09-02T03:00:00Z",
                caller=_caller("mechnet-watchdog"),
                outcome="at_rate",
            ),
            # Image generation: 5 submits against 20 status polls, so the poll
            # volume dwarfs the job volume and the two cannot track each other.
            *[
                _gateway_event("submit_image", f"2026-09-01T04:00:0{index}Z", caller=_caller("dmos-poc"))
                for index in range(3)
            ],
            *[
                _gateway_event("submit_image", f"2026-09-01T04:10:0{index}Z", caller=_caller("claude-frontier"))
                for index in range(2)
            ],
            *[
                _gateway_event("get_image_status", f"2026-09-01T04:20:{index:02d}Z", caller=_caller("dmos-poc"))
                for index in range(20)
            ],
            *[
                _gateway_event("submit_render", f"2026-09-02T05:00:0{index}Z", caller=_caller("bf6-dispatcher"))
                for index in range(2)
            ],
            *[
                _gateway_event("get_media_status", f"2026-09-02T05:10:0{index}Z", caller=_caller("bf6-dispatcher"))
                for index in range(6)
            ],
            *[
                _gateway_event("git_status", f"2026-09-01T06:00:0{index}Z", caller=_caller("codex-cli"))
                for index in range(2)
            ],
            *[
                _gateway_event("submit_task", f"2026-09-01T07:00:0{index}Z", caller=_caller("botherder-am4"))
                for index in range(4)
            ],
            *[
                _gateway_event("run_tests", f"2026-09-02T08:00:0{index}Z", caller=_caller("omen-worker-1"))
                for index in range(2)
            ],
            _gateway_event("list_dir", "2026-09-02T09:00:00Z", caller=_caller("__unauthenticated__")),
        ]

    def _execution_events(self) -> list[dict]:
        return [
            _execution_event(
                1,
                "request.accepted",
                "2026-09-01T01:00:00Z",
                desired={"idempotency_key": "secret-key"},
            ),
            _execution_event(2, "invocation.started", "2026-09-01T01:00:01Z", invocation_id=f"inv_{'c' * 31}1"),
            _execution_event(3, "invocation.failed", "2026-09-01T01:00:02Z", invocation_id=f"inv_{'c' * 31}1"),
            _execution_event(4, "invocation.started", "2026-09-01T01:00:03Z", invocation_id=f"inv_{'c' * 31}2"),
            _execution_event(5, "invocation.succeeded", "2026-09-01T01:00:04Z", invocation_id=f"inv_{'c' * 31}2"),
            _execution_event(
                6,
                "artifact.recorded",
                "2026-09-01T01:00:05Z",
                artifacts=[{
                    "artifact_id": f"art_{'d' * 32}",
                    "sha256": "e" * 64,
                    "size": 42,
                    "media_type": "text/plain",
                    "filename": "secret.txt",
                }],
            ),
            _execution_event(7, "job.succeeded", "2026-09-01T01:00:06Z"),
            _execution_event(8, "delivery.projected", "2026-09-01T01:00:07Z"),
            *_execution_job(
                9, day="2026-09-01", minute=1, operation="image.generate",
                principal=_principal("dmos-poc"), terminal="job.succeeded",
            ),
            *_execution_job(
                11, day="2026-09-01", minute=2, operation="image.generate",
                principal=_principal("dmos-poc"), terminal="job.failed",
            ),
            # Adapter-only attribution: no principal at all.
            *_execution_job(
                13, day="2026-09-01", minute=3, operation="bf6.process_segment",
                source={"transport": "http", "adapter": "bf6-hatchet"}, terminal="job.succeeded",
            ),
            *_execution_job(
                15, day="2026-09-01", minute=4, operation="bf6.render_clip_workflow",
                principal=_principal("bf6-dispatcher"), terminal="job.cancelled",
            ),
            # Precedence discriminator (D-015): principal and adapter map to
            # DIFFERENT lanes, so this job counts as claude_code, not irc_adapter.
            *_execution_job(
                17, day="2026-09-01", minute=5, operation="media.render",
                principal=_principal("claude-frontier"),
                source={"transport": "irc", "adapter": "botherder-am4"},
                terminal="job.succeeded",
            ),
            *_execution_job(
                19, day="2026-09-02", minute=6, operation="media.podcast",
                principal=_principal("claude-frontier"), terminal="job.succeeded",
            ),
            # Precedence (D-015): the principal maps to "other", so the adapter decides.
            *_execution_job(
                21, day="2026-09-02", minute=7, operation="llm.chat",
                principal=_principal("docker-open-notebook-facade"),
                source={"transport": "irc", "adapter": "botherder-am4"},
                terminal="job.succeeded",
            ),
            # Unknown operation string -> "other", and the string itself stays private.
            *_execution_job(
                23, day="2026-09-02", minute=8, operation="cluster.rebalance",
                principal=_principal("codex-cli"), terminal="job.expired",
            ),
            # operation: None -> "other".
            *_execution_job(
                25, day="2026-09-02", minute=9, operation=None,
                principal=_principal("omen-worker-1"), terminal="job.succeeded",
            ),
            *_execution_job(
                27, day="2026-09-02", minute=10, operation="inference.generate",
                principal=_principal("claude-frontier"), terminal="job.succeeded",
            ),
        ]

    def _ledgers(self, root: Path) -> tuple[Path, Path]:
        gateway = root / "gateway.ndjson"
        gateway.write_text(
            "\n".join(json.dumps(event) for event in self._gateway_events()) + "\n", encoding="utf-8"
        )
        execution = root / "execution.ndjson"
        execution.write_text(
            "\n".join(json.dumps(event) for event in self._execution_events()) + "\n", encoding="utf-8"
        )
        return gateway, execution

    def _seat_receipts(self) -> list[dict]:
        """Thirteen receipts across two seats: twelve measured, one unknown; all on one week."""
        from hearth.seats.receipts import attempt_identity, provider_identity, seat_identity
        rows = []
        prefix = "b" * 64
        for seat_index, count in ((0, 9), (1, 4)):
            seat = seat_identity(f"secret-seat-{seat_index}-8096", prefix)
            for task in range(count):
                measured = not (seat_index == 1 and task == 3)
                rows.append({
                    "schema": "seat.physical-attempt.v1",
                    "seat_id": seat,
                    "attempt_id": attempt_identity(seat, task),
                    "task": task,
                    "slot": 0,
                    "log_basename": f"secret-seat-{seat_index}-8096",
                    "seat_epoch_sha256": prefix,
                    "provider": {"execution_class": "local", "identity_sha256": provider_identity("secret-model", "build 1 (abc)", 4096),
                                 "model_name": "secret-model", "build": "build 1 (abc)", "n_ctx_seq": 4096},
                    "started_at": f"2026-07-0{seat_index + 1}T10:00:{task:02d}.000000Z",
                    "finished_at": f"2026-07-0{seat_index + 1}T10:01:{task:02d}.000000Z",
                    "timestamp_derivation": {"method": "log_mtime_minus_last_elapsed", "epoch_start": "2026-07-01T09:00:00.000000Z",
                                             "log_mtime": "2026-07-01T12:00:00.000000Z", "error_bound_s": 2},
                    "usage": {"tokens_in": 1000 + task, "tokens_out": 10 + task} if measured else None,
                    "usage_unknown_reason": None if measured else "server log has no timing block for this task",
                    "timing": {"prompt_ms": 12.5, "predicted_ms": 3.25} if measured else None,
                    "outcome": "succeeded" if measured else "unknown",
                    "source": {"transport": "server-log", "adapter": "seat-log-harvest",
                               "execution_mode": "external", "accounting_owner": "direct"},
                    "harvested_at": "2026-07-02T00:00:00.000000Z",
                })
        return rows

    def _seat_ledger(self, root: Path, rows: list[dict] | None = None) -> Path:
        seats = root / "seats.ndjson"
        rows = self._seat_receipts() if rows is None else rows
        seats.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
        return seats

    def _snapshot(self, root: Path, with_seats: bool = False) -> dict:
        gateway, execution = self._ledgers(root)
        seats = self._seat_ledger(root) if with_seats else root / "no-seats.ndjson"
        return build_snapshot(gateway, execution, seats, exporter_revision="test")

    # ---- research seats (ADR-0047 Phase B) ------------------------------

    def test_seat_cohort_is_aggregated_apart_from_every_other_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            without = self._snapshot(Path(tmp))
        with tempfile.TemporaryDirectory() as tmp:
            with_seats = self._snapshot(Path(tmp), with_seats=True)
        cohort = with_seats["seat_inference"]
        self.assertEqual(cohort["attempts"], 13)
        self.assertEqual(cohort["measured_attempts"], 12)
        self.assertEqual(cohort["unknown_usage_attempts"], 1)
        self.assertEqual(cohort["failed_attempts"], 0)
        self.assertEqual(cohort["seats"], 2)
        self.assertEqual(cohort["tokens_in"], sum(1000 + t for t in range(9)) + sum(1000 + t for t in (0, 1, 2)))
        self.assertEqual(cohort["tokens_out"], sum(10 + t for t in range(9)) + sum(10 + t for t in (0, 1, 2)))
        self.assertIn("seat_prefix_sha256", with_seats["provenance"])
        self.assertIn("research seats harvested", with_seats["coverage"]["boundary"])
        self.assertTrue(any("Research-seat rows" in s for s in with_seats["coverage"]["limitations"]))
        self.assertEqual(with_seats["gateway"], without["gateway"])
        self.assertEqual(with_seats["execution"], without["execution"])
        rows_without = {row["week_start"]: row for row in without["weekly"]}
        for row_with in with_seats["weekly"]:
            row_without = rows_without.get(row_with["week_start"])
            for key in ("operations", "learning", "inference", "media", "work_plane", "other"):
                # A week only the seats saw carries true gateway zeros.
                self.assertEqual(row_with[key], row_without[key] if row_without else 0)
        seat_cells = {row["week_start"]: row["seat_attempts"] for row in with_seats["weekly"]}
        self.assertEqual(seat_cells.get("2026-06-29"), 13)
        self.assertTrue(all(v == 0 for w, v in seat_cells.items() if w != "2026-06-29"))

    def test_absent_seat_ledger_emits_no_cohort_and_zero_seat_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        self.assertNotIn("seat_inference", snapshot)
        self.assertNotIn("seat_prefix_sha256", snapshot["provenance"])
        self.assertEqual(snapshot["coverage"]["boundary"], "calls observed at the HEARTH gateway and execution ledgers")
        self.assertTrue(all(row["seat_attempts"] == 0 for row in snapshot["weekly"]))

    def test_small_seat_week_is_suppressed_and_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway, execution = self._ledgers(root)
            rows = self._seat_receipts()[:4]
            snapshot = build_snapshot(gateway, execution, self._seat_ledger(root, rows), exporter_revision="test")
        row = next(r for r in snapshot["weekly"] if r["week_start"] == "2026-06-29")
        self.assertIsNone(row["seat_attempts"])
        self.assertGreaterEqual(row["suppressed_cells"], 1)

    def test_seat_ledger_faults_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway, execution = self._ledgers(root)
            rows = self._seat_receipts()
            duplicate = self._seat_ledger(root, rows + rows[:1])
            with self.assertRaisesRegex(PublicProjectionError, "repeats an attempt"):
                build_snapshot(gateway, execution, duplicate, exporter_revision="test")
            bad = json.loads(json.dumps(rows))
            bad[0]["usage"]["tokens_in"] = -5
            broken = self._seat_ledger(root, bad)
            with self.assertRaisesRegex(PublicProjectionError, "not a valid receipt"):
                build_snapshot(gateway, execution, broken, exporter_revision="test")

    def test_seat_identifiers_never_survive_serialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp), with_seats=True)
        serialized = json.dumps(snapshot)
        for identifier in ("secret-seat", "secret-model", "seat-log-harvest", "8096", "build 1"):
            self.assertNotIn(identifier, serialized)

    # ---- structure -----------------------------------------------------

    def test_family_maps_cover_exactly_the_classifier_families(self) -> None:
        self.assertEqual(set(FAMILY_IDS), set(FAMILY_ORDER))
        self.assertEqual(set(MACRO_IDS), set(FAMILY_ORDER))
        self.assertEqual(len(set(FAMILY_IDS.values())), len(FAMILY_ORDER))

    def test_schema_enums_match_the_projection_constants(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        defs = schema["$defs"]
        self.assertEqual(set(defs["family"]["properties"]["id"]["enum"]), set(FAMILY_IDS.values()))
        self.assertEqual(defs["executionFamily"]["properties"]["id"]["enum"], EXECUTION_FAMILY_ORDER)
        self.assertEqual(defs["agentLane"]["properties"]["id"]["enum"], AGENT_LANE_ORDER)
        self.assertEqual(set(EXECUTION_FAMILY.values()) - set(EXECUTION_FAMILY_ORDER), set())
        for label in list(EXECUTION_FAMILY_LABELS.values()) + list(AGENT_LANE_LABELS.values()):
            self.assertLessEqual(len(label), 40)

    def test_forbidden_source_keys_can_never_be_public_keys(self) -> None:
        self.assertIn("operation", FORBIDDEN_SOURCE_KEYS)
        self.assertLessEqual({"caller", "principal", "source", "job_id"}, FORBIDDEN_SOURCE_KEYS)
        self.assertTrue(FORBIDDEN_SOURCE_KEYS.isdisjoint(PUBLIC_KEYS))

    # ---- projection ----------------------------------------------------

    def test_projection_emits_only_aggregates_and_fixed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        self.assertEqual(snapshot["gateway"]["events"], 56)
        self.assertEqual(snapshot["gateway"]["ok_events"], 56)
        self.assertEqual(snapshot["gateway"]["operational_observations"], 28)
        self.assertEqual(snapshot["gateway"]["work_and_learning_events"], 28)
        self.assertEqual(snapshot["gateway"]["unclassified_events"], 0)
        self.assertEqual(snapshot["gateway"]["inference"]["local_calls"], 12)
        self.assertEqual(snapshot["gateway"]["inference"]["tokens_in"], 1200)
        self.assertEqual(snapshot["execution"]["events"], 28)
        self.assertEqual(snapshot["execution"]["requests_accepted"], 11)
        self.assertEqual(snapshot["execution"]["retried_jobs"], 1)
        self.assertEqual(snapshot["execution"]["recovered_jobs"], 1)
        self.assertEqual(snapshot["execution"]["artifacts_recorded"], 1)
        self.assertTrue(snapshot["execution"]["projection_replay_verified"])
        self.assertEqual(snapshot["mechnet"]["snapshot_state"], "at_rate")
        rendered = json.dumps(snapshot, ensure_ascii=False)
        for private in ("secret-host", "10.0.0.8", "Users", "private-task", "secret.txt", "secret-key"):
            self.assertNotIn(private, rendered)
        validate_public_snapshot(snapshot)

    def test_fourteen_gateway_families_in_fixed_order_with_relabelled_door_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        families = snapshot["gateway"]["families"]
        self.assertEqual(len(families), 14)
        self.assertEqual([row["id"] for row in families], [FAMILY_IDS[name] for name in FAMILY_ORDER])
        self.assertEqual(_row(families, "door_status")["label"], "Door status / polling")
        self.assertEqual(_row(families, "image_generation")["label"], "Image generation")
        self.assertEqual(
            {row["id"]: row["count"] for row in families},
            {
                "health_automation": 1,
                "door_status": 27,
                "learning_retro": 0,
                "local_inference": 12,
                "cloud_inference": 0,
                "image_generation": 5,
                "media_render": 2,
                "fleet_builds": 4,
                "git_vcs": 2,
                "filesystem": 1,
                "test_assay": 2,
                "catalog_hardware": 0,
                "scheduler": 0,
                "other": 0,
            },
        )

    def test_execution_jobs_are_grouped_into_six_operation_families(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        by_family = snapshot["execution"]["by_family"]
        self.assertEqual([row["id"] for row in by_family], EXECUTION_FAMILY_ORDER)
        self.assertEqual(
            [
                (
                    row["id"],
                    row["requests_accepted"],
                    row["jobs_succeeded"],
                    row["jobs_failed"],
                    row["jobs_cancelled"],
                    row["jobs_expired"],
                )
                for row in by_family
            ],
            [
                ("image_generation", 2, 1, 1, 0, 0),
                ("video_highlight_render", 2, 1, 0, 1, 0),
                ("media_render", 1, 1, 0, 0, 0),
                ("media_pipeline", 1, 1, 0, 0, 0),
                ("inference", 2, 2, 0, 0, 0),
                ("other", 3, 2, 0, 0, 1),
            ],
        )
        self.assertEqual(
            sum(row["requests_accepted"] for row in by_family),
            snapshot["execution"]["requests_accepted"],
        )
        self.assertEqual(
            sum(row["jobs_succeeded"] for row in by_family), snapshot["execution"]["jobs_succeeded"]
        )
        self.assertEqual(_row(by_family, "video_highlight_render")["label"], "Clippy · BF6 highlight renders")

    def test_work_is_grouped_into_eight_agent_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        by_agent = snapshot["gateway"]["by_agent"]
        self.assertEqual([row["id"] for row in by_agent], AGENT_LANE_ORDER)
        self.assertEqual(
            [
                (row["id"], row["calls"], row["work_calls"], row["jobs_accepted"], row["jobs_succeeded"])
                for row in by_agent
            ],
            [
                ("claude_code", 14, 14, 3, 3),
                ("codex", 2, 2, 1, 0),
                ("dmos_image_client", 23, 3, 2, 1),
                ("clippy_dispatcher", 8, 2, 2, 1),
                ("irc_adapter", 4, 4, 1, 1),
                ("fleet_workers", 2, 2, 1, 1),
                ("automation", 1, 0, 0, 0),
                ("other", 2, 1, 1, 1),
            ],
        )
        self.assertEqual(sum(row["calls"] for row in by_agent), snapshot["gateway"]["events"])
        self.assertEqual(
            sum(row["jobs_accepted"] for row in by_agent), snapshot["execution"]["requests_accepted"]
        )
        # Polling lanes must not read as work lanes.
        self.assertLess(_row(by_agent, "dmos_image_client")["work_calls"], _row(by_agent, "dmos_image_client")["calls"])
        self.assertEqual(_row(by_agent, "automation")["work_calls"], 0)
        # Two execution jobs carry adapter "botherder-am4". Only the one with no
        # lane-mapped principal lands in irc_adapter, so principal wins the tie.
        self.assertEqual(_row(by_agent, "irc_adapter")["jobs_accepted"], 1)
        self.assertEqual(_row(by_agent, "claude_code")["jobs_accepted"], 3)

    def test_gateway_calls_and_execution_jobs_are_distinct_quantities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        gateway_image = _row(snapshot["gateway"]["families"], "image_generation")["count"]
        job_image = _row(snapshot["execution"]["by_family"], "image_generation")["requests_accepted"]
        self.assertEqual(gateway_image, 5)
        self.assertEqual(job_image, 2)
        self.assertNotEqual(gateway_image, job_image)
        # 27 door-status polls against 11 accepted jobs: the two series cannot track each other.
        self.assertGreater(
            _row(snapshot["gateway"]["families"], "door_status")["count"],
            snapshot["execution"]["requests_accepted"],
        )

    def test_weekly_rows_carry_the_media_macro_and_suppress_small_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        self.assertEqual(len(snapshot["weekly"]), 1)
        first_week = snapshot["weekly"][0]
        self.assertEqual(first_week["week_start"], "2026-08-31")
        self.assertIn("media", first_week)
        # media = 7 and work_plane = 9 fall under the minimum public cell of 10.
        self.assertIsNone(first_week["media"])
        self.assertIsNone(first_week["work_plane"])
        # operations = 28 and inference = 12 clear it, so suppression is not blanket.
        self.assertEqual(first_week["operations"], 28)
        self.assertEqual(first_week["inference"], 12)
        self.assertEqual(first_week["learning"], 0)
        self.assertEqual(first_week["other"], 0)
        self.assertEqual(first_week["suppressed_cells"], 2)

    def test_coverage_limitations_state_the_new_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        limitations = snapshot["coverage"]["limitations"]
        for sentence in (
            "Image and media rows count execution-ledger jobs (one per accepted request), never gateway status polls.",
            "Image jobs run before the execution ledger existed (May 2026) are not claimed.",
            "Agent lanes are fixed labels derived from caller class; caller identities are never published.",
        ):
            self.assertIn(sentence, limitations)

    # ---- privacy -------------------------------------------------------

    def test_no_identifier_or_operation_string_survives_serialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        rendered = json.dumps(snapshot, ensure_ascii=False)
        for operation in EXECUTION_FAMILY:
            self.assertNotIn(operation, rendered)
        for identifier in AGENT_LANE_BY_ADAPTER:
            self.assertNotIn(identifier, rendered)
        for identifier in FIXTURE_IDENTIFIERS:
            self.assertNotIn(identifier, rendered)
        keys = set(_walk_keys(snapshot))
        self.assertEqual(keys & FORBIDDEN_SOURCE_KEYS, set())
        self.assertEqual(keys - PUBLIC_KEYS, set())

    def test_undeclared_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        snapshot["gateway"]["by_caller"] = []
        with self.assertRaisesRegex(PublicProjectionError, "undeclared keys"):
            validate_public_snapshot(snapshot)

    def test_injected_private_key_or_identifier_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = self._snapshot(Path(tmp))
        with_operation = json.loads(json.dumps(base))
        with_operation["execution"]["operation"] = "image.generate"
        with self.assertRaises(PublicProjectionError):
            validate_public_snapshot(with_operation)
        # Even resealed, an undeclared/forbidden key still fails closed.
        with self.assertRaisesRegex(PublicProjectionError, "undeclared keys"):
            validate_public_snapshot(_reseal(with_operation))

        with_caller = json.loads(json.dumps(base))
        _row(with_caller["gateway"]["by_agent"], "claude_code")["label"] = "claude-frontier"
        with self.assertRaisesRegex(PublicProjectionError, "digest"):
            validate_public_snapshot(with_caller)

    def test_schema_is_load_bearing_for_row_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = self._snapshot(Path(tmp))
        import jsonschema  # noqa: F401  - the schema gate is only meaningful when installed

        short = json.loads(json.dumps(base))
        short["execution"]["by_family"] = short["execution"]["by_family"][:-1]
        with self.assertRaisesRegex(PublicProjectionError, "schema validation failed"):
            validate_public_snapshot(_reseal(short))

        short_lanes = json.loads(json.dumps(base))
        short_lanes["gateway"]["by_agent"] = short_lanes["gateway"]["by_agent"][:-1]
        with self.assertRaisesRegex(PublicProjectionError, "schema validation failed"):
            validate_public_snapshot(_reseal(short_lanes))

    # ---- determinism, monotonicity, fault injection ---------------------

    def test_same_fixture_yields_byte_identical_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway, execution = self._ledgers(Path(tmp))
            first = build_snapshot(gateway, execution, exporter_revision="test")
            second = build_snapshot(gateway, execution, exporter_revision="test")
        self.assertEqual(
            json.dumps(first, sort_keys=True, ensure_ascii=False),
            json.dumps(second, sort_keys=True, ensure_ascii=False),
        )

    def test_appending_events_never_lowers_a_public_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway, execution = self._ledgers(root)
            before = build_snapshot(gateway, execution, exporter_revision="test")

            with gateway.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        _gateway_event(
                            "submit_image", "2026-09-02T11:00:00Z", caller=_caller("dmos-poc")
                        )
                    )
                    + "\n"
                )
            with execution.open("a", encoding="utf-8") as stream:
                for event in _execution_job(
                    29,
                    day="2026-09-02",
                    minute=11,
                    operation="media.animate",
                    principal=_principal("mechnet-orchestrator"),
                    terminal="job.succeeded",
                ):
                    stream.write(json.dumps(event) + "\n")
            after = build_snapshot(gateway, execution, exporter_revision="test")

        for family in EXECUTION_FAMILY_ORDER:
            self.assertGreaterEqual(
                _row(after["execution"]["by_family"], family)["requests_accepted"],
                _row(before["execution"]["by_family"], family)["requests_accepted"],
            )
        for lane in AGENT_LANE_ORDER:
            self.assertGreaterEqual(
                _row(after["gateway"]["by_agent"], lane)["calls"],
                _row(before["gateway"]["by_agent"], lane)["calls"],
            )
        self.assertEqual(_row(after["gateway"]["by_agent"], "dmos_image_client")["calls"], 24)
        self.assertEqual(_row(after["execution"]["by_family"], "media_pipeline")["requests_accepted"], 2)
        self.assertEqual(_row(after["gateway"]["by_agent"], "fleet_workers")["jobs_accepted"], 2)

    def test_terminal_event_without_an_accepted_request_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway, execution = self._ledgers(root)
            orphan = root / "orphan.ndjson"
            orphan.write_text(
                json.dumps(_execution_event(1, "job.succeeded", "2026-09-01T01:00:00Z")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PublicProjectionError, "replay failed"):
                build_snapshot(gateway, orphan, exporter_revision="test")

    def test_small_weekly_cells_are_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        first_week = snapshot["weekly"][0]
        self.assertIsNone(first_week["media"])
        self.assertGreater(first_week["suppressed_cells"], 0)

    def test_non_contiguous_execution_stream_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway, execution = self._ledgers(Path(tmp))
            events = [json.loads(line) for line in execution.read_text(encoding="utf-8").splitlines()]
            events[2]["sequence"] = 99
            execution.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(PublicProjectionError, "not contiguous"):
                build_snapshot(gateway, execution, exporter_revision="test")

    def test_invalid_execution_lifecycle_fails_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway, execution = self._ledgers(Path(tmp))
            events = [json.loads(line) for line in execution.read_text(encoding="utf-8").splitlines()]
            events[2]["invocation_id"] = f"inv_{'f' * 32}"
            execution.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(PublicProjectionError, "replay failed"):
                build_snapshot(gateway, execution, exporter_revision="test")

    def test_digest_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot(Path(tmp))
        snapshot["gateway"]["events"] += 1
        with self.assertRaisesRegex(PublicProjectionError, "digest"):
            validate_public_snapshot(snapshot)

    def test_write_snapshot_writes_the_named_output_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway, execution = self._ledgers(root)
            out = root / "staged" / "public-system-proof.v1.json"
            snapshot = write_snapshot(out, gateway, execution)
            self.assertTrue(out.exists())
            written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written["snapshot_id"], snapshot["snapshot_id"])
        validate_public_snapshot(written)


if __name__ == "__main__":
    unittest.main()
