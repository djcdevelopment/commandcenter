"""Dedicated durable executor for podcast, animation, and composite MediaGen jobs."""

from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import os
import queue
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from hearth.execution.ids import new_invocation_id
from hearth.execution.model import FINAL_JOB_STATUSES
from hearth.mediagen import compose, podcast, video
from hearth.observation.telemetry import get_current_traceparent, trace_span
from hearth.schemas.validate import validate

TERMINAL = set(FINAL_JOB_STATUSES)
OUTPUT_DIR = Path(os.environ.get("IMAGEGEN_OUTPUT_ROOT", r"E:\omen\imagegen\data\outputs"))
COMFY_INPUT_DIR = Path(os.environ.get(
    "COMFY_INPUT_DIR", r"E:\Comfy-Desktop\ComfyUI-Installs\OMEN\ComfyUI\input"
))


class MediaGenerationSubsystem:
    """One-worker parent scheduler; expensive child GPU work stays in image.generate."""

    def __init__(self, *, service, autostart: bool = True) -> None:
        self._service = service
        self._queue: queue.Queue[str] = queue.Queue(maxsize=32)
        self._stop = threading.Event()
        self._cancel: dict[str, threading.Event] = {}
        self._children: dict[str, set[str]] = {}
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        if autostart:
            self.start()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="hearth-mediagen", daemon=True
        )
        self._thread.start()

    def close(self, *, wait: bool = True) -> None:
        self._stop.set()
        with self._lock:
            for signal in self._cancel.values():
                signal.set()
            children = [child for rows in self._children.values() for child in rows]
        image = getattr(self._service, "_image_dispatcher", None)
        if image is not None:
            for child_id in children:
                state = self._service.ledger.get_job(child_id)
                if state and state.get("status") not in TERMINAL:
                    image.cancel(child_id, reason="MediaGen subsystem is shutting down")
        if self._thread is not None and wait:
            self._thread.join(timeout=10)
        self._thread = None

    def enqueue(self, job_id: str) -> None:
        try:
            self._queue.put_nowait(job_id)
        except queue.Full as exc:
            raise RuntimeError("MediaGen queue is full") from exc

    def cancel(self, job_id: str, *, reason: str) -> dict:
        state = self._service.ledger.get_job(job_id)
        if state is None:
            raise ValueError("unknown job_id: " + job_id)
        if state.get("status") in TERMINAL:
            return {"job_id": job_id, "status": state["status"], "already_terminal": True}
        with self._lock:
            signal = self._cancel.setdefault(job_id, threading.Event())
            signal.set()
            children = list(self._children.get(job_id, set()))
        self._service.cancel(job_id, reason=reason)
        image = getattr(self._service, "_image_dispatcher", None)
        for child_id in children:
            child = self._service.ledger.get_job(child_id)
            if child and child.get("status") not in TERMINAL and image is not None:
                image.cancel(child_id, reason="parent cancelled: " + reason)
        state = self._service.ledger.get_job(job_id) or state
        if job_id not in self._children and state.get("status") not in TERMINAL:
            self._service._append("job.cancelled", state, reason=reason)
            state = self._service.ledger.get_job(job_id) or state
        return {"job_id": job_id, "status": state["status"], "already_terminal": False}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._run_job(job_id)
            except Exception:
                # _run_job converts every workload failure into ledger state.
                pass
            finally:
                self._queue.task_done()

    def _run_job(self, job_id: str) -> None:
        state = self._service.ledger.get_job(job_id)
        if state is None or state.get("status") in TERMINAL:
            return
        if state.get("status") == "cancellation_requested":
            self._service._append("job.cancelled", state, reason="cancelled before MediaGen dispatch")
            return
        metadata = (state.get("desired") or {}).get("input_artifact")
        if not isinstance(metadata, dict):
            self._service._append("job.failed", state, reason="MediaGen input artifact is missing")
            return
        spec = json.loads(self._service.artifacts.read(metadata).decode("utf-8"))
        invocation_id = new_invocation_id()
        observed = {"backend": "hearth-mediagen", "model": state.get("operation")}
        self._service._append("job.dispatched", state, observed=observed)
        state = self._service.ledger.get_job(job_id) or state
        self._service._append(
            "invocation.started", state, invocation_id=invocation_id, observed=observed
        )
        state = self._service.ledger.get_job(job_id) or state
        self._service._append("job.running", state)
        signal = self._cancel.setdefault(job_id, threading.Event())
        with self._lock:
            self._children[job_id] = set()
        span_name = "hearth.mediagen.dag" if state["operation"] == "media.pipeline" else (
            "hearth.mediagen.podcast" if state["operation"] == "media.podcast"
            else "hearth.mediagen.animate"
        )
        started = time.monotonic()
        try:
            with trace_span(
                span_name, parent_traceparent=spec.get("traceparent"),
                attributes={"job.id": job_id, "operation": state["operation"]},
            ):
                with tempfile.TemporaryDirectory(
                    prefix=job_id + "-", dir=str(self._work_root())
                ) as temporary:
                    work = Path(temporary)
                    if state["operation"] == "media.podcast":
                        result = self._run_podcast(job_id, invocation_id, spec, work, signal)
                    elif state["operation"] == "media.animate":
                        result = self._run_animation(job_id, invocation_id, spec, work, signal)
                    else:
                        result = self._run_pipeline(job_id, invocation_id, spec, work, signal)
            if signal.is_set():
                raise InterruptedError("MediaGen job was cancelled")
            state = self._service.ledger.get_job(job_id) or state
            final_observed = {
                **observed, **result, "duration_ms": int((time.monotonic() - started) * 1000)
            }
            self._service._append(
                "invocation.succeeded", state, invocation_id=invocation_id,
                observed=final_observed,
            )
            state = self._service.ledger.get_job(job_id) or state
            self._service._append("job.succeeded", state, observed=final_observed)
        except InterruptedError as exc:
            state = self._service.ledger.get_job(job_id) or state
            self._service._append(
                "invocation.cancelled", state, invocation_id=invocation_id, reason=str(exc)
            )
            state = self._service.ledger.get_job(job_id) or state
            self._service._append("job.cancelled", state, reason=str(exc))
        except Exception as exc:
            state = self._service.ledger.get_job(job_id) or state
            self._service._append(
                "invocation.failed", state, invocation_id=invocation_id, reason=str(exc)[:2000]
            )
            state = self._service.ledger.get_job(job_id) or state
            self._service._append("job.failed", state, reason=str(exc)[:2000])
        finally:
            with self._lock:
                self._children.pop(job_id, None)
                self._cancel.pop(job_id, None)

    @staticmethod
    def _work_root() -> Path:
        root = Path(__file__).resolve().parents[1] / "var" / "mediagen"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _record(self, job_id: str, invocation_id: str, content: bytes | str,
                *, media_type: str, filename: str, role: str) -> dict:
        artifact = self._service.artifacts.put(
            content, media_type=media_type, filename=filename
        )
        state = self._service.ledger.get_job(job_id)
        assert state is not None
        self._service._append(
            "artifact.recorded", state, invocation_id=invocation_id,
            artifacts=[{**artifact, "role": role}],
        )
        return artifact

    def _checkpoint(self, job_id: str, invocation_id: str, checkpoint: dict) -> None:
        self._record(
            job_id, invocation_id,
            json.dumps(checkpoint, sort_keys=True, separators=(",", ":")),
            media_type="application/json; charset=utf-8",
            filename=job_id + "-checkpoint.json", role="checkpoint",
        )

    def _latest_checkpoint(self, state: dict) -> dict:
        for metadata in reversed(state.get("artifacts") or []):
            if metadata.get("role") == "checkpoint":
                try:
                    value = json.loads(self._service.artifacts.read(metadata).decode("utf-8"))
                    return value if isinstance(value, dict) else {}
                except Exception:
                    return {}
        return {}

    def _artifact_bytes(self, artifact_id: str) -> bytes:
        metadata = self._service.ledger.get_artifact(artifact_id)
        if metadata is None:
            raise RuntimeError("checkpoint references a missing artifact: " + artifact_id)
        return self._service.artifacts.read(metadata)

    @staticmethod
    def _check_cancel(signal: threading.Event) -> None:
        if signal.is_set():
            raise InterruptedError("MediaGen job was cancelled")

    @staticmethod
    def _trace_id() -> Optional[str]:
        traceparent = get_current_traceparent()
        if traceparent:
            parts = traceparent.split("-")
            if len(parts) >= 2:
                return parts[1]
        return None

    def _wait_for_arcserve(self, signal: threading.Event) -> None:
        """Wait for the LLM tenancy instead of failing a durable job on contention."""
        image = getattr(self._service, "_image_dispatcher", None)
        if image is None:
            raise RuntimeError("MediaGen requires the image-generation subsystem")
        deadline = time.monotonic() + 40 * 60
        while time.monotonic() < deadline:
            self._check_cancel(signal)
            current = image.session.status()["session"]
            if not current.get("active") and current.get("state") == "llm":
                if image.session.verify_arcserve():
                    return
            if current.get("state") == "faulted":
                raise RuntimeError(
                    "ArcServe tenancy is faulted: " + str(current.get("reason"))
                )
            time.sleep(2)
        raise TimeoutError("ArcServe was unavailable for MediaGen contract generation")

    def _publish(self, source: Path, filename: str) -> Path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        destination = OUTPUT_DIR / filename
        temporary = destination.with_name("." + destination.name + ".tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
        return destination

    def _media_contract(self, *, artifact: dict, media_type: str, source_schema: str,
                        details: dict) -> dict:
        contract = {
            "schema": "mediagen.media-artifact.v1", "version": "1.0.0",
            "artifact_id": artifact["artifact_id"], "media_type": media_type,
            "sha256": artifact["sha256"], "file_size_bytes": artifact["size"],
            "source_contract_schema": source_schema, **details,
        }
        trace_id = self._trace_id()
        if trace_id:
            contract["trace_id"] = trace_id
        validate(contract, schema_id="mediagen.media-artifact.v1")
        return contract

    def _run_podcast(self, job_id: str, invocation_id: str, spec: dict,
                     work: Path, signal: threading.Event) -> dict:
        self._check_cancel(signal)
        state = self._service.ledger.get_job(job_id) or {}
        checkpoint = self._latest_checkpoint(state) or {
            "progress": {}, "artifacts": {}, "child_job_ids": [], "warnings": []
        }
        artifacts = checkpoint.setdefault("artifacts", {})
        warnings = checkpoint.setdefault("warnings", [])
        if artifacts.get("script"):
            script = json.loads(self._artifact_bytes(artifacts["script"]).decode("utf-8"))
        else:
            checkpoint["progress"] = {"stage": "waiting_for_llm"}
            self._checkpoint(job_id, invocation_id, checkpoint)
            self._wait_for_arcserve(signal)
            script = podcast.generate_podcast_script(spec["document_text"])
            if spec.get("title"):
                script["title"] = spec["title"]
                validate(script, schema_id="mediagen.podcast-script.v1")
            item = self._record(
                job_id, invocation_id, json.dumps(script, indent=2),
                media_type="application/json; charset=utf-8",
                filename=job_id + "-podcast-script.json", role="contract",
            )
            artifacts["script"] = item["artifact_id"]
            checkpoint["progress"] = {"stage": "script_complete"}
            self._checkpoint(job_id, invocation_id, checkpoint)
        self._check_cancel(signal)
        wav = work / (job_id + ".wav")
        audio_details = podcast.synthesize_podcast(
            script, wav, profile_name=spec.get("voice_profile")
        )
        # voice_warnings describes the synthesis decision, not the artifact, so it must be
        # drained here: MediaArtifact.v1 is additionalProperties:false and _media_contract
        # spreads **details straight into the validated contract.
        for message in audio_details.pop("voice_warnings", []):
            warnings.append(message)
            checkpoint["degraded"] = True
        published = self._publish(wav, "podcast_" + job_id + ".wav")
        audio = self._record(
            job_id, invocation_id, published.read_bytes(), media_type="audio/wav",
            filename=published.name, role="output",
        )
        contract = self._media_contract(
            artifact=audio, media_type="audio/wav",
            source_schema="mediagen.podcast-script.v1",
            details={**audio_details, "codec": "pcm_f32le"},
        )
        result = self._record(
            job_id, invocation_id, json.dumps(contract, indent=2),
            media_type="application/json; charset=utf-8",
            filename=job_id + "-result.json", role="result",
        )
        checkpoint["progress"] = {"stage": "completed"}
        checkpoint["artifacts"].update(audio=audio["artifact_id"], result=result["artifact_id"])
        self._checkpoint(job_id, invocation_id, checkpoint)
        return {
            "result_artifact_id": result["artifact_id"],
            "degraded": bool(checkpoint.get("degraded")), "warnings": warnings,
        }

    def _session_acquire(self, job_id: str, signal: threading.Event) -> Optional[str]:
        image = getattr(self._service, "_image_dispatcher", None)
        if image is None:
            raise RuntimeError("MediaGen requires the image-generation subsystem")
        session = image.session
        current = session.status()["session"]
        existing = bool(current.get("active"))
        owned_id = None
        if not existing:
            result = session.start(reason="MediaGen job " + job_id)
            if not result.get("ok"):
                raise RuntimeError("image session could not start: " + str(result.get("error")))
            owned_id = (result.get("session") or {}).get("session_id")
        deadline = time.monotonic() + 20 * 60
        while time.monotonic() < deadline:
            self._check_cancel(signal)
            current = session.status()["session"]
            if current.get("state") == "imagegen" and current.get("active"):
                return owned_id
            if current.get("state") == "faulted":
                raise RuntimeError("image session faulted: " + str(current.get("reason")))
            time.sleep(2)
        raise TimeoutError("image session did not become active within 20 minutes")

    def _session_restore(self, job_id: str, owned_id: Optional[str]) -> None:
        if not owned_id:
            return
        image = getattr(self._service, "_image_dispatcher", None)
        if image is None:
            raise RuntimeError("image subsystem disappeared before ArcServe restore")
        session = image.session
        current = session.status()["session"]
        if current.get("session_id") == owned_id and current.get("active"):
            result = session.stop(reason="MediaGen job completed: " + job_id)
            if not result.get("ok"):
                raise RuntimeError("ArcServe restore could not start: " + str(result.get("error")))
        deadline = time.monotonic() + 10 * 60
        while time.monotonic() < deadline:
            current = session.status()["session"]
            if not current.get("active") and current.get("state") == "llm":
                if not session.verify_arcserve():
                    raise RuntimeError("ArcServe release completed but warm probe failed")
                return
            if current.get("session_id") not in {None, owned_id} and current.get("active"):
                return
            time.sleep(2)
        raise TimeoutError("ArcServe was not restored within 10 minutes")

    def _child_image(self, parent_id: str, stage: str, scene_id: str,
                     arguments: dict, signal: threading.Event) -> dict:
        self._check_cancel(signal)
        parent = self._service.ledger.get_job(parent_id)
        assert parent is not None
        child = self._service.submit(
            operation_name="image.generate", arguments=arguments,
            principal=parent["principal"],
            source={"transport": "internal", "adapter": "hearth.mediagen",
                    "parent_job_id": parent_id, "stage": stage, "scene_id": scene_id},
            policy={"deadline_s": 7200},
            idempotency_key=f"mediagen:{parent_id}:{stage}:{scene_id}",
        )
        child_id = child["job_id"]
        with self._lock:
            self._children.setdefault(parent_id, set()).add(child_id)
        deadline = time.monotonic() + 7210
        while child.get("status") not in TERMINAL and time.monotonic() < deadline:
            if signal.is_set():
                image = getattr(self._service, "_image_dispatcher", None)
                if image is not None:
                    image.cancel(child_id, reason="parent MediaGen job cancelled")
                raise InterruptedError("MediaGen job was cancelled")
            self._service.watch(
                job_id=child_id, after_sequence=int(child.get("last_sequence", 0)),
                wait_seconds=2,
            )
            child = self._service.ledger.get_job(child_id) or child
        if child.get("status") not in TERMINAL:
            image = getattr(self._service, "_image_dispatcher", None)
            if image is not None:
                image.cancel(child_id, reason="MediaGen child wait deadline exceeded")
            raise TimeoutError("child image job exceeded its deadline")
        return child

    @staticmethod
    def _output_artifact(state: dict, media_prefix: str) -> Optional[dict]:
        return next((item for item in state.get("artifacts") or []
                     if item.get("role") == "output" and
                     str(item.get("media_type", "")).startswith(media_prefix)), None)

    def _stage_image(self, job_id: str, scene_id: str, content: bytes, media_type: str) -> Path:
        COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
        suffix = ".png" if media_type == "image/png" else ".webp"
        destination = COMFY_INPUT_DIR / f"{job_id}-{scene_id}{suffix}"
        temporary = destination.with_name("." + destination.name + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
        return destination

    @staticmethod
    def _seed(parent_id: str, stage: str, scene_id: str) -> int:
        digest = hashlib.sha256(f"{parent_id}:{stage}:{scene_id}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % (2**63 - 1) or 1

    def _animate_bytes(self, parent_id: str, invocation_id: str, scene_id: str,
                       content: bytes, media_type: str, prompt: str, target_lane: str,
                       signal: threading.Event) -> tuple[dict, dict]:
        from hearth.toolsurface.image_generate import _workflow_registry
        workflow = next((item for item in _workflow_registry().get("workflows", [])
                         if item.get("id") == "wan2-i2v" and item.get("enabled") is True), None)
        if workflow is None:
            raise RuntimeError("wan2-i2v is not registered or enabled")
        allowed_lanes = workflow.get("allowed_lane_ids") or []
        if target_lane != "any" and target_lane not in allowed_lanes:
            raise RuntimeError("target lane is quarantined for wan2-i2v: " + target_lane)
        staged = self._stage_image(parent_id, scene_id, content, media_type)
        try:
            child = self._child_image(parent_id, "animate", scene_id, {
                "workflow_id": "wan2-i2v",
                "parameters": {"input_image": staged.name, "prompt": prompt,
                               "seed": self._seed(parent_id, "animate", scene_id)},
                "strategy": "single", "priority": "normal", "target_lane": target_lane,
            }, signal)
            if child.get("status") != "succeeded":
                raise RuntimeError(child.get("reason") or "Wan child job failed")
            output = self._output_artifact(child, "video/")
            if output is None:
                raise RuntimeError("Wan child completed without a video artifact")
            clip = self._artifact_bytes(output["artifact_id"])
            local = self._work_root() / (parent_id + "-verify.mp4")
            local.write_bytes(clip)
            details = compose.verify_wan_clip(local)
            local.unlink(missing_ok=True)
            artifact = self._record(
                parent_id, invocation_id, clip, media_type="video/mp4",
                filename=f"{parent_id}-{scene_id}.mp4", role="intermediate",
            )
            return artifact, details
        finally:
            staged.unlink(missing_ok=True)

    def _run_animation(self, job_id: str, invocation_id: str, spec: dict,
                       work: Path, signal: threading.Event) -> dict:
        owned = None
        try:
            owned = self._session_acquire(job_id, signal)
            content = base64.b64decode(spec["source_image_b64"], validate=True)
            clip, details = self._animate_bytes(
                job_id, invocation_id, "scene_001", content, spec["source_media_type"],
                spec["motion_prompt"], spec.get("target_lane", "any"), signal,
            )
            published = self._publish_bytes(
                self._artifact_bytes(clip["artifact_id"]), "animation_" + job_id + ".mp4"
            )
            output = self._record(
                job_id, invocation_id, published.read_bytes(), media_type="video/mp4",
                filename=published.name, role="output",
            )
            contract = self._media_contract(
                artifact=output, media_type="video/mp4",
                source_schema="mediagen.visual-storyboard.v1", details=details,
            )
            result = self._record(
                job_id, invocation_id, json.dumps(contract, indent=2),
                media_type="application/json; charset=utf-8",
                filename=job_id + "-result.json", role="result",
            )
            self._checkpoint(job_id, invocation_id, {
                "progress": {"stage": "animation_complete", "completed_scenes": 1,
                             "total_scenes": 1},
                "child_job_ids": sorted(self._children.get(job_id, set())),
                "artifacts": {"video": output["artifact_id"], "result": result["artifact_id"]},
                "warnings": [], "degraded": False,
            })
            return {"result_artifact_id": result["artifact_id"],
                    "degraded": False, "warnings": []}
        finally:
            self._session_restore(job_id, owned)

    def _publish_bytes(self, content: bytes, filename: str) -> Path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        destination = OUTPUT_DIR / filename
        temporary = destination.with_name("." + destination.name + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
        return destination

    def _run_pipeline(self, job_id: str, invocation_id: str, spec: dict,
                      work: Path, signal: threading.Event) -> dict:
        state = self._service.ledger.get_job(job_id) or {}
        checkpoint = self._latest_checkpoint(state) or {
            "progress": {}, "artifacts": {"stills": {}, "animations": {}},
            "child_job_ids": [], "warnings": [], "degraded": False,
        }
        artifacts = checkpoint.setdefault("artifacts", {})
        artifacts.setdefault("stills", {})
        artifacts.setdefault("animations", {})
        warnings = checkpoint.setdefault("warnings", [])

        if not artifacts.get("script") or not artifacts.get("storyboard"):
            checkpoint["progress"] = {"stage": "waiting_for_llm"}
            self._checkpoint(job_id, invocation_id, checkpoint)
            self._wait_for_arcserve(signal)

        if artifacts.get("script"):
            script = json.loads(self._artifact_bytes(artifacts["script"]).decode("utf-8"))
        else:
            script = podcast.generate_podcast_script(spec["document_text"])
            if spec.get("title"):
                script["title"] = spec["title"]
                validate(script, schema_id="mediagen.podcast-script.v1")
            item = self._record(job_id, invocation_id, json.dumps(script, indent=2),
                media_type="application/json; charset=utf-8",
                filename=job_id + "-podcast-script.json", role="contract")
            artifacts["script"] = item["artifact_id"]
            checkpoint["progress"] = {"stage": "podcast_contract_complete"}
            self._checkpoint(job_id, invocation_id, checkpoint)
        self._check_cancel(signal)

        if artifacts.get("storyboard"):
            storyboard = json.loads(self._artifact_bytes(artifacts["storyboard"]).decode("utf-8"))
        else:
            storyboard = video.generate_visual_storyboard(
                spec["document_text"], scene_count=spec.get("scene_count", 4)
            )
            item = self._record(job_id, invocation_id, json.dumps(storyboard, indent=2),
                media_type="application/json; charset=utf-8",
                filename=job_id + "-storyboard.json", role="contract")
            artifacts["storyboard"] = item["artifact_id"]
            checkpoint["progress"] = {"stage": "storyboard_contract_complete"}
            self._checkpoint(job_id, invocation_id, checkpoint)
        self._check_cancel(signal)

        audio_path = work / "podcast.wav"
        audio_future = None
        audio_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hearth-mediagen-tts")
        if artifacts.get("audio"):
            audio_path.write_bytes(self._artifact_bytes(artifacts["audio"]))
        else:
            # Captured on THIS thread, then run inside it on the audio pool thread.
            # ThreadPoolExecutor does not propagate contextvars, so without this the
            # DispatchIdentity (ADR-0027 capability evidence) and the
            # _caller_roots/_caller_repos path-containment vars silently vanish for
            # every media.pipeline TTS call.
            context = contextvars.copy_context()
            audio_future = audio_pool.submit(
                context.run, podcast.synthesize_podcast, script, audio_path,
                profile_name=spec.get("voice_profile"),
            )

        owned = None
        still_rows: list[tuple[dict, dict]] = []
        try:
            owned = self._session_acquire(job_id, signal)
            checkpoint["session_id"] = owned
            scenes = storyboard["scenes"]
            for index, scene in enumerate(scenes, start=1):
                self._check_cancel(signal)
                scene_id = scene["scene_id"]
                existing = artifacts["stills"].get(scene_id)
                if existing:
                    existing_artifact = self._service.ledger.get_artifact(existing)
                    if existing_artifact is not None:
                        still_rows.append((scene, existing_artifact))
                        continue
                    artifacts["stills"].pop(scene_id, None)
                    warnings.append(f"{scene_id}: missing still checkpoint was regenerated")
                child = self._child_image(job_id, "still", scene_id, {
                    "workflow_id": "z-image-turbo",
                    "parameters": {"prompt": scene["still_prompt"], "width": 1280,
                                   "height": 720, "steps": 8,
                                   "seed": self._seed(job_id, "still", scene_id)},
                    "strategy": "single", "priority": "normal", "target_lane": "any",
                }, signal)
                checkpoint["child_job_ids"] = sorted(self._children.get(job_id, set()))
                output = self._output_artifact(child, "image/") if child.get("status") == "succeeded" else None
                if output is None:
                    warnings.append(f"{scene_id}: still generation failed")
                    checkpoint["degraded"] = True
                else:
                    content = self._artifact_bytes(output["artifact_id"])
                    parent_artifact = self._record(
                        job_id, invocation_id, content, media_type=output["media_type"],
                        filename=f"{job_id}-{scene_id}{Path(output.get('filename', '.png')).suffix}",
                        role="intermediate",
                    )
                    artifacts["stills"][scene_id] = parent_artifact["artifact_id"]
                    still_rows.append((scene, parent_artifact))
                checkpoint["progress"] = {
                    "stage": "stills", "completed_scenes": index, "total_scenes": len(scenes)
                }
                self._checkpoint(job_id, invocation_id, checkpoint)

            if audio_future is not None:
                audio_details = audio_future.result()
                # Drain before the details are checkpointed or spread into the
                # media-artifact contract -- see the matching note in _run_podcast.
                for message in audio_details.pop("voice_warnings", []):
                    warnings.append(message)
                    checkpoint["degraded"] = True
                published_audio = self._publish(audio_path, "podcast_" + job_id + ".wav")
                audio_artifact = self._record(
                    job_id, invocation_id, published_audio.read_bytes(), media_type="audio/wav",
                    filename=published_audio.name, role="output",
                )
                artifacts["audio"] = audio_artifact["artifact_id"]
                artifacts["audio_details"] = audio_details
                self._checkpoint(job_id, invocation_id, checkpoint)
            else:
                audio_details = artifacts.get("audio_details") or {
                    "duration_seconds": compose._duration(compose.probe(audio_path)),
                    "sample_rate": 24000, "channels": 1,
                }

            if not still_rows:
                raise RuntimeError("all storyboard still generations failed")

            for index, (scene, still_artifact) in enumerate(still_rows, start=1):
                self._check_cancel(signal)
                scene_id = scene["scene_id"]
                existing = artifacts["animations"].get(scene_id)
                if existing:
                    continue
                try:
                    clip, _ = self._animate_bytes(
                        job_id, invocation_id, scene_id,
                        self._artifact_bytes(still_artifact["artifact_id"]),
                        still_artifact["media_type"], scene["motion_prompt"], "any", signal,
                    )
                    artifacts["animations"][scene_id] = clip["artifact_id"]
                except InterruptedError:
                    raise
                except Exception as exc:
                    warnings.append(f"{scene_id}: animation failed; static fallback used ({str(exc)[:300]})")
                    checkpoint["degraded"] = True
                checkpoint["child_job_ids"] = sorted(self._children.get(job_id, set()))
                checkpoint["progress"] = {
                    "stage": "animations", "completed_scenes": index,
                    "total_scenes": len(still_rows),
                }
                self._checkpoint(job_id, invocation_id, checkpoint)
        finally:
            try:
                self._session_restore(job_id, owned)
            finally:
                audio_pool.shutdown(wait=True, cancel_futures=False)

        segments = []
        for scene, still_artifact in still_rows:
            scene_id = scene["scene_id"]
            animation_id = artifacts["animations"].get(scene_id)
            source_id = animation_id or still_artifact["artifact_id"]
            source_suffix = ".mp4" if animation_id else (
                ".png" if still_artifact["media_type"] == "image/png" else ".webp"
            )
            source = work / (scene_id + "-source" + source_suffix)
            source.write_bytes(self._artifact_bytes(source_id))
            segment = work / (scene_id + "-normalized.mp4")
            compose.normalize_scene(source, segment, still=animation_id is None)
            segments.append(segment)
        final = work / "final.mp4"
        details = compose.compose_full_audio(segments, audio_path, final, work)
        published = self._publish(final, "mediagen_" + job_id + ".mp4")
        output = self._record(
            job_id, invocation_id, published.read_bytes(), media_type="video/mp4",
            filename=published.name, role="output",
        )
        media_contract = self._media_contract(
            artifact=output, media_type="video/mp4",
            source_schema="mediagen.visual-storyboard.v1", details=details,
        )
        result = self._record(
            job_id, invocation_id, json.dumps(media_contract, indent=2),
            media_type="application/json; charset=utf-8",
            filename=job_id + "-result.json", role="result",
        )
        artifacts.update(video=output["artifact_id"], result=result["artifact_id"])
        checkpoint["progress"] = {
            "stage": "completed", "completed_scenes": len(still_rows),
            "total_scenes": len(storyboard["scenes"]),
        }
        self._checkpoint(job_id, invocation_id, checkpoint)
        return {"result_artifact_id": result["artifact_id"],
                "degraded": bool(checkpoint.get("degraded")), "warnings": warnings}
