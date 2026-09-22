"""Thin review-gated coordinator over the canonical execution service.

The execution ledger owns requests, jobs, invocations and model artifacts.  The
operator history owns workflow facts.  This module only freezes Git inputs,
validates the model's candidate, and projects those records into a manifest.
It intentionally has no apply, commit, merge, or push method.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import tomllib
import urllib.request
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Optional

from hearth.execution import ExecutionService
from hearth.operator import canonical, envelope, history, paths
from hearth.toolsurface.backends import Backend, load_pool

CANDIDATE_SCHEMA = "local-work-candidate.v1"
MANIFEST_SCHEMA = "local-work-manifest.v1"
TEMPLATE_VERSION = "local-work-prompts.v1"
ROUTE_PROFILE_VERSION = "local-work-routes.v1"
KINDS = frozenset({"markdown", "json", "whole_file", "unified_diff"})
LANES = frozenset({"auto", "fast", "deep"})
FINAL = frozenset({"accepted", "rejected", "superseded", "failed"})
VISION_FAMILIES = frozenset({"vision", "document_ocr", "image_analysis"})
_DIFF_PATH = re.compile(r"^(?:---|\+\+\+)\s+(?:a/|b/)?([^\t\r\n]+)", re.MULTILINE)
TokenCounter = Callable[[Backend, str, str], int]


class LocalWorkError(RuntimeError):
    pass


def _digest(value: bytes | str) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _git(repo: Path, *args: str, input_text: str | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], input=input_text, text=True,
        capture_output=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if completed.returncode:
        raise LocalWorkError((completed.stderr or completed.stdout).strip())
    return completed.stdout


def _safe_relative(value: str) -> str:
    text = value.replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts or text.startswith("./"):
        raise LocalWorkError(f"repository path must be normalized and relative: {value!r}")
    return path.as_posix()


class LocalWorkService:
    def __init__(self, execution: ExecutionService, *, root: Path | str | None = None,
                 token_counter: TokenCounter | None = None) -> None:
        self.execution = execution
        self.root = Path(root).resolve() if root else paths.operator_home()
        self.token_counter = token_counter or self._server_token_count
        self._lock = threading.RLock()

    def _run_dir(self, work_id: str) -> Path:
        if not re.fullmatch(r"work_[a-f0-9]{32}", work_id):
            raise LocalWorkError("invalid work_id")
        return self.root / "runs" / "operator" / work_id

    def _manifest_path(self, work_id: str) -> Path:
        return self._run_dir(work_id) / "work-manifest.json"

    def _read(self, work_id: str) -> dict[str, Any]:
        target = self._manifest_path(work_id)
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LocalWorkError(f"unknown local work: {work_id}") from exc

    def _write(self, manifest: Mapping[str, Any]) -> None:
        target = self._manifest_path(str(manifest["work_id"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(dict(manifest), indent=2, sort_keys=True) + "\n"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(encoded, encoding="utf-8", newline="")
        os.replace(temporary, target)

    def _event(self, manifest: Mapping[str, Any], event_type: str,
               payload: Mapping[str, Any], refs: Mapping[str, Any] | None = None) -> dict:
        # Payloads contain identities, counts and digests only; source and prompt
        # bytes remain in the execution artifact.
        return history.append(event_type, dict(payload), refs=dict(refs or {}),
                              run_id=str(manifest["work_id"]),
                              envelope_id=str(manifest["envelope_id"]))

    @staticmethod
    def _resolve_commit(repo: Path, base_commit: str) -> str:
        resolved = _git(repo, "rev-parse", "--verify", f"{base_commit}^{{commit}}").strip()
        if not re.fullmatch(r"[a-f0-9]{40}", resolved):
            raise LocalWorkError("base_commit did not resolve to a commit")
        return resolved

    @staticmethod
    def _source_pack(repo: Path, base: str, files: list[str]) -> tuple[str, list[dict[str, Any]]]:
        chunks: list[str] = []
        metadata: list[dict[str, Any]] = []
        for raw in files:
            name = _safe_relative(raw)
            try:
                content = _git(repo, "show", f"{base}:{name}")
            except LocalWorkError as exc:
                raise LocalWorkError(f"cannot read {name!r} at {base}: {exc}") from exc
            encoded = content.encode("utf-8")
            metadata.append({"path": name, "bytes": len(encoded), "sha256": _digest(encoded),
                             "lines": len(content.splitlines())})
            chunks.append(f"--- SOURCE {name} @ {base} ---\n{content}")
        return "\n".join(chunks), metadata

    @staticmethod
    def _lane(lane: str, evidence_tokens: int, task_family: str | None) -> str:
        if lane not in LANES:
            raise LocalWorkError(f"lane must be one of {sorted(LANES)}")
        if task_family in VISION_FAMILIES:
            raise LocalWorkError("vision task families are unsupported on local-work lanes")
        if lane != "auto":
            return lane
        floor = 4096 if task_family == "quote_retrieval" else 8192
        return "deep" if evidence_tokens >= floor else "fast"

    @staticmethod
    def _route_profile() -> tuple[dict[str, str], str]:
        target = Path(__file__).resolve().parents[1] / "etc" / "local-work-routes.toml"
        raw = target.read_bytes()
        document = tomllib.loads(raw.decode("utf-8"))
        lanes = document.get("lane") or {}
        resolved = {name: str(value["backend"]) for name, value in lanes.items()}
        if set(resolved) != {"fast", "deep"}:
            raise LocalWorkError("local-work route profile requires exactly fast and deep lanes")
        return resolved, _digest(raw)

    @staticmethod
    def _template(kind: str) -> tuple[str, str]:
        mapping = {"unified_diff": "local_work_patch_v1.txt", "whole_file": "local_work_whole_file_v1.txt",
                   "json": "local_work_json_v1.txt", "markdown": "local_work_markdown_v1.txt"}
        target = Path(__file__).resolve().parents[1] / "prompts" / mapping[kind]
        text = target.read_text(encoding="utf-8")
        return text, _digest(text)

    @staticmethod
    def _server_token_count(provider: Backend, model: str, prompt: str) -> int:
        headers = {"Content-Type": "application/json"}
        if provider.auth_env:
            token = os.environ.get(provider.auth_env)
            if not token:
                raise LocalWorkError(f"missing authentication for tokenizer: ${provider.auth_env}")
            headers["Authorization"] = f"Bearer {token}"

        def post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
            request = urllib.request.Request(
                provider.endpoint.rstrip("/") + path,
                data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                raise LocalWorkError(f"exact tokenizer endpoint refused: {exc}") from exc

        rendered = post("/apply-template", {"model": model, "messages": [{"role": "user", "content": prompt}]})
        text = rendered.get("prompt") or rendered.get("content")
        if not isinstance(text, str):
            raise LocalWorkError("exact tokenizer endpoint returned no rendered prompt")
        counted = post("/tokenize", {"model": model, "content": text, "add_special": False})
        tokens = counted.get("tokens")
        count = len(tokens) if isinstance(tokens, list) else counted.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise LocalWorkError("exact tokenizer endpoint returned no token count")
        return count

    def submit(self, *, intent: str, acceptance_criteria: list[str], repo: str,
               base_commit: str, files: list[str], artifact_kind: str,
               target_path: str | None, lane: str, task_family: str | None,
               deadline_s: int, max_tokens: int | None, receipt_id: str | None,
               idempotency_key: str | None, caller_id: str) -> dict[str, Any]:
        if artifact_kind not in KINDS:
            raise LocalWorkError(f"artifact_kind must be one of {sorted(KINDS)}")
        if not acceptance_criteria or not all(isinstance(x, str) and x.strip() for x in acceptance_criteria):
            raise LocalWorkError("acceptance_criteria must contain non-empty strings")
        if not files:
            raise LocalWorkError("files must not be empty")
        repo_path = Path(repo).resolve()
        if not (repo_path / ".git").exists() and not _git(repo_path, "rev-parse", "--git-dir").strip():
            raise LocalWorkError("repo must be a Git working tree")
        base = self._resolve_commit(repo_path, base_commit)
        declared = [_safe_relative(item) for item in files]
        target = _safe_relative(target_path) if target_path else None
        source_pack, source_meta = self._source_pack(repo_path, base, declared)
        template, template_hash = self._template(artifact_kind)
        # The depth floor is token-based, not a byte heuristic.  For auto we
        # ask the currently declared fast server to count the evidence alone;
        # the selected server then counts the complete templated request below.
        routes, route_hash = self._route_profile()
        fast_provider = load_pool().by_name(routes["fast"])
        if fast_provider is None or not fast_provider.models:
            raise LocalWorkError("local fast lane is unavailable")
        evidence_tokens = self.token_counter(
            fast_provider, fast_provider.models[0], source_pack) if lane == "auto" else 0
        selected_lane = self._lane(lane, evidence_tokens, task_family)
        backend_name = routes[selected_lane]
        provider = load_pool().by_name(backend_name)
        if provider is None or provider.retired:
            raise LocalWorkError(f"local lane {selected_lane!r} is unavailable: {backend_name}")
        model = provider.models[0] if provider.models else ""
        request_doc = {"intent": intent, "acceptance_criteria": acceptance_criteria,
                       "artifact_kind": artifact_kind, "target_path": target,
                       "declared_paths": declared, "source_pack": source_pack}
        prompt = template + "\n\nREQUEST\n" + json.dumps(request_doc, sort_keys=True)
        input_tokens = self.token_counter(provider, model, prompt)
        output_reserve = max_tokens or int(provider.settings.get("max_tokens") or 4096)
        context_tokens = int(provider.settings.get("context_tokens") or
                             (int(provider.settings.get("context_bytes") or 0) // 4))
        if context_tokens <= 0 or input_tokens + output_reserve > context_tokens:
            raise LocalWorkError(
                f"exact context refusal: {input_tokens} input + {output_reserve} output > {context_tokens}")

        work_id = (f"work_{_digest(caller_id + ':' + idempotency_key)[:32]}"
                   if idempotency_key else f"work_{uuid.uuid4().hex}")
        if idempotency_key and self._manifest_path(work_id).is_file():
            existing = self._read(work_id)
            if existing["prompt"]["digest"] != _digest(prompt):
                raise LocalWorkError("idempotency_key was already used for different local work")
            return self.reconcile(work_id)
        env = envelope.build_envelope(
            intent, acceptance_criteria,
            {"repo": str(repo_path), "base_commit": base, "paths": [], "files": declared},
            {"task_type": "engineering", "repo_size": "standard", "language": "mixed",
             "read_vs_reasoning": "balanced", "context_continuity": "session",
             "mutation_level": "none", "risk_level": "medium"},
            {"deadline_s": deadline_s, "max_attempts": 2, "max_context_tokens": context_tokens, "budget": None},
            submitted_by=caller_id, submission_source="mcp")
        manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA, "work_id": work_id, "envelope_id": env["envelope_id"],
            "status": "queued", "repo": str(repo_path), "base_commit": base,
            "source": {"digest": _digest(source_pack), "files": source_meta},
            "prompt": {"digest": _digest(prompt), "template_version": TEMPLATE_VERSION,
                       "template_sha256": template_hash, "input_tokens": input_tokens,
                       "output_reserve_tokens": output_reserve,
                       "context_tokens": context_tokens},
            "route": {"profile_version": ROUTE_PROFILE_VERSION,
                      "profile_sha256": route_hash,
                      "requested_lane": lane, "selected_lane": selected_lane,
                      "provider": backend_name, "model": model, "task_family": task_family},
            "artifact_kind": artifact_kind, "target_path": target, "declared_paths": declared,
            "criteria": acceptance_criteria, "attempts": [], "artifact": None,
            "receipt_id": receipt_id, "verdict": None,
            "caller": {"submitted_by": caller_id, "validated_by": None},
        }
        with self._lock:
            self._write(manifest)
            envelope.store_envelope(env, work_id, raw_prompt=prompt)
            state = self.execution.submit(
                operation_name="work.produce",
                arguments={"prompt": prompt, "backend": backend_name, "model": model,
                           "task_family": task_family},
                principal={"type": "hearth_caller", "id": caller_id, "authenticated": True},
                source={"transport": "mcp", "adapter": caller_id},
                policy={"max_tokens": output_reserve, "deadline_s": deadline_s},
                idempotency_key=idempotency_key,
            )
            manifest["request_id"] = state["request_id"]
            manifest["job_id"] = state["job_id"]
            manifest["attempts"].append({"number": 1, "request_id": state["request_id"],
                                         "job_id": state["job_id"], "repair": False})
            self._write(manifest)
            self._event(manifest, "step.dispatched", {"job_id": state["job_id"], "attempt": 1,
                        "provider": backend_name, "model": model}, refs={"receipt_id": receipt_id} if receipt_id else {})
        return self.reconcile(work_id)

    def _result(self, job: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
        artifact_id = job.get("result_artifact_id") or next(
            (x.get("artifact_id") for x in job.get("artifacts", []) if x.get("role") == "result"), None)
        if not artifact_id:
            raise LocalWorkError("completed job has no result artifact")
        return self.execution.read_artifact(str(artifact_id))

    @staticmethod
    def _parse_candidate(raw: bytes, expected_kind: str) -> dict[str, Any]:
        try:
            candidate = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise LocalWorkError(f"malformed local-work-candidate.v1: {exc}") from exc
        exact = {"schema", "artifact_kind", "summary", "target_path", "citations", "content"}
        if not isinstance(candidate, dict) or set(candidate) != exact:
            raise LocalWorkError("candidate must contain exactly the local-work-candidate.v1 fields")
        if candidate["schema"] != CANDIDATE_SCHEMA or candidate["artifact_kind"] != expected_kind:
            raise LocalWorkError("candidate schema or artifact_kind mismatch")
        if not isinstance(candidate["summary"], str) or not candidate["summary"].strip():
            raise LocalWorkError("candidate summary must not be empty")
        if not isinstance(candidate["citations"], list):
            raise LocalWorkError("candidate citations must be a list")
        if expected_kind != "json" and not isinstance(candidate["content"], str):
            raise LocalWorkError("candidate content must be text for this artifact kind")
        return candidate

    def _validate_candidate(self, manifest: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
        repo = Path(str(manifest["repo"]))
        base = str(manifest["base_commit"])
        declared = set(manifest["declared_paths"])
        for citation in candidate["citations"]:
            if not isinstance(citation, dict) or set(citation) != {"path", "start_line", "end_line"}:
                raise LocalWorkError("citation shape is invalid")
            path = _safe_relative(str(citation["path"]))
            if path not in declared:
                raise LocalWorkError(f"citation path was not declared: {path}")
            start, end = citation["start_line"], citation["end_line"]
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                raise LocalWorkError("citation line range is invalid")
            lines = len(_git(repo, "show", f"{base}:{path}").splitlines())
            if end > lines:
                raise LocalWorkError(f"citation exceeds {path} at pinned commit")
        if manifest["artifact_kind"] == "whole_file" and candidate["target_path"] != manifest["target_path"]:
            raise LocalWorkError("whole-file target_path mismatch")
        if manifest["artifact_kind"] == "unified_diff":
            content = str(candidate["content"])
            if "GIT binary patch" in content or "Binary files " in content:
                raise LocalWorkError("binary changes are forbidden")
            touched = {_safe_relative(item) for item in _DIFF_PATH.findall(content) if item != "/dev/null"}
            if not touched or not touched.issubset(declared):
                raise LocalWorkError(f"diff touches undeclared paths: {sorted(touched - declared)}")
            with tempfile.TemporaryDirectory(prefix="hearth-local-work-") as temp:
                clone = Path(temp) / "repo"
                subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", str(repo), str(clone)],
                               check=True, timeout=120)
                _git(clone, "checkout", "--quiet", "--detach", base)
                completed = subprocess.run(["git", "-C", str(clone), "apply", "--check", "--whitespace=error-all", "-"],
                                           input=content, text=True, capture_output=True, timeout=120)
                if completed.returncode:
                    raise LocalWorkError(f"git apply --check failed: {completed.stderr.strip()}")

    def reconcile(self, work_id: str) -> dict[str, Any]:
        with self._lock:
            manifest = self._read(work_id)
            if manifest["status"] in FINAL or manifest["status"] == "awaiting_review":
                return manifest
            job = self.execution.get_job(str(manifest["job_id"]))
            if job is None:
                manifest["status"] = "failed"
                manifest["failure"] = "execution job missing"
            elif job["status"] in {"accepted", "queued", "dispatched", "running"}:
                manifest["status"] = "running" if job["status"] in {"dispatched", "running"} else "queued"
            elif job["status"] != "succeeded":
                manifest["status"] = "failed"
                manifest["failure"] = job.get("reason") or f"execution ended {job['status']}"
                self._event(manifest, "attempt.recorded", {"job_id": job["job_id"], "ok": False,
                            "reason_sha256": _digest(manifest["failure"])})
                self._event(manifest, "outcome.final", {"status": "failed"})
            else:
                metadata, raw = self._result(job)
                try:
                    candidate = self._parse_candidate(raw, str(manifest["artifact_kind"]))
                except LocalWorkError as exc:
                    if len(manifest["attempts"]) >= 2:
                        manifest["status"] = "failed"
                        manifest["failure"] = str(exc)
                        self._event(manifest, "outcome.final", {"status": "failed", "reason_sha256": _digest(str(exc))})
                    else:
                        original = self.execution.artifacts.read(job["desired"]["input_artifact"]).decode("utf-8")
                        prior = raw.decode("utf-8", errors="replace")
                        repair = (original + "\n\nREPAIR: The response below was rejected as malformed. "
                                  "Return only a corrected object with exactly schema, artifact_kind, "
                                  "summary, target_path, citations, and content.\nREJECTED RESPONSE:\n" + prior)
                        provider = load_pool().by_name(str(manifest["route"]["provider"]))
                        if provider is None:
                            raise LocalWorkError("repair route provider disappeared")
                        repair_tokens = self.token_counter(
                            provider, str(manifest["route"]["model"]), repair)
                        if repair_tokens + int(manifest["prompt"]["output_reserve_tokens"]) > int(manifest["prompt"]["context_tokens"]):
                            manifest["status"] = "failed"
                            manifest["failure"] = "structural repair does not fit exact context"
                            self._event(manifest, "outcome.final", {"status": "failed",
                                        "reason_sha256": _digest(manifest["failure"])})
                            self._write(manifest)
                            return manifest
                        state = self.execution.submit(
                            operation_name="work.produce",
                            arguments={"prompt": repair, "backend": manifest["route"]["provider"],
                                       "model": manifest["route"]["model"]},
                            principal=job["principal"], source=job["source"],
                            policy=job["desired"]["policy"],
                            idempotency_key=f"{work_id}:structural-repair")
                        manifest["job_id"] = state["job_id"]
                        manifest["request_id"] = state["request_id"]
                        manifest["status"] = "queued"
                        manifest["attempts"].append({"number": 2, "job_id": state["job_id"],
                                                     "request_id": state["request_id"], "repair": True})
                        self._event(manifest, "attempt.recorded", {"job_id": job["job_id"], "ok": False,
                                    "structural_repair": True, "reason_sha256": _digest(str(exc))})
                        self._event(manifest, "step.dispatched", {"job_id": state["job_id"], "attempt": 2,
                                    "structural_repair": True})
                else:
                    try:
                        self._validate_candidate(manifest, candidate)
                    except LocalWorkError as exc:
                        manifest["status"] = "failed"
                        manifest["failure"] = str(exc)
                        self._event(manifest, "verification.recorded", {"passed": False,
                                    "reason_sha256": _digest(str(exc))})
                        self._event(manifest, "outcome.final", {"status": "failed"})
                    else:
                        observed = (job.get("invocations") or [{}])[-1].get("observed") or {}
                        manifest["route"].update({key: observed.get(key) for key in
                                                  ("backend", "model", "routed_by", "tokens_in", "tokens_out", "duration_ms")
                                                  if observed.get(key) is not None})
                        manifest["artifact"] = {key: metadata[key] for key in
                                                ("artifact_id", "sha256", "size", "media_type")}
                        manifest["status"] = "awaiting_review"
                        self._event(manifest, "attempt.recorded", {"job_id": job["job_id"], "ok": True})
                        self._event(manifest, "artifact.produced", {"artifact_id": metadata["artifact_id"],
                                    "sha256": metadata["sha256"], "size": metadata["size"]})
                        self._event(manifest, "verification.recorded", {"passed": True,
                                    "checks": ["schema", "citations", "declared_paths", "git_apply_check"]})
            self._write(manifest)
            return manifest

    def get(self, work_id: str) -> dict[str, Any]:
        return self.reconcile(work_id)

    def reconcile_all(self) -> int:
        base = self.root / "runs" / "operator"
        count = 0
        if not base.is_dir():
            return 0
        for target in base.glob("work_*/work-manifest.json"):
            manifest = json.loads(target.read_text(encoding="utf-8"))
            if manifest.get("status") not in FINAL and manifest.get("status") != "awaiting_review":
                self.reconcile(str(manifest["work_id"]))
                count += 1
        return count

    def watch(self, work_id: str, after_sequence: int = 0, wait_seconds: float = 0) -> dict[str, Any]:
        manifest = self.reconcile(work_id)
        events = history.read_run_history(work_id)
        selected = [row for row in events if int(row["sequence"]) > after_sequence]
        return {"events": selected, "next_sequence": selected[-1]["sequence"] if selected else after_sequence,
                "status": manifest["status"]}

    def artifact(self, work_id: str) -> dict[str, Any]:
        manifest = self.reconcile(work_id)
        if manifest["status"] not in {"awaiting_review", "accepted", "rejected", "superseded"}:
            raise LocalWorkError("candidate is not available for review")
        metadata, raw = self.execution.read_artifact(manifest["artifact"]["artifact_id"])
        if metadata["sha256"] != manifest["artifact"]["sha256"]:
            raise LocalWorkError("candidate artifact digest no longer matches manifest")
        candidate = self._parse_candidate(raw, manifest["artifact_kind"])
        self._validate_candidate(manifest, candidate)
        return {"work_id": work_id, "artifact": metadata, "candidate": candidate}

    def verdict(self, work_id: str, *, decision: str, criteria: list[dict[str, Any]],
                summary: str, evidence: list[Any], receipt_id: str | None,
                caller_id: str) -> dict[str, Any]:
        if decision not in {"accepted", "rejected", "superseded"}:
            raise LocalWorkError("decision must be accepted, rejected, or superseded")
        with self._lock:
            manifest = self.reconcile(work_id)
            if manifest["status"] != "awaiting_review":
                raise LocalWorkError("only awaiting_review work can receive a verdict")
            expected = list(manifest["criteria"])
            rows = {row.get("criterion"): row for row in criteria if isinstance(row, dict)}
            if decision == "accepted":
                missing = [criterion for criterion in expected if
                           rows.get(criterion, {}).get("status") != "passed" or
                           not rows.get(criterion, {}).get("evidence")]
                if missing:
                    raise LocalWorkError(f"accepted requires passed evidenced rows: {missing}")
            if not isinstance(summary, str) or not summary.strip() or not evidence:
                raise LocalWorkError("verdict requires summary and evidence")
            verdict = {"decision": decision, "criteria": criteria, "summary": summary,
                       "evidence": evidence, "receipt_id": receipt_id or manifest.get("receipt_id"),
                       "caller_id": caller_id}
            manifest["verdict"] = verdict
            manifest["status"] = decision
            manifest["caller"]["validated_by"] = caller_id
            self._event(manifest, "verification.recorded", {"passed": decision == "accepted",
                        "decision": decision, "criteria_count": len(criteria),
                        "evidence_count": len(evidence), "validator": caller_id},
                        refs={"receipt_id": verdict["receipt_id"]} if verdict["receipt_id"] else {})
            self._event(manifest, "outcome.final", {"status": decision, "validator": caller_id})
            self._write(manifest)
            return manifest
