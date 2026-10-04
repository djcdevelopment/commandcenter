"""Thin review-gated coordinator over the canonical execution service.

The execution ledger owns requests, jobs, invocations and model artifacts.  The
operator history owns workflow facts.  This module only freezes Git inputs,
validates the model's candidate, and projects those records into a manifest.
It intentionally has no apply, commit, merge, or push method.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.request
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Optional

from hearth.delivery import carry, contract, items, procedures, render as delivery_render, revision as revision_coverage, sourcemap
from hearth.delivery.verify import verify as verify_delivery
from fleet import environment
from hearth.execution import ExecutionService
from hearth.execution.model import FINAL_JOB_STATUSES
from hearth.execution.lab_config import active_configuration
from hearth.operator import canonical, envelope, history, paths
from hearth.toolsurface.backends import Backend, load_pool

CANDIDATE_SCHEMA = "local-work-candidate.v1"
MANIFEST_SCHEMA = "local-work-manifest.v1"
TEMPLATE_VERSION = "local-work-prompts.v1"
ROUTE_PROFILE_VERSION = "local-work-routes.v2"
DELIVERY_TOKEN_CEILING = 16384
CARRY_WORK_TOKENS = 24576   # the procedure's thinking turn: inference.deliberate's ceiling, not work.produce's
# The system line of the accepted working drafts (delivery-plan/evidence/multistep/run_multistep.py SYSTEM), unchanged.
CARRY_SYSTEM = ("You are auditing source files in order to write a short report. You work in steps inside this one "
                "conversation: you keep working notes, check them, and only then write. Say only what the source shows.")
CARRY_NOTES_HEADING = ("WORKING NOTES (written by the draft's author; they name source lines and can guide you to the "
                       "right lines; never copy a quote from them):")
# The items procedure (ADR-0058): execution admits 256 pending jobs in all (submit raises "global execution queue is full" past
# that, and the count is shared with every other caller) and runs 16 workers, each of which polls for a backend lease while
# its job waits, with the job's deadline running from the moment a worker takes it. So 2 x N jobs are never submitted at once:
# a stage keeps ITEMS_WINDOW in flight (the settle stage, as many as the settler has slots) and refills at every reconcile.
ITEMS_WINDOW = 12
ITEMS_MAX_TOKENS = 400
ITEMS_AIDS = ["item_enumerator", "reader_agreement", "line_reference", "constrained_output"]
IN_FLIGHT = frozenset({"accepted", "queued", "dispatched", "running"})
KINDS = frozenset({"markdown", "json", "whole_file", "unified_diff"})
LANES = frozenset({"auto", "fast", "deep", "tool"})   # tool: explicit only, when the route profile names it
FINAL = frozenset({"accepted", "rejected", "superseded", "failed"})
VISION_FAMILIES = frozenset({"vision", "document_ocr", "image_analysis"})
# This is deliberately narrower than Backend.settings.  A manifest needs enough
# declared serving facts to distinguish a qualified run from a later shape, but
# it must never become a dump of endpoint, credential, or operator settings.
SERVING_PROFILE_KEYS = frozenset({
    "serving_profile_version", "hardware_profile_id",
    "engine", "engine_build", "model_weight", "model_weight_sha256", "quantization",
    "device_backend", "devices", "split_mode", "tensor_split",
    "context_tokens", "max_tokens", "parallel_slots",
    "kv_cache_key_type", "kv_cache_value_type", "batch_tokens", "ubatch_tokens",
    "flash_attention", "speculative", "reasoning_budget_tokens", "reasoning_effort",
})
# Families whose routing evidence pins the quality lane regardless of evidence size.
DEEP_LANE_FAMILIES = frozenset({"code_fix", "code_review"})
_DIFF_PATH = re.compile(r"^(?:---|\+\+\+)\s+(?:a/|b/)?([^\t\r\n]+)", re.MULTILINE)
# Line-ranged string citations the deep lane writes although the schema asks for objects:
# "path:N-M", "path:N", "path (line N)", "path (lines N-M)". 2026-10-02 work_d95f7fc93... failed
# on "host/lab-configurations.toml:10-19" with a declared path. The spelling is normalised; the
# path is still checked against declared_paths and the range against the pinned commit.
_STRING_CITATION = re.compile(
    r"^(?P<path>[^:()\s]+)(?::(?P<a>\d+)(?:-(?P<b>\d+))?|\s*\(lines?\s*(?P<c>\d+)(?:\s*-\s*(?P<d>\d+))?\))?$")
TokenCounter = Callable[[Backend, str, str], int]


class LocalWorkError(RuntimeError):
    pass


class _Refused(Exception):
    """A delivery answer that cannot be rendered; the message is its named reason."""


def _digest(value: bytes | str) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _observe_temperature(manifest: dict[str, Any], observed: Mapping[str, Any]) -> None:
    """conditions.temperature is what the request body carried (stamped by the body builder), not what was
    asked for; null means none was sent and the server default applied."""
    if isinstance(manifest.get("conditions"), dict):
        manifest["conditions"]["temperature"] = observed.get("temperature")


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
        if task_family in DEEP_LANE_FAMILIES:
            # The family's own evidence pins the 27B at every depth (routing families:
            # code_fix / code_review -> qwen3.8-27b; the 30B failed the fix canary four times).
            # `auto` used to send small code work to the fast lane anyway (sizing-map 2026-09-27).
            return "deep"
        floor = 4096 if task_family == "quote_retrieval" else 8192
        return "deep" if evidence_tokens >= floor else "fast"

    @staticmethod
    def _route_profile() -> tuple[dict[str, str], str]:
        # HEARTH_LOCAL_WORK_ROUTES names a per-host route profile (the Linux production
        # host routes fast/deep to backends that exist in HEARTH_BACKENDS); unset, the
        # packaged profile applies, so the checked-in tests keep their meaning.
        override = os.environ.get("HEARTH_LOCAL_WORK_ROUTES", "").strip()
        target = Path(override) if override else (
            Path(__file__).resolve().parents[1] / "etc" / "local-work-routes.toml")
        raw = target.read_bytes()
        document = tomllib.loads(raw.decode("utf-8"))
        lanes = document.get("lane") or {}
        resolved = {name: str(value["backend"]) for name, value in lanes.items()}
        if not {"fast", "deep"} <= set(resolved) <= {"fast", "deep", "tool"}:
            raise LocalWorkError("local-work route profile requires fast and deep lanes (tool is optional)")
        return resolved, _digest(raw)

    @staticmethod
    def _serving_profile(settings: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        """Return an allowlisted, canonically digested serving declaration.

        This stamps what the selected provider *declared*, not an assertion that
        a remote server was live or launched with matching flags.  Keeping the
        projection scalar-only makes the manifest stable and prevents arbitrary
        nested backend configuration from becoming caller-visible provenance.
        """
        profile: dict[str, Any] = {}
        for key in sorted(SERVING_PROFILE_KEYS):
            if key not in settings:
                continue
            value = settings[key]
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise LocalWorkError(f"serving profile setting {key!r} must be scalar")
            profile[key] = value
        encoded = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return profile, _digest(encoded)

    @staticmethod
    def _template(kind: str, delivery: bool = False, quote_mode: str = "text") -> tuple[str, str]:
        mapping = {"unified_diff": "local_work_patch_v1.txt", "whole_file": "local_work_whole_file_v1.txt",
                   "json": "local_work_json_v1.txt", "markdown": "local_work_markdown_v1.txt"}
        if delivery:
            mapping["markdown"] = ("local_work_delivery_lines_v1.txt" if quote_mode == "line_reference"
                                   else "local_work_delivery_v1.txt")
        target = Path(__file__).resolve().parents[1] / "prompts" / mapping[kind]
        text = target.read_text(encoding="utf-8")
        return text, _digest(text)

    @staticmethod
    def _template_file(name: str) -> tuple[str, str]:
        text = (Path(__file__).resolve().parents[1] / "prompts" / name).read_text(encoding="utf-8")
        return text, _digest(text)

    @staticmethod
    def _delivery_task(intent: str, brief: Mapping[str, Any], packet: str, carried: bool = False) -> str:
        """REQUEST ... SOURCE FILES: the task text every delivery prompt carries after its instruction."""
        form = contract.form_defaults(brief)
        words = form.get("words")
        limits = [f"Write at most {words['max']} words in total (summary and paragraph text)."
                  + (f" Write at least {words['min']}." if words["min"] else "")] if words else []
        if form["sections"]:
            limits.append("Use these section headings, in this order: " + "; ".join(form["sections"]) + ".")
        substance = "\n".join(f"- {row['id']}: {row['statement']}" for row in brief["substance"])
        source_label = ("numbered; cite repository-relative path:N" if form["quote_mode"] == "line_reference"
                        else "numbered lines; quote the text without the line-number prefix" if carried
                        else "plain text; copy quotes from the CODE blocks")
        return (f"REQUEST\nINTENT: {intent}\n\nSUBSTANCE (the report must show each):\n{substance}"
                f"\n\nFORM:\n" + ("\n".join(f"- {x}" for x in limits) or "- No length limit.")
                + f"\n\nSOURCE FILES ({source_label}):\n{packet}")

    @staticmethod
    def _delivery_prompt(template: str, intent: str, brief: Mapping[str, Any], packet: str) -> str:
        return f"{template}\n\n" + LocalWorkService._delivery_task(intent, brief, packet)

    @staticmethod
    def _delivery_max_tokens(brief: Mapping[str, Any]) -> int:
        """1.75 tokens per word of the cap, plus 1,536 for quotes and JSON overhead; 8,192 with no cap;
        floor 2,048; never above the work.produce ceiling (the context check below still applies)."""
        if brief.get("generation", {}).get("max_tokens") is not None:
            return brief["generation"]["max_tokens"]
        words = contract.form_defaults(brief).get("words")
        want = int(1.75 * words["max"]) + 1536 if words else 8192
        return min(max(want, 2048), DELIVERY_TOKEN_CEILING)

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

        if str(provider.settings.get("engine") or "").lower() == "vllm":
            # vLLM's OpenAI server has no /apply-template; its POST /tokenize renders the
            # chat template itself when given messages and returns {"count", "tokens"}.
            counted = post("/tokenize", {"model": model,
                                         "messages": [{"role": "user", "content": prompt}],
                                         "add_generation_prompt": True,
                                         "add_special_tokens": False})
            tokens = counted.get("tokens")
            count = len(tokens) if isinstance(tokens, list) else counted.get("count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise LocalWorkError("exact tokenizer endpoint returned no token count")
            return count

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
               idempotency_key: str | None, caller_id: str,
               brief: Mapping[str, Any] | None = None,
               temperature: float | None = None, revise: bool = False,
               procedure: str | None = None) -> dict[str, Any]:
        if artifact_kind not in KINDS:
            raise LocalWorkError(f"artifact_kind must be one of {sorted(KINDS)}")
        if brief is not None and artifact_kind != "markdown":
            raise LocalWorkError("brief (delivery) requires artifact_kind markdown")
        if revise and brief is None:
            raise LocalWorkError("revise requires a brief (delivery)")
        if procedure is not None:
            if procedure not in ("carry", "one_call", "items"):
                raise LocalWorkError("procedure must be 'one_call', 'items' or 'carry' or absent")
            if brief is None:
                raise LocalWorkError(f"procedure {procedure!r} requires a brief (delivery)")
            if procedure == "items" and "items" not in brief:
                raise LocalWorkError("procedure 'items' requires a brief with items (brief.items.kind)")
        items_run = brief is not None and "items" in brief and procedure in (None, "items")
        if items_run and (revise or max_tokens is not None):
            raise LocalWorkError(f"procedure 'items' fixes its own budgets ({ITEMS_MAX_TOKENS} per call): "
                                 f"{'revise' if revise else 'max_tokens'} is refused")
        if procedure == "carry":
            if revise:
                raise LocalWorkError("procedure 'carry' has no revision round: revise is refused")
            if max_tokens is not None:
                raise LocalWorkError(f"procedure 'carry' fixes its own budgets ({CARRY_WORK_TOKENS} for the work turn): "
                                     "max_tokens is refused")
        if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                                        or not 0 <= temperature <= 2):
            raise LocalWorkError("temperature must be a number in [0, 2]")
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
        delivery = brief is not None
        if delivery:   # brief refusals come before any seat is asked to count tokens
            brief = {**brief, "sources": [{"path": name, "commit": base} for name in declared]}
            try:
                contract.check_brief(brief)
                source_map = sourcemap.build(str(repo_path), base, declared)
            except (contract.ContractError, sourcemap.SourceMapError) as exc:
                raise LocalWorkError(f"delivery brief refused: {exc}") from exc
            quote_mode = contract.form_defaults(brief)["quote_mode"]
        # The depth floor is token-based, not a byte heuristic.  For auto we
        # ask the currently declared fast server to count the evidence alone;
        # the selected server then counts the complete templated request below.
        routes, route_hash = self._route_profile()
        fast_provider = load_pool().by_name(routes["fast"])
        if fast_provider is None or not fast_provider.models:
            raise LocalWorkError("local fast lane is unavailable")
        roster = self._items_roster(routes) if items_run else None
        evidence_tokens = self.token_counter(
            fast_provider, fast_provider.models[0], source_pack) if lane == "auto" and not items_run else 0
        selected_lane = self._lane(lane, evidence_tokens, task_family)
        if items_run:   # the first reader's seat; the pack is never counted or sent
            selected_lane = "fast"
        if selected_lane not in routes:
            raise LocalWorkError(f"local lane {selected_lane!r} is not in this host's route profile")
        backend_name = routes[selected_lane]
        provider = load_pool().by_name(backend_name)
        if provider is None or provider.retired:
            raise LocalWorkError(f"local lane {selected_lane!r} is unavailable: {backend_name}")
        model = provider.models[0] if provider.models else ""
        work_id = (f"work_{_digest(caller_id + ':' + idempotency_key)[:32]}"
                   if idempotency_key else f"work_{uuid.uuid4().hex}")
        existing = self._read(work_id) if idempotency_key and self._manifest_path(work_id).is_file() else None
        context_tokens = int(provider.settings.get("context_tokens") or
                             (int(provider.settings.get("context_bytes") or 0) // 4))
        deliberate = int(provider.settings.get("deliberate_max_tokens") or 0)
        carried, pinned, choice, fallback, packet = False, False, None, None, None
        if delivery:
            if procedure:
                carried, pinned, choice = procedure == "carry", True, {"by": "caller", "level": "pin"}
            elif existing is not None:
                recorded = (existing.get("route") or {}).get("procedure") or ("carry" if "carry" in existing else "one_call")
                carried, items_run = recorded == "carry", recorded == "items" and items_run   # a brief without items: refused below
                pinned, choice = carried, {"by": "retry", "level": "recorded"}
            elif items_run:
                choice = {"by": "door", "level": "brief_items"}
            elif max_tokens is not None or revise:
                choice = {"by": "caller", "level": "caller_argument", "argument": "max_tokens" if max_tokens is not None else "revise"}
            else:
                try:
                    table, table_sha = procedures.load(os.environ.get("HEARTH_DELIVERY_PROCEDURES"))
                    picked, basis = procedures.choose(table, backend_name, task_family)
                except procedures.ProcedureTableError as exc:
                    raise LocalWorkError(f"delivery procedure table refused: {exc}") from exc
                carried, choice = picked == "carry", {"by": "door", **basis, "table_sha256": table_sha}
            if carried:
                if quote_mode != "text":
                    reason = "form.quote_mode must be text"
                    if pinned:
                        raise LocalWorkError(f"procedure 'carry' attaches text quotes: {reason}")
                    carried, fallback = False, reason
                elif selected_lane != "deep" or not deliberate:
                    reason = f"lane {selected_lane!r} -> {backend_name!r}: the deep lane on a backend that declares deliberate_max_tokens is needed"
                    if pinned:
                        raise LocalWorkError(f"procedure 'carry' needs the deep lane on a backend that declares "
                                             f"deliberate_max_tokens: lane {selected_lane!r} -> {backend_name!r}")
                    carried, fallback = False, reason
                elif deliberate < CARRY_WORK_TOKENS:
                    reason = f"{backend_name!r} declares deliberate_max_tokens {deliberate} < {CARRY_WORK_TOKENS}"
                    if pinned:
                        raise LocalWorkError(f"procedure 'carry' needs deliberate_max_tokens >= {CARRY_WORK_TOKENS}: "
                                             f"{backend_name!r} declares {provider.settings['deliberate_max_tokens']}")
                    carried, fallback = False, reason
        template, template_hash = self._template(artifact_kind, delivery, contract.form_defaults(brief)["quote_mode"]
                                                 if delivery else "text")
        request_doc = {"intent": intent, "acceptance_criteria": acceptance_criteria,
                       "artifact_kind": artifact_kind, "target_path": target,
                       "declared_paths": declared, "source_files": source_meta,
                       "source_pack": source_pack}
        explicit_max = max_tokens
        if items_run:
            item_list = self._items_enumerate(brief, repo_path, base, declared)
            used_hash = _digest(Path(items.__file__).read_bytes())
            prompt = json.dumps({"procedure": "items", "kind": brief["items"]["kind"], "brief": brief, "intent": intent,
                                 "items": [x["name"] for x in item_list], "readers": roster[:2], "settler": roster[2]},
                                sort_keys=True, separators=(",", ":"))
            max_tokens = ITEMS_MAX_TOKENS
        for _attempt in (0, 1):
            if items_run:
                break
            work_template = None
            if delivery:
                try:
                    packet = sourcemap.render_for_model(source_map, numbered=carried or quote_mode == "line_reference",
                                                        symbols=False)
                except sourcemap.SourceMapError as exc:
                    raise LocalWorkError(f"delivery brief refused: {exc}") from exc
                max_tokens = explicit_max if explicit_max is not None else (
                    CARRY_WORK_TOKENS if carried else self._delivery_max_tokens(brief))
            if carried:
                # The procedure is part of the prompt (and so of the digest the duplicate check compares).
                work_template, used_hash = self._template_file("local_work_delivery_work_v1.txt")
                task = self._delivery_task(intent, brief, packet, carried=True)
                prompt = f"PROCEDURE: carry\n{task}\n\n{work_template.strip()}"
            else:
                used_hash = template_hash
                prompt = (self._delivery_prompt(template, intent, brief, packet) if delivery
                          else template + "\n\nREQUEST\n" + json.dumps(request_doc, sort_keys=True))
            if not carried or pinned:
                break
            carried_bytes = len((CARRY_SYSTEM + prompt.split("\n", 1)[1]).encode("utf-8"))
            if context_tokens > 0 and carried_bytes // 4 + CARRY_WORK_TOKENS <= context_tokens:
                break
            carried, fallback = False, (f"carry work refusal: {carried_bytes // 4} input (bytes // 4) + "
                                        f"{CARRY_WORK_TOKENS} output > {context_tokens}")
        input_tokens = 0 if items_run else self.token_counter(provider, model, prompt)
        output_reserve = max_tokens or int(provider.settings.get("max_tokens") or 4096)
        if carried:
            carried_bytes = len((CARRY_SYSTEM + prompt.split("\n", 1)[1]).encode("utf-8"))
            if context_tokens <= 0 or carried_bytes // 4 + CARRY_WORK_TOKENS > context_tokens:
                raise LocalWorkError(f"carry work refusal: {carried_bytes // 4} input (bytes // 4) + "
                                     f"{CARRY_WORK_TOKENS} output > {context_tokens}")
        if context_tokens <= 0 or input_tokens + output_reserve > context_tokens:
            raise LocalWorkError(
                f"exact context refusal: {input_tokens} input + {output_reserve} output > {context_tokens}")
        serving_profile, serving_profile_sha256 = self._serving_profile(provider.settings)

        if existing is not None:
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
        lab_name, lab_source = active_configuration()
        manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA, "work_id": work_id, "envelope_id": env["envelope_id"],
            "status": "queued", "repo": str(repo_path), "base_commit": base,
            "source": {"digest": _digest(source_pack), "files": source_meta},
            "prompt": {"digest": _digest(prompt), "template_version": TEMPLATE_VERSION,
                       "template_sha256": used_hash, "input_tokens": input_tokens,
                       "output_reserve_tokens": output_reserve,
                       "context_tokens": context_tokens},
            "route": {"profile_version": ROUTE_PROFILE_VERSION,
                      "profile_sha256": route_hash,
                      "requested_lane": lane, "selected_lane": selected_lane,
                      "provider": backend_name, "model": model, "task_family": task_family,
                      "serving_profile": serving_profile,
                      "serving_profile_sha256": serving_profile_sha256},
            "artifact_kind": artifact_kind, "target_path": target, "declared_paths": declared,
            "conditions": {"environment": environment.stamp()["name"],
                           "lab_configuration": lab_name, "lab_configuration_source": lab_source,
                           "temperature": temperature},
            "criteria": acceptance_criteria, "attempts": [], "artifact": None,
            "receipt_id": receipt_id, "verdict": None,
            "caller": {"submitted_by": caller_id, "validated_by": None},
        }
        if delivery:
            manifest["delivery"] = True
            manifest["brief"] = brief
            if revise:
                manifest["revise"] = True
            manifest["brief_sha256"] = _digest(json.dumps(brief, sort_keys=True, separators=(",", ":")))
            manifest["route"]["procedure"] = "items" if items_run else "carry" if carried else "one_call"
            manifest["route"]["procedure_choice"] = {**choice, **({"fallback": fallback} if fallback else {})}
        if carried:
            manifest["carry"] = {"stage": "work", "batch": 0, "batches": 0, "retried": [], "jobs": [], "quotes": {}}
        if items_run:
            manifest["items"] = {"kind": brief["items"]["kind"], "stage": "read", "count": len(item_list),
                                 "readers": [{k: r[k] for k in ("backend", "model")} for r in roster[:2]],
                                 "settler": {k: roster[2][k] for k in ("backend", "model")}, "deadline_s": deadline_s,
                                 "jobs": [], "failure": None}
        with self._lock:
            self._write(manifest)
            envelope.store_envelope(env, work_id, raw_prompt=prompt)
            if items_run:
                try:
                    raw = (json.dumps(item_list, indent=2) + "\n").encode("utf-8")
                    (self._run_dir(work_id) / "items.json").write_bytes(raw)
                    manifest["items"]["items_sha256"] = _digest(raw)
                    self._reconcile_items(manifest)
                except Exception as exc:   # never leave a queued manifest nobody comes back to
                    self._items_fail(manifest, f"dispatch refused: {type(exc).__name__}: {exc}")
                    self._write(manifest)
                    raise
                self._write(manifest)
                self._spawn_auto_reconcile(work_id, manifest["job_id"])
                return self.reconcile(work_id)
            if carried:
                try:
                    state = self._carry_submit(
                        manifest, "carry-work", "work", 0, "",
                        [{"role": "system", "content": CARRY_SYSTEM},
                         {"role": "user", "content": prompt.split("\n", 1)[1]}],
                        True, 0 if temperature is None else temperature, CARRY_WORK_TOKENS, deadline_s,
                        {"type": "hearth_caller", "id": caller_id, "authenticated": True},
                        {"transport": "mcp", "adapter": caller_id})
                except Exception as exc:   # never leave a queued manifest nobody comes back to (the drain counts it busy)
                    self._carry_fail(manifest, None, f"dispatch refused: {type(exc).__name__}: {exc}")
                    self._write(manifest)
                    raise
                self._write(manifest)
                self._spawn_auto_reconcile(work_id, state["job_id"])
                return self.reconcile(work_id)
            state = self.execution.submit(
                operation_name="work.produce",
                arguments={"prompt": prompt, "backend": backend_name, "model": model,
                           "task_family": task_family,
                           **({"temperature": temperature} if temperature is not None else {}),
                           **({"response_schema": contract.output_json_schema()} if delivery else {})},
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
            self._spawn_auto_reconcile(work_id, state["job_id"])
        return self.reconcile(work_id)

    def _auto_reconcile(self, work_id: str, job_id: str) -> None:
        try:
            manifest = self._read(work_id)
            if "items" in manifest:
                self._items_wait(work_id)
            else:
                # A work advances with nobody polling: wait for this job to end, then reconcile, which stores the
                # outcome (or dispatches the next stage or the repair attempt and spawns its waiter). No wall-clock
                # cap: a job can outlive any fixed wait (deadline up to 3,600 s from its start, plus time queued).
                while True:
                    job = self.execution.get_job(job_id)
                    if job is None or job["status"] in FINAL_JOB_STATUSES:
                        break
                    self.execution.watch(job_id=job_id, after_sequence=int(job.get("last_sequence", 0)), wait_seconds=30)
        except Exception:
            pass
        try:
            self.reconcile(work_id)
        except Exception:
            pass

    def _spawn_auto_reconcile(self, work_id: str, job_id: str) -> None:
        try:
            thread = threading.Thread(
                target=self._auto_reconcile,
                args=(work_id, job_id),
                daemon=True,
                name=f"auto-reconcile-{work_id}",
            )
            thread.start()
        except Exception:
            pass

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

    def _validate_candidate(self, manifest: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
        """Mechanical checks only. Returns notes about any mechanical repair applied
        (today: git apply --recount when hunk line counts are wrong but the hunks apply)."""
        notes: dict[str, Any] = {}
        repo = Path(str(manifest["repo"]))
        base = str(manifest["base_commit"])
        declared = set(manifest["declared_paths"])
        normalized_citations = []
        string_ranges = 0
        for citation in candidate["citations"]:
            if isinstance(citation, str):
                match = _STRING_CITATION.match(citation.strip())
                if match and (match.group("a") or match.group("c")):
                    start = int(match.group("a") or match.group("c"))
                    end_text = match.group("b") or match.group("d")
                    citation = {"path": match.group("path"), "start_line": start,
                                "end_line": int(end_text) if end_text else start}
                    string_ranges += 1
                    notes["normalized_string_citation"] = string_ranges
            if isinstance(citation, str):
                path = _safe_relative(citation)
                if path not in declared:
                    raise LocalWorkError(f"citation path was not declared: {path}")
                lines = len(_git(repo, "show", f"{base}:{path}").splitlines())
                normalized_citations.append({"path": path, "start_line": 1, "end_line": max(1, lines)})
                notes["normalized_bare_citation"] = True
            elif isinstance(citation, dict):
                if set(citation) == {"path"}:
                    path = _safe_relative(str(citation["path"]))
                    if path not in declared:
                        raise LocalWorkError(f"citation path was not declared: {path}")
                    lines = len(_git(repo, "show", f"{base}:{path}").splitlines())
                    normalized_citations.append({"path": path, "start_line": 1, "end_line": max(1, lines)})
                    notes["normalized_bare_citation"] = True
                elif set(citation) == {"path", "start_line", "end_line"}:
                    path = _safe_relative(str(citation["path"]))
                    if path not in declared:
                        raise LocalWorkError(f"citation path was not declared: {path}")
                    start, end = citation["start_line"], citation["end_line"]
                    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                        raise LocalWorkError("citation line range is invalid")
                    lines = len(_git(repo, "show", f"{base}:{path}").splitlines())
                    if end > lines:
                        raise LocalWorkError(f"citation exceeds {path} at pinned commit")
                    normalized_citations.append({"path": path, "start_line": start, "end_line": end})
                else:
                    raise LocalWorkError("citation shape is invalid")
            else:
                raise LocalWorkError("citation shape is invalid")
        candidate["citations"] = normalized_citations
        if manifest["artifact_kind"] == "whole_file" and candidate["target_path"] != manifest["target_path"]:
            raise LocalWorkError("whole-file target_path mismatch")
        if manifest["artifact_kind"] == "whole_file":
            notes["changes"] = self._whole_file_changes(repo, base, str(manifest["target_path"]), str(candidate["content"]))
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
                    # Local models write correct hunks with wrong @@ line counts far more often than
                    # wrong hunks (omen-linux, 2026-09-27: attempt 1 of the first Linux candidate).
                    # --recount ignores the counts and re-derives them; the content still has to apply
                    # exactly, so this is a mechanical repair, not a semantic retry (ADR-0048).
                    recount = subprocess.run(["git", "-C", str(clone), "apply", "--check", "--recount",
                                              "--whitespace=error-all", "-"],
                                             input=content, text=True, capture_output=True, timeout=120)
                    if recount.returncode:
                        raise LocalWorkError(f"git apply --check failed: {completed.stderr.strip()}")
                    notes["git_apply"] = "recount"
                    notes["git_apply_strict_error"] = completed.stderr.strip()[:300]
        return notes

    @staticmethod
    def _whole_file_changes(repo: Path, base: str, target: str, content: str) -> dict[str, Any]:
        def lines(text: str) -> list[str]:
            text = text.replace("\r\n", "\n")
            return text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
        new = lines(content)
        if not _git(repo, "ls-tree", base, "--", target).strip():   # a new file: one hunk over all of it
            return {"hunks": [{"old": [0, 0], "old_empty": True, "new": [1, max(1, len(new))]}],
                    "added": len(new), "removed": 0}
        old = lines(_git(repo, "show", f"{base}:{target}"))
        if old == new:
            raise LocalWorkError("whole_file candidate is identical to the base file")
        hunks, added, removed = [], 0, 0
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            hunk: dict[str, Any] = {"old": [i1 + 1, i2] if i2 > i1 else [i1, i1], "new": [j1 + 1, j2] if j2 > j1 else [j1, j1]}
            if i2 == i1:
                hunk["old_empty"] = True
            if j2 == j1:
                hunk["new_empty"] = True
            hunks.append(hunk)
            removed += i2 - i1
            added += j2 - j1
        return {"hunks": hunks, "added": added, "removed": removed}

    def _dispatch_repair(self, manifest: dict[str, Any], job: Mapping[str, Any], raw: bytes, exc: Exception) -> None:
        work_id = str(manifest["work_id"])
        if len(manifest["attempts"]) >= 2:
            manifest["status"] = "failed"
            manifest["failure"] = str(exc)
            self._event(manifest, "outcome.final", {"status": "failed", "reason_sha256": _digest(str(exc))})
            return
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
            return
        state = self.execution.submit(
            operation_name="work.produce",
            arguments={"prompt": repair, "backend": manifest["route"]["provider"],
                       "model": manifest["route"]["model"],
                       **({"temperature": manifest["conditions"]["temperature"]}
                          if (manifest.get("conditions") or {}).get("temperature") is not None else {})},
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
        self._spawn_auto_reconcile(work_id, state["job_id"])

    def _fail(self, manifest: dict[str, Any], reason: str) -> None:
        manifest["status"] = "failed"
        manifest["failure"] = reason
        self._event(manifest, "verification.recorded", {"passed": False, "reason_sha256": _digest(reason)})
        self._event(manifest, "outcome.final", {"status": "failed", "reason": reason[:200]})

    def _render_answer(self, manifest: Mapping[str, Any], job: Mapping[str, Any], raw: bytes,
                       carried: Mapping[str, int] | None = None, items_meta: Mapping[str, Any] | None = None) -> tuple:
        """-> (output, markdown, delivery manifest); a failure raises _Refused with its named reason.
        `carried` (the carry procedure's repair counts) marks the answer as assembled from a carried draft."""
        try:
            output = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise _Refused(f"invalid_output_json: {exc}") from exc
        try:   # the renderer drops blank quotes and counts empty_quote_dropped; the raw answer is kept everywhere
            contract.check_output(delivery_render.drop_blank_quotes(output)[0])
        except contract.ContractError as exc:
            raise _Refused(f"invalid_delivery_output: {exc}") from exc
        observed = (job.get("invocations") or [{}])[-1]
        route = manifest["route"]
        meta = {"environment": (manifest.get("conditions") or {}).get("environment"),
                "model": observed.get("model") or route["model"], "backend": observed.get("backend") or route["provider"],
                "configuration": {"model": observed.get("model") or route["model"], "seat": route["provider"],
                                  "context_tokens": manifest["prompt"]["context_tokens"],
                                  "profile": route["serving_profile_sha256"][:12]},
                "aids_used": ITEMS_AIDS + (["thinking"] if items_meta["readers"][-1]["calls"] else []) if items_meta is not None
                else ["thinking", "carried_draft"] if carried is not None else ["constrained_output"]}
        try:
            sm = sourcemap.build(manifest["repo"], manifest["base_commit"], list(manifest["declared_paths"]))
            markdown, delivery = delivery_render.render(output, manifest["brief"], sm, meta)
            if carried is not None:
                delivery["procedure"] = "carry"
                delivery["repairs"].update({k: v for k, v in carried.items() if v})
            if items_meta is not None:
                delivery["procedure"], delivery["items"] = "items", dict(items_meta)
            contract.check_manifest(delivery)
        except (contract.ContractError, sourcemap.SourceMapError) as exc:
            raise _Refused(f"delivery_render_failed: {type(exc).__name__}: {exc}") from exc
        return output, markdown, delivery

    def _write_delivery_files(self, work_id: str, raw: bytes, delivery: Mapping[str, Any],
                              markdown: str | None, tag: str = "") -> dict[str, Any]:
        """tag '' writes the kept files; '.r0' / '.r1' the original / revised answer and manifest (no markdown)."""
        run_dir = self._run_dir(work_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        files = {"output": (f"delivery-output{tag}.json", raw, "application/json"),
                 "manifest": (f"delivery{tag}.json",
                              (json.dumps(delivery, indent=2, sort_keys=True) + "\n").encode("utf-8"), "application/json")}
        if markdown is not None:
            files["candidate"] = ("candidate.md", markdown.encode("utf-8"), "text/markdown; charset=utf-8")
        refs: dict[str, Any] = {}
        for key, (name, data, media_type) in files.items():
            (run_dir / name).write_bytes(data)
            refs[key] = {"file": name, "sha256": _digest(data), "size": len(data), "media_type": media_type}
        return refs

    def _adopt_delivery(self, manifest: dict[str, Any], job: Mapping[str, Any], metadata: Mapping[str, Any],
                        raw: bytes, rendered: tuple) -> None:
        """Make this answer the kept one: files, summary, observed route, artifact. Status is set by _finish_delivery."""
        _, markdown, delivery = rendered
        observed = (job.get("invocations") or [{}])[-1]
        refs = self._write_delivery_files(str(manifest["work_id"]), raw, delivery, markdown)
        by_match: dict[str, int] = {}
        for claim in delivery["claims"]:
            by_match[claim["match"]] = by_match.get(claim["match"], 0) + 1
        manifest["delivery_artifacts"] = refs
        manifest["delivery_summary"] = {
            "claims": len(delivery["claims"]), "by_match": by_match,
            "missing": by_match.get("missing", 0), "unsupported": len(delivery["unsupported"]),
            "words": delivery["measures"]["words"], "repairs": delivery["repairs"],
            "deviations": delivery["deviations"], "deterministic": delivery["verification"]["deterministic"]["state"]}
        # The route describes the kept answer: a repair the first answer needed must not survive a kept revision
        # that needed none (each attempt keeps its own, _note_attempt).
        manifest["route"].pop("structured_output_repairs", None)
        manifest["route"].update({key: observed.get(key) for key in
                                  ("backend", "model", "routed_by", "tokens_in", "tokens_out", "duration_ms",
                                   "response_schema_sha256", "temperature", "structured_output_repairs")
                                  if observed.get(key) is not None})
        _observe_temperature(manifest, observed)
        manifest["artifact"] = {key: metadata[key] for key in ("artifact_id", "sha256", "size", "media_type")}

    @staticmethod
    def _note_attempt(manifest: dict[str, Any], job: Mapping[str, Any]) -> None:
        """Record on the attempt what the door repaired in that job's answer (W15), whichever answer is kept."""
        repairs = ((job.get("invocations") or [{}])[-1]).get("structured_output_repairs")
        if not repairs:
            return
        for attempt in manifest.get("attempts") or []:
            if attempt.get("job_id") == job.get("job_id"):
                attempt["structured_output_repairs"] = list(repairs)

    def _finish_delivery(self, manifest: dict[str, Any], job_id: str) -> None:
        manifest["status"] = "awaiting_review"
        refs = manifest["delivery_artifacts"]
        self._event(manifest, "attempt.recorded", {"job_id": job_id, "ok": True})
        self._event(manifest, "artifact.produced", {"artifact_id": manifest["artifact"]["artifact_id"],
                    "sha256": refs["candidate"]["sha256"], "size": refs["candidate"]["size"],
                    "delivery": manifest["delivery_summary"]})
        self._event(manifest, "verification.recorded", {"passed": True,
                    "checks": ["delivery_output_schema", "delivery_render", "delivery_manifest"],
                    "deterministic": manifest["delivery_summary"]["deterministic"]})

    @staticmethod
    def _where(cid: str, output: Mapping[str, Any], quote: bool = True) -> str:
        """A claim id (s<i>.p<j>.q<k>, summary, words) in words the author can find in its own answer."""
        m = re.fullmatch(r"s(\d+)\.p(\d+)\.q(\d+)", cid)
        if not m:
            return {"summary": "The summary", "words": "The whole report"}.get(cid, cid)
        i, j, k = (int(x) for x in m.groups())
        heading = output["sections"][i]["heading"] if i < len(output["sections"]) else "?"
        return f"Section {i + 1} ({json.dumps(heading)}), paragraph {j + 1}" + (f", quote {k + 1}" if quote else "")

    @staticmethod
    def _rung0_failures(manifest: Mapping[str, Any], output: Mapping[str, Any],
                        delivery: Mapping[str, Any]) -> list:
        """Failing rung 0 findings other than an unresolved quote (those are counted as unsupported)."""
        return [f for f in verify_delivery(delivery, manifest["brief"], output)["rung0"]["findings"]
                if f["severity"] == "fail" and f["kind"] != "quote_unresolved"]

    def _objections(self, output: Mapping[str, Any], delivery: Mapping[str, Any], failures: list) -> list:
        """What the door can say, mechanically, is wrong with a rendered answer: each unsupported quote with the
        reason the renderer recorded for it, then each other failing rung 0 finding."""
        claims = {c["id"]: c for c in delivery["claims"]}
        found: list[str] = []
        for cid in delivery["unsupported"]:
            c = claims.get(cid) or {}
            quote = c.get("quote") or ""
            if not quote:
                found.append(f"{self._where(cid, output)}: the paragraph has no quote, so nothing supports it.")
                continue
            why = (f"this short quote appears {c['ambiguous']} times in the source files, so it cannot be placed"
                   if c.get("ambiguous") else "this text was not found in the source files")
            found.append(f"{self._where(cid, output)}: {json.dumps(quote[:300])}: {why}.")
        found += [f"{self._where(f['claim_id'], output, quote=False)}: {f['kind'].replace('_', ' ')}: {f['detail']}."
                  for f in failures]
        return found

    def _dispatch_revision(self, manifest: dict[str, Any], job: Mapping[str, Any], raw: bytes,
                           objections: list) -> None:
        """One bounded objection round: the original prompt, the first answer, the objections; same backend,
        schema, temperature and token budget (all read back from the first job). Never more than one: the
        idempotency key is per work item, so a reconcile that repeats this after a crash gets the same job."""
        work_id = str(manifest["work_id"])
        original = self.execution.artifacts.read(job["desired"]["input_artifact"]).decode("utf-8")
        lines = contract.form_defaults(manifest["brief"])["quote_mode"] == "line_reference"
        template, _ = self._template_file("local_work_delivery_lines_revise_v1.txt" if lines
                                          else "local_work_delivery_revise_v1.txt")
        prompt = (original + "\n\nYOUR FIRST ANSWER:\n" + raw.decode("utf-8", errors="replace")
                  + "\n\nOBJECTIONS:\n" + "\n".join(f"- {x}" for x in objections) + "\n\n" + template)
        arguments = dict(job["desired"]["arguments"])
        provider = load_pool().by_name(str(arguments["backend"]))
        if provider is None:
            raise LocalWorkError("revision route provider disappeared")
        tokens = self.token_counter(provider, str(arguments["model"]), prompt)
        reserve, context = int(manifest["prompt"]["output_reserve_tokens"]), int(manifest["prompt"]["context_tokens"])
        manifest["revision"]["prompt"] = {"digest": _digest(prompt), "input_tokens": tokens}
        if tokens + reserve > context:
            raise LocalWorkError(f"revision does not fit exact context: {tokens} input + {reserve} output > {context}")
        state = self.execution.submit(
            operation_name="work.produce", arguments={"prompt": prompt, **arguments},
            principal=job["principal"], source=job["source"], policy=job["desired"]["policy"],
            idempotency_key=f"{work_id}:revision")
        manifest["revision"]["reason"] = "revision dispatched"
        manifest["job_id"], manifest["request_id"] = state["job_id"], state["request_id"]
        manifest["status"] = "queued"
        manifest["attempts"].append({"number": 2, "job_id": state["job_id"], "request_id": state["request_id"],
                                     "repair": False, "revision": True})
        self._event(manifest, "step.dispatched", {"job_id": state["job_id"], "attempt": 2, "revision": True,
                    "objections": len(objections)})
        self._spawn_auto_reconcile(work_id, state["job_id"])

    @staticmethod
    def _revision_pending(manifest: Mapping[str, Any]) -> bool:
        revision = manifest.get("revision")
        return bool(revision) and revision.get("kept") is None

    @staticmethod
    def _measure(delivery: Mapping[str, Any], failures: list) -> dict[str, Any]:
        return {"unsupported": len(delivery["unsupported"]),
                "deterministic": delivery["verification"]["deterministic"]["state"],
                "rung0_failures": sorted({f["kind"] for f in failures}),
                "rung0_findings": sorted(json.dumps(f, sort_keys=True) for f in failures)}

    def _keep_original(self, manifest: dict[str, Any], reason: str, after: dict[str, Any] | None = None) -> None:
        coverage = manifest["revision"].get("coverage")
        if coverage and coverage.get("state") == "pending":
            coverage.update(state="unverified", reason=reason)
        manifest["revision"].update(kept="original", reason=reason, after=after)
        if len(manifest["attempts"]) > 1:
            self._event(manifest, "attempt.recorded", {"job_id": manifest["attempts"][1]["job_id"], "ok": False,
                        "revision": True, "reason_sha256": _digest(reason)})
        self._finish_delivery(manifest, str(manifest["attempts"][0]["job_id"]))

    @staticmethod
    def _better(before: Mapping[str, Any], after: Mapping[str, Any]) -> str | None:
        """Mechanical eligibility only. Coverage must also pass before the revised answer is kept."""
        if set(after["rung0_failures"]) - set(before["rung0_failures"]):
            return None
        if set(after.get("rung0_findings", [])) - set(before.get("rung0_findings", [])):
            return None
        if after["unsupported"] < before["unsupported"]:
            return f"unsupported quotes {before['unsupported']} -> {after['unsupported']}"
        if after["unsupported"] == before["unsupported"] and \
                len(set(after.get("rung0_findings", []))) < len(set(before.get("rung0_findings", []))):
            return (f"unsupported quotes equal ({after['unsupported']}) and rung 0 findings "
                    f"{len(set(before.get('rung0_findings', [])))} -> {len(set(after.get('rung0_findings', [])))}")
        return None

    def _dispatch_coverage(self, manifest: dict[str, Any], job: Mapping[str, Any], output: dict,
                           after: dict, reason: str) -> None:
        """Separate execution job: never hold the reconciliation lock while a judge runs."""
        rev, work_id = manifest["revision"], str(manifest["work_id"])
        original = self._revision_output(manifest, "original")
        if output != self._revision_output(manifest, "revised"):
            raise LocalWorkError("revised output differs from saved revision")
        author = str(manifest["route"]["provider"])
        backend = "am4-vllm" if author == "omen-dense-27b" else "omen-dense-27b"
        provider = load_pool().by_name(backend)
        if provider is None or provider.retired or not provider.models:
            raise LocalWorkError(f"coverage judge unavailable: {backend}")
        sm = sourcemap.build(manifest["repo"], manifest["base_commit"], list(manifest["declared_paths"]))
        prompt = revision_coverage.prompt(original, output, manifest["brief"],
                                          sourcemap.render_for_model(sm, numbered=True, symbols=False))
        tokens = self.token_counter(provider, provider.models[0], prompt)
        reserve = min(8192, max(2048, 256 * len(revision_coverage.prose(original))))
        context = int(provider.settings.get("context_tokens") or 0)
        if not context or tokens + reserve > context:
            raise LocalWorkError(f"coverage judge context refusal: {tokens} input + {reserve} output > {context}")
        state = self.execution.submit(
            operation_name="inference.generate",
            arguments={"prompt": prompt, "backend": backend, "model": provider.models[0],
                       "temperature": 0.0, "response_schema": revision_coverage.schema(original)},
            principal=job["principal"], source=job["source"],
            policy={"max_tokens": reserve, "deadline_s": 600},
            idempotency_key=f"{work_id}:revision-coverage")
        recorded = self.execution.get_job(state["job_id"])
        args = (recorded or {}).get("desired", {}).get("arguments", {})
        actual_prompt = self.execution.artifacts.read(recorded["desired"]["input_artifact"]) if recorded else b""
        if (_digest(actual_prompt) != _digest(prompt) or args.get("backend") != backend
                or args.get("model") != provider.models[0]
                or args.get("response_schema") != revision_coverage.schema(original)
                or args.get("temperature") != 0):
            raise LocalWorkError("coverage idempotency collision: recorded request differs")
        rev.update(after=after, reason=reason)
        rev["coverage"] = {"state": "pending", "backend": backend, "model": provider.models[0], "author_backend": author,
                           "job_id": state["job_id"], "author_job_id": job["job_id"],
                           "prompt_sha256": _digest(prompt), "input_tokens": tokens,
                           "output_reserve_tokens": reserve}
        manifest.update(job_id=state["job_id"], request_id=state["request_id"], status="queued")
        self._event(manifest, "step.dispatched", {"job_id": state["job_id"], "revision_coverage": True,
                    "provider": backend})
        self._spawn_auto_reconcile(work_id, state["job_id"])

    def _revision_output(self, manifest: Mapping[str, Any], version: str) -> dict:
        ref = manifest["revision"]["files"][version]["output"]
        raw = (self._run_dir(str(manifest["work_id"])) / ref["file"]).read_bytes()
        if _digest(raw) != ref["sha256"]:
            raise LocalWorkError(f"{version} revision output digest mismatch")
        return json.loads(raw)

    def _reconcile_coverage(self, manifest: dict[str, Any], job: Mapping[str, Any]) -> None:
        rev, work_id = manifest["revision"], str(manifest["work_id"])
        coverage, run_dir = rev["coverage"], self._run_dir(work_id)
        try:
            _, raw = self._result(job)
            filename = "revision-coverage.json"
            (run_dir / filename).write_bytes(raw)
            coverage["artifact"] = {"file": filename, "sha256": _digest(raw), "size": len(raw)}
            observed = (job.get("invocations") or [{}])[-1]
            coverage["observed"] = {k: observed.get(k) for k in
                                    ("backend", "model", "temperature", "tokens_in", "tokens_out", "duration_ms")}
            if observed.get("backend") != coverage["backend"] or observed.get("model") != coverage["model"]:
                raise LocalWorkError("coverage judge ran on a different backend or model")
            original = self._revision_output(manifest, "original")
            revised = self._revision_output(manifest, "revised")
            sm = sourcemap.build(manifest["repo"], manifest["base_commit"], list(manifest["declared_paths"]))
            assessment = revision_coverage.assess(raw, original, revised, sm)
            coverage.update(assessment)
            if assessment["state"] != "pass":
                return self._keep_original(manifest, "revision coverage failed: " + "; ".join(assessment["reasons"]),
                                           rev["after"])
            author_job = self.execution.get_job(coverage["author_job_id"])
            if author_job is None:
                raise LocalWorkError("revised author job missing after coverage judgment")
            metadata, answer = self._result(author_job)
            if _digest(answer) != rev["files"]["revised"]["output"]["sha256"]:
                raise LocalWorkError("revised answer changed during coverage judgment")
            rendered = self._render_answer(manifest, author_job, answer)
        except Exception as exc:
            coverage.update(state="unverified", reason=f"{type(exc).__name__}: {exc}")
            return self._keep_original(manifest, f"revision coverage unavailable: {type(exc).__name__}: {exc}",
                                       rev.get("after"))
        self._adopt_delivery(manifest, author_job, metadata, answer, rendered)
        rev.update(kept="revised", reason=rev["reason"] + "; non-author 27B coverage passed")
        self._finish_delivery(manifest, str(author_job["job_id"]))

    def _reconcile_revision(self, manifest: dict[str, Any], job: Mapping[str, Any]) -> None:
        """The second answer: kept only when it validates and is better (_better); otherwise the original stands
        and the reason is recorded. Nothing here fails the work item: the first answer was already rendered."""
        work_id = str(manifest["work_id"])
        self._note_attempt(manifest, job)
        try:
            metadata, raw = self._result(job)
        except LocalWorkError as exc:
            return self._keep_original(manifest, f"revised answer unreadable: {exc}")
        try:
            rendered = self._render_answer(manifest, job, raw)
        except _Refused as exc:
            (self._run_dir(work_id) / "delivery-output.r1.json").write_bytes(raw)
            manifest["revision"]["files"]["revised"] = {"output": {
                "file": "delivery-output.r1.json", "sha256": _digest(raw), "size": len(raw),
                "media_type": "application/json"}}
            return self._keep_original(manifest, f"revised answer refused: {exc}")
        output, _, delivery = rendered
        manifest["revision"]["files"]["revised"] = self._write_delivery_files(work_id, raw, delivery, None, ".r1")
        try:
            after = self._measure(delivery, self._rung0_failures(manifest, output, delivery))
        except Exception as exc:
            return self._keep_original(manifest, f"revised answer not checked: {type(exc).__name__}: {exc}")
        before = manifest["revision"]["before"]
        reason = self._better(before, after)
        if reason is None:
            return self._keep_original(
                manifest, f"revised answer is not better: unsupported quotes {before['unsupported']} -> "
                          f"{after['unsupported']}, rung 0 failures {before['rung0_failures']} -> "
                          f"{after['rung0_failures']}", after)
        try:
            original = self._revision_output(manifest, "original")
            if output != self._revision_output(manifest, "revised"):
                raise LocalWorkError("revised output differs from saved revision")
            if not revision_coverage.same_headings(original, output):
                raise LocalWorkError("revision changed section headings; original retained to preserve heading claims")
            if not revision_coverage.same_prose(original, output):
                return self._dispatch_coverage(manifest, job, output, after, reason)
        except Exception as exc:
            manifest["revision"]["coverage"] = {"state": "unverified", "reason": f"{type(exc).__name__}: {exc}"}
            return self._keep_original(manifest, f"revision coverage not dispatched: {type(exc).__name__}: {exc}", after)
        manifest["revision"].update(after=after, kept="revised", reason=reason + "; prose and headings unchanged",
            coverage={"state": "pass", "method": "identical_prose", "criteria_preserved": True,
                      "blocks": len(revision_coverage.prose(original)),
                      "reason": "Summary, headings, paragraph text and order are identical; only quotes changed."})
        self._adopt_delivery(manifest, job, metadata, raw, rendered)
        self._finish_delivery(manifest, str(job["job_id"]))

    # ---- the carry procedure: work (one thinking turn) -> attach (quotes, batch by batch) -> render ----
    def _carry_submit(self, manifest: dict[str, Any], key: str, stage: str, batch: int, part: str,
                      messages: list, thinking: bool, temperature: float, max_tokens: int, deadline_s: int,
                      principal: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
        """One inference.deliberate job for a stage; the stage is written to the manifest before the dispatch is
        recorded. A repeated call (a reconcile after a crash) gets the same job, and the recorded request is
        compared with this one, as the coverage job does."""
        work_id, carry_state = str(manifest["work_id"]), manifest["carry"]
        arguments = {"messages": messages, "backend": manifest["route"]["provider"], "model": manifest["route"]["model"],
                     "thinking": thinking, "temperature": temperature}
        carry_state.update(stage=stage, batch=batch, part=part)
        state = self.execution.submit(operation_name="inference.deliberate", arguments=arguments, principal=principal,
                                      source=source, policy={"max_tokens": max_tokens, "deadline_s": deadline_s},
                                      idempotency_key=f"{work_id}:{key}")
        recorded = self.execution.get_job(state["job_id"])
        args = (recorded or {}).get("desired", {}).get("arguments", {})
        wire = json.loads(self.execution.artifacts.read(recorded["desired"]["input_artifact"])) if recorded else None
        if (wire != messages or any(args.get(k) != v for k, v in arguments.items() if k != "messages")
                or recorded["desired"]["policy"].get("max_tokens") != max_tokens):
            raise LocalWorkError(f"carry idempotency collision: recorded {key} request differs")
        manifest.update(job_id=state["job_id"], request_id=state["request_id"], status="queued")
        if state["job_id"] not in [j["job_id"] for j in carry_state["jobs"]]:
            carry_state["jobs"].append({"stage": stage, "batch": batch, "part": part, "job_id": state["job_id"]})
            manifest["attempts"].append({"number": len(manifest["attempts"]) + 1, "request_id": state["request_id"],
                                         "job_id": state["job_id"], "repair": False, "carry_stage": stage})
        self._event(manifest, "step.dispatched", {"job_id": state["job_id"], "attempt": len(manifest["attempts"]),
                    "provider": manifest["route"]["provider"], "model": manifest["route"]["model"],
                    "carry_stage": stage, "carry_batch": batch, "carry_part": part},
                    refs={"receipt_id": manifest["receipt_id"]} if manifest.get("receipt_id") else {})
        return state

    def _carry_fail(self, manifest: dict[str, Any], job: Mapping[str, Any] | None, reason: str) -> None:
        """Any failed stage fails the work, names the stage, and keeps what exists; nothing falls back or renders."""
        carry_state, run_dir = manifest["carry"], self._run_dir(str(manifest["work_id"]))
        carry_state["failure"] = {"stage": carry_state["stage"], "batch": carry_state["batch"],
                                  "part": carry_state.get("part", ""), "reason": reason}
        if job is not None and carry_state["stage"] == "work":   # a cut or failed thinking turn still has its record
            for role, name in (("output", "carry-draft.partial.md"), ("reasoning", "carry-reasoning.txt")):
                ref = next((a["artifact_id"] for a in job.get("artifacts", []) if a.get("role") == role), None)
                if ref:
                    data = self.execution.read_artifact(str(ref))[1]
                    (run_dir / name).write_bytes(data)
                    carry_state.setdefault("kept", {})[role] = {"file": name, "sha256": _digest(data), "size": len(data)}
        self._fail(manifest, f"carry {carry_state['stage']}"
                   + (f" batch {carry_state['batch']}{carry_state.get('part', '')}" if carry_state["batch"] else "")
                   + f": {reason}")

    def _carry_draft(self, manifest: Mapping[str, Any]) -> str:
        ref = manifest["carry"]["draft"]
        data = (self._run_dir(str(manifest["work_id"])) / ref["file"]).read_bytes()
        if _digest(data) != ref["sha256"]:
            raise LocalWorkError("carry draft digest no longer matches manifest")
        return data.decode("utf-8")

    def _carry_parts(self, draft: str) -> tuple:
        """(blocks, carried, notes, found): only the report part is carried; the notes stay a work artifact."""
        blocks = carry.split_draft(draft)
        return (blocks, *carry.report_part(blocks))

    @staticmethod
    def _carry_units(batches: list, batch: int, part: str) -> list:
        unit = batches[batch - 1]
        half = (len(unit) + 1) // 2
        return {"": unit, "a": unit[:half], "b": unit[half:]}[part]

    def _carry_dispatch_attach(self, manifest: dict[str, Any], job: Mapping[str, Any], batch: int, part: str) -> None:
        carry_state, work_id = manifest["carry"], str(manifest["work_id"])
        _, carried, notes, _ = self._carry_parts(self._carry_draft(manifest))
        batches = carry.batches(carried)
        unit = self._carry_units(batches, batch, part)
        first = self.execution.get_job(carry_state["jobs"][0]["job_id"])
        user = json.loads(self.execution.artifacts.read(first["desired"]["input_artifact"]))[1]["content"]
        instruction = self._template_file("local_work_delivery_work_v1.txt")[0].strip()
        if not user.endswith("\n\n" + instruction):
            raise LocalWorkError("carry work prompt no longer ends with its instruction")
        task = user[:-len("\n\n" + instruction)]
        attach = self._template_file("local_work_delivery_attach_v1.txt")[0]
        head, mark, _ = attach.rpartition("BLOCKS TO CHECK:")
        if not mark:
            raise LocalWorkError("carry attach prompt no longer ends with BLOCKS TO CHECK:")
        if notes:
            head += CARRY_NOTES_HEADING + "\n" + carry.format_notes(notes) + "\n"
        content = task + "\n" + head + mark + "\n" + carry.format_blocks(unit)
        messages = [{"role": "system", "content": CARRY_SYSTEM}, {"role": "user", "content": content}]
        max_tokens = min(4096, 512 * len(unit) + 1024)   # a one-unit batch of a long sentence got 600 and was cut
        context = int(manifest["prompt"]["context_tokens"])
        needed = len((CARRY_SYSTEM + content).encode("utf-8")) // 4 + max_tokens
        if needed > context:
            return self._carry_fail(manifest, None, f"attach does not fit: {needed} (bytes // 4 + output) > {context}")
        self._carry_submit(manifest, f"carry-attach-{batch}{part}", "attach", batch, part, messages, False, 0, max_tokens,
                           int(first["desired"]["policy"]["deadline_s"]), job["principal"], job["source"])
        self._spawn_auto_reconcile(work_id, manifest["job_id"])

    def _carry_next(self, manifest: dict[str, Any], job: Mapping[str, Any]) -> None:
        """The unit after the one that just succeeded: the second half, the next batch, or render."""
        carry_state = manifest["carry"]
        batch, part = carry_state["batch"], carry_state.get("part", "")
        batches = carry.batches(self._carry_parts(self._carry_draft(manifest))[1])
        while True:
            batch, part = (batch, "b") if part == "a" else (batch + 1, "")
            if batch > len(batches):
                carry_state.update(stage="render", batch=len(batches), part="")
                self._write(manifest)
                return self._carry_render(manifest, job)
            if self._carry_units(batches, batch, part):
                return self._carry_dispatch_attach(manifest, job, batch, part)

    def _carry_retry(self, manifest: dict[str, Any], job: Mapping[str, Any], reason: str) -> None:
        carry_state = manifest["carry"]
        batch, part = carry_state["batch"], carry_state.get("part", "")
        if part or batch in carry_state["retried"]:
            return self._carry_fail(manifest, job, f"{reason} (second failure of batch {batch})")
        batches = carry.batches(self._carry_parts(self._carry_draft(manifest))[1])
        if len(self._carry_units(batches, batch, "")) < 2:   # its "half" would be the same request again
            return self._carry_fail(manifest, job, f"{reason} (a one-unit batch cannot be halved)")
        carry_state["retried"].append(batch)
        self._event(manifest, "attempt.recorded", {"job_id": job["job_id"], "ok": False, "carry_stage": "attach",
                    "carry_batch": batch, "retried_as_halves": True, "reason_sha256": _digest(reason)})
        self._carry_dispatch_attach(manifest, job, batch, "a")

    def _reconcile_carry(self, manifest: dict[str, Any], job: Mapping[str, Any] | None) -> None:
        carry_state, work_id = manifest["carry"], str(manifest["work_id"])
        if job is None:
            return self._carry_fail(manifest, None, "execution job missing")
        if carry_state["stage"] == "render":   # crashed after the stage was written: every quote is already stored
            return self._carry_render(manifest, job)
        if carry_state["stage"] == "attach" and carry_state.get("units") != "sentence":
            return self._carry_fail(manifest, job, "attach was dispatched by blocks before a restart; this door attaches "
                                                   "by sentence units and does not re-batch it")
        if job["status"] in IN_FLIGHT:
            manifest["status"] = "running" if job["status"] in {"dispatched", "running"} else "queued"
            return
        observed = (job.get("invocations") or [{}])[-1]
        if job["status"] != "succeeded":
            reason = job.get("reason") or f"execution ended {job['status']}"
            if carry_state["stage"] == "attach" and observed.get("error_code") == "output_truncated":
                return self._carry_retry(manifest, job, reason)
            return self._carry_fail(manifest, job, reason)
        self._event(manifest, "attempt.recorded", {"job_id": job["job_id"], "ok": True, "carry_stage": carry_state["stage"],
                    "carry_batch": carry_state["batch"], "carry_part": carry_state.get("part", "")})
        artifacts = {a["role"]: a["artifact_id"] for a in job.get("artifacts", []) if a.get("role")}
        if carry_state["stage"] == "work":
            draft = self.execution.read_artifact(str(artifacts["output"]))[1]
            run_dir = self._run_dir(work_id)
            reasoning = self.execution.read_artifact(str(artifacts["reasoning"]))[1] if "reasoning" in artifacts else b""
            (run_dir / "carry-draft.md").write_bytes(draft)
            (run_dir / "carry-reasoning.txt").write_bytes(reasoning)
            carry_state["draft"] = {"file": "carry-draft.md", "sha256": _digest(draft), "size": len(draft)}
            carry_state["reasoning"] = {"file": "carry-reasoning.txt", "sha256": _digest(reasoning), "size": len(reasoning)}
            carry_state["work"] = {k: observed.get(k) for k in ("finish_reason", "tokens_in", "tokens_out", "tokens_reasoning",
                                                                "duration_ms", "backend", "model", "temperature")}
            try:
                _, carried, notes, found = self._carry_parts(draft.decode("utf-8"))
                carry.assemble(carried, {}, notes_blocks=len(notes), sources=self._carry_sources(manifest))   # what render would refuse fails now, before any attach
                carry_state["batches"] = len(carry.batches(carried))
            except (carry.CarryError, UnicodeDecodeError) as exc:
                return self._carry_fail(manifest, job, f"the working draft cannot be carried: {exc}")
            if not found:   # the notes cannot be told from the report, and the notes hold line numbers: nothing is delivered
                return self._carry_fail(manifest, job, "the working draft has no report heading (notes, then a heading "
                                                       "\"Report\"): its notes cannot be separated from its report")
            if not carry_state["batches"]:
                return self._carry_fail(manifest, job, "the working draft has no text blocks")
            carry_state["units"] = "sentence"
            return self._carry_dispatch_attach(manifest, job, 1, "")
        batches = carry.batches(self._carry_parts(self._carry_draft(manifest))[1])
        unit = self._carry_units(batches, carry_state["batch"], carry_state.get("part", ""))
        answer = self.execution.read_artifact(str(artifacts["output"]))[1].decode("utf-8", errors="replace")
        try:
            quotes, repairs = carry.parse_attach(answer, [b["id"] for b in unit])
        except carry.CarryError as exc:
            return self._carry_retry(manifest, job, f"attach answer rejected: {exc}")
        for block_id, found in quotes.items():
            carry_state["quotes"][str(block_id)] = found
        done = carry_state.setdefault("repairs_by_job", {})
        done[job["job_id"]] = repairs   # per job, so a repeated reconcile never counts a repair twice
        self._carry_next(manifest, job)

    @staticmethod
    def _carry_sources(manifest: Mapping[str, Any]) -> dict:
        sm = sourcemap.build(manifest["repo"], manifest["base_commit"], list(manifest["declared_paths"]))
        return {f.path: f.lines for f in sm.files}

    def _carry_render(self, manifest: dict[str, Any], job: Mapping[str, Any]) -> None:
        carry_state, work_id = manifest["carry"], str(manifest["work_id"])
        work_job = self.execution.get_job(carry_state["jobs"][0]["job_id"])
        try:
            _, carried, notes, found = self._carry_parts(self._carry_draft(manifest))
            if not found:
                raise carry.CarryError("the working draft has no report heading")
            output, report = carry.assemble(carried, carry_state["quotes"], notes_blocks=len(notes), sources=self._carry_sources(manifest))   # keys as stored
            raw = (json.dumps(output, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            parse_repairs: dict[str, int] = {}
            for counts in carry_state.get("repairs_by_job", {}).values():
                for key, value in counts.items():
                    parse_repairs[key] = parse_repairs.get(key, 0) + value
            rendered = self._render_answer(manifest, work_job, raw, {**report["repairs"], **parse_repairs})
        except (carry.CarryError, _Refused) as exc:
            return self._carry_fail(manifest, job, f"render: {exc}")
        metadata = self.execution.read_artifact(str(next(
            a["artifact_id"] for a in work_job["artifacts"] if a.get("role") == "output")))[0]
        self._note_attempt(manifest, work_job)
        self._adopt_delivery(manifest, work_job, metadata, raw, rendered)
        run_dir = self._run_dir(work_id)
        agreement = json.dumps(carry.line_reference_agreement(report, rendered[2]), indent=2) + "\n"
        (run_dir / "line-references.json").write_text(agreement, encoding="utf-8", newline="")
        for key, name, media in (("carry_draft", "carry-draft.md", "text/markdown; charset=utf-8"),
                                 ("carry_reasoning", "carry-reasoning.txt", "text/plain; charset=utf-8"),
                                 ("line_references", "line-references.json", "application/json")):
            data = (run_dir / name).read_bytes()
            manifest["delivery_artifacts"][key] = {"file": name, "sha256": _digest(data), "size": len(data), "media_type": media}
        carry_state["report"] = {k: report[k] for k in ("blocks", "paragraphs", "paragraphs_with_quotes", "quotes",
                                                        "headings_without_paragraphs_dropped",
                                                        "blocks_empty_after_stripping_dropped") if k in report}
        self._finish_delivery(manifest, str(job["job_id"]))

    @staticmethod
    def _items_roster(routes: Mapping[str, str]) -> list[dict[str, str]]:
        """Readers: the fast then the tool lane's backends; settler: the deep lane's. No substitutes: two readings by one
        model are not two readings."""
        pool, roster = load_pool(), []
        for lane, role in (("fast", "reader"), ("tool", "reader"), ("deep", "settler")):
            name = routes.get(lane)
            if not name:
                raise LocalWorkError(f"procedure 'items' needs a {lane!r} lane in the route profile (the {role}): none is named")
            provider = pool.by_name(name)
            if provider is None or provider.retired or not provider.models:
                raise LocalWorkError(f"procedure 'items' {role} lane {lane!r} is unavailable: {name}")
            if role == "settler" and int(provider.settings.get("deliberate_max_tokens") or 0) < items.JUDGE_TOKENS:
                raise LocalWorkError(f"procedure 'items' needs a settler that declares deliberate_max_tokens >= {items.JUDGE_TOKENS}: "
                                     f"{name} declares {provider.settings.get('deliberate_max_tokens') or 0}")
            roster.append({"backend": name, "model": provider.models[0], "role": role})
        if len({r["backend"] for r in roster}) < 3:
            raise LocalWorkError("procedure 'items' needs three different backends (fast, tool and deep lanes): "
                                 + ", ".join(r["backend"] for r in roster))
        return roster

    @staticmethod
    def _items_enumerate(brief: Mapping[str, Any], repo_path: Path, base: str, declared: list[str]) -> list[dict]:
        try:
            return items.enumerate_items(brief["items"]["kind"], str(repo_path), base, declared)
        except items.ItemsError as exc:
            raise LocalWorkError(f"items refused: {exc}") from exc

    def _items_fail(self, manifest: dict[str, Any], reason: str) -> None:
        """Any failed stage fails the work and names the stage; the stage's unfinished jobs are cancelled."""
        state = manifest["items"]
        state["failure"] = {"stage": state["stage"], "reason": reason}
        for row in state["jobs"]:
            job = self.execution.get_job(row["job_id"])
            if job is not None and job["status"] not in FINAL_JOB_STATUSES:
                self.execution.cancel(row["job_id"], reason="items work failed")
        self._fail(manifest, f"items {state['stage']}: {reason}")

    def _items_answer(self, kind: str, job: Mapping[str, Any], stage: str) -> tuple:
        """-> (parse() / parse_judgment() result | None, error | None) for one finished job."""
        if job["status"] != "succeeded":
            return None, job.get("reason") or f"execution ended {job['status']}"
        try:
            if stage == "settle":   # a thinking answer: its output artifact, not the reasoning
                ref = next((a["artifact_id"] for a in job.get("artifacts", []) if a.get("role") == "output"), None)
                if ref is None:
                    raise LocalWorkError("completed job has no output artifact")
                return items.parse_judgment(kind, self.execution.read_artifact(str(ref))[1].decode("utf-8")), None
            return items.parse(kind, self._result(job)[1].decode("utf-8")), None
        except (LocalWorkError, items.ItemsError, UnicodeDecodeError) as exc:
            return None, str(exc)

    def _items_submit(self, manifest: dict[str, Any], stage: str, reader: int, item: Mapping[str, Any],
                      readings: list | None = None) -> None:
        state, work_id, caller = manifest["items"], str(manifest["work_id"]), manifest["caller"]["submitted_by"]
        kind, repo, base = state["kind"], manifest["repo"], manifest["base_commit"]
        who = state["settler"] if stage == "settle" else state["readers"][reader]
        if stage == "settle":
            operation, key = "inference.deliberate", f"{work_id}:items-settle-{item['id']}"
            arguments = {"messages": [{"role": "user", "content": items.judge_prompt(kind, item, repo, base, readings)}],
                         "backend": who["backend"], "model": who["model"], "thinking": True, "temperature": 0.0}
            max_tokens = items.JUDGE_TOKENS
        else:
            operation, key = "inference.generate", f"{work_id}:items-read-{reader}-{item['id']}"
            arguments = {"prompt": items.prompt(kind, item, repo, base), "backend": who["backend"], "model": who["model"],
                         "temperature": 0.0, "response_schema": items.schema(kind)}
            max_tokens = ITEMS_MAX_TOKENS
        job = self.execution.submit(
            operation_name=operation, arguments=arguments,
            principal={"type": "hearth_caller", "id": caller, "authenticated": True},
            source={"transport": "mcp", "adapter": caller},
            policy={"max_tokens": max_tokens, "deadline_s": state["deadline_s"]}, idempotency_key=key)
        state["jobs"].append({"stage": stage, "reader": reader, "item": item["id"], "job_id": job["job_id"]})
        manifest.update(job_id=job["job_id"], request_id=job["request_id"], status="queued")

    def _items_gather(self, manifest: Mapping[str, Any]) -> dict[str, dict[int, dict[str, Any]]]:
        """item id -> reader index (2 is the settler's judgment) -> {job_id, answer, error, calls} for every recorded job;
        calls are the job's invocations (a job requeued by a gateway restart called its seat twice, one expired in queue never)."""
        state, got = manifest["items"], {}
        for row in state["jobs"]:
            job = self.execution.get_job(row["job_id"])
            if job is None:
                raise LocalWorkError(f"execution job missing: {row['job_id']}")
            answer, error = self._items_answer(state["kind"], job, row["stage"])
            got.setdefault(row["item"], {})[row["reader"]] = {"job_id": row["job_id"], "answer": answer, "error": error,
                                                              "calls": len(job.get("invocations") or [])}
        return got

    def _reconcile_items(self, manifest: dict[str, Any]) -> None:
        """One pass of the items procedure: submit what the stage still lacks (a window at a time), and when every job of the
        stage is final, move to the next stage. Idempotent: the job keys are fixed, so a repeat after a crash gets the same jobs."""
        state, work_id = manifest["items"], str(manifest["work_id"])
        raw = (self._run_dir(work_id) / "items.json").read_bytes()
        if _digest(raw) != state["items_sha256"]:
            raise LocalWorkError("items.json digest no longer matches the manifest")
        listed, kind = json.loads(raw), state["kind"]
        if state["stage"] == "render":
            return self._items_render(manifest, listed)
        stage = state["stage"]
        by_id = {x["id"]: x for x in listed}
        plan = ([(("read", r, x["id"])) for x in listed for r in (0, 1)] if stage == "read"
                else [("settle", 2, i) for i in state["settle"]])
        recorded = {(j["stage"], j["reader"], j["item"]): j["job_id"] for j in state["jobs"]}
        jobs = {k: self.execution.get_job(recorded[k]) for k in plan if k in recorded}
        if any(j is None for j in jobs.values()):
            raise LocalWorkError("execution job missing for a recorded items job")
        live = [j for j in jobs.values() if j["status"] not in FINAL_JOB_STATUSES]
        todo = [k for k in plan if k not in recorded]
        for r in ((0, 1) if stage == "read" and (todo or live) else ()):   # past the 10% rule already: fail now, not after every call
            ended = [j for k, j in jobs.items() if k[1] == r and j["status"] in FINAL_JOB_STATUSES and j["status"] != "succeeded"]
            if len(ended) * 10 > len(listed):
                return self._items_fail(manifest, f"reader {state['readers'][r]['backend']} failed {len(ended)} of {len(listed)} calls: "
                                                  f"{ended[0].get('reason') or 'execution ended ' + ended[0]['status']}")
        if todo or live:
            slots = int(load_pool().by_name(state["settler"]["backend"]).settings.get("parallel_slots") or 1)
            room = (slots if stage == "settle" else ITEMS_WINDOW) - len(live)
            if todo and not state["jobs"]:
                self._event(manifest, "step.dispatched", {"items_stage": "read", "jobs": len(plan),
                            "provider": manifest["route"]["provider"], "model": manifest["route"]["model"]})
            sent = todo[:max(room, 0)]
            if stage == "settle" and sent and not any(j["stage"] == "settle" for j in state["jobs"]):
                self._event(manifest, "step.dispatched", {"items_stage": "settle", "jobs": len(plan),
                            "provider": state["settler"]["backend"], "model": state["settler"]["model"]})
            got = self._items_gather(manifest) if stage == "settle" and sent else {}
            for _, reader, item_id in sent:
                self._items_submit(manifest, stage, reader, by_id[item_id], (
                    [got[item_id][0]["answer"], got[item_id][1]["answer"]] if stage == "settle" else None))
            manifest["status"] = "running" if any(j["status"] in {"dispatched", "running"} for j in live) else "queued"
            return
        got = self._items_gather(manifest)
        if stage == "read":
            for r in (0, 1):
                failed = [got[x["id"]][r]["error"] for x in listed if got[x["id"]][r]["answer"] is None]
                if len(failed) * 10 > len(listed):
                    return self._items_fail(manifest, f"reader {state['readers'][r]['backend']} failed {len(failed)} of "
                                                      f"{len(listed)} calls: {failed[0]}")
            state["settle"] = [x["id"] for x in listed
                               if items.needs_judge(kind, [got[x["id"]][0]["answer"], got[x["id"]][1]["answer"]])]
            state["stage"] = "settle" if state["settle"] else "render"
        else:
            failed = [got[i][2]["error"] for i in state["settle"] if got[i][2]["answer"] is None]
            if len(failed) * 2 > len(state["settle"]):
                return self._items_fail(manifest, f"settler {state['settler']['backend']} failed {len(failed)} of "
                                                  f"{len(state['settle'])} calls: {failed[0]}")
            state["stage"] = "render"
        self._write(manifest)
        return self._reconcile_items(manifest)

    def _items_render(self, manifest: dict[str, Any], listed: list) -> None:
        state, work_id, kind = manifest["items"], str(manifest["work_id"]), manifest["items"]["kind"]
        got, rows, log = self._items_gather(manifest), [], []
        names = [("reader", state["readers"][0]), ("reader", state["readers"][1]), ("settler", state["settler"])]
        for x in listed:
            g = got[x["id"]]
            readings = [g[0]["answer"], g[1]["answer"]]
            judged = g.get(2, {}).get("answer")
            rows.append({**items.settle(kind, readings, judged), "readings": readings, "judgment": judged})
            log.append({"id": x["id"], "name": x["name"], "state": rows[-1]["state"], "by": rows[-1]["by"],
                        "readings": [{"reader": k, "role": names[k][0], "backend": names[k][1]["backend"], "job_id": e["job_id"],
                                      **({"error": e["error"]} if e["answer"] is None
                                         else {"answer": e["answer"]})} for k, e in sorted(g.items())]})
        output, report = items.assemble(kind, listed, rows, [r for _, r in names])
        calls = [sum(g[k]["calls"] for g in got.values() if k in g) for k in range(3)]
        meta = {**{k: report[k] for k in ("kind", "items", "agreed", "settled", "unverified", "reader_failures", "files")},
                "judge_failures": sum(1 for g in got.values() if 2 in g and g[2]["answer"] is None),
                "readers": [{**names[k][1], "role": names[k][0], "calls": calls[k]} for k in range(3)]}
        answer = (json.dumps(output, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        job = {"job_id": manifest["job_id"], "invocations": [{}]}
        try:
            rendered = self._render_answer(manifest, job, answer, items_meta=meta)
        except _Refused as exc:
            return self._items_fail(manifest, str(exc))
        stored = self.execution.artifacts.put(answer, media_type="application/json", filename=f"{work_id}-items-output.json")
        self._adopt_delivery(manifest, job, stored, answer, rendered)
        run_dir = self._run_dir(work_id)
        (run_dir / "items-readings.json").write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8", newline="")
        for key, name in (("items", "items.json"), ("items_readings", "items-readings.json")):
            data = (run_dir / name).read_bytes()
            manifest["delivery_artifacts"][key] = {"file": name, "sha256": _digest(data), "size": len(data),
                                                   "media_type": "application/json"}
        state["stage"], state["report"] = "done", {k: v for k, v in meta.items() if k != "readers"}
        self._finish_delivery(manifest, str(manifest["job_id"]))

    def _items_wait(self, work_id: str) -> None:
        """The waiter: reconcile, then wait on a job still in flight (or poll briefly), until the work leaves queued/running."""
        while True:
            manifest = self.reconcile(work_id)
            if manifest["status"] not in {"queued", "running"}:
                return
            for row in reversed(manifest["items"]["jobs"]):
                job = self.execution.get_job(row["job_id"])
                if job is not None and job["status"] not in FINAL_JOB_STATUSES:
                    self.execution.watch(job_id=job["job_id"], after_sequence=int(job.get("last_sequence", 0)), wait_seconds=2)
                    break
            else:
                time.sleep(0.05)

    def _reconcile_delivery(self, manifest: dict[str, Any], job: Mapping[str, Any],
                            metadata: Mapping[str, Any], raw: bytes) -> None:
        """A delivery job's result is a delivery-output.v1 document. Every failure is named and final:
        no structural repair prompt, no second lane. With `revise` set, one objection round may follow a
        first answer that rendered (see _dispatch_revision); a failed first answer never gets one."""
        self._note_attempt(manifest, job)
        try:
            rendered = self._render_answer(manifest, job, raw)
        except _Refused as exc:
            return self._fail(manifest, str(exc))
        self._adopt_delivery(manifest, job, metadata, raw, rendered)
        if manifest.get("revise") and not manifest.get("revision"):
            output, _, delivery = rendered
            try:
                failures = self._rung0_failures(manifest, output, delivery)
                objections = self._objections(output, delivery, failures)
            except Exception as exc:
                manifest["revision"] = {"round": 1, "objections": 0, "before": None, "after": None, "kept": None,
                                        "reason": "", "files": {}}
                return self._keep_original(manifest, f"revision not dispatched: objections not computed: "
                                                     f"{type(exc).__name__}: {exc}")
            if objections:
                manifest["revision"] = {
                    "round": 1, "objections": len(objections), "before": self._measure(delivery, failures),
                    "after": None, "kept": None, "reason": "",
                    "files": {"original": self._write_delivery_files(str(manifest["work_id"]), raw, delivery,
                                                                     None, ".r0")}}
                try:
                    return self._dispatch_revision(manifest, job, raw, objections)
                except Exception as exc:
                    # A revision is optional: whatever stops it (context, tokenizer, a paused or full execution
                    # queue) leaves the rendered first answer standing, with the reason named.
                    return self._keep_original(manifest, f"revision not dispatched: {type(exc).__name__}: {exc}")
        self._finish_delivery(manifest, str(job["job_id"]))

    def reconcile(self, work_id: str) -> dict[str, Any]:
        with self._lock:
            manifest = self._read(work_id)
            if manifest["status"] in FINAL or manifest["status"] == "awaiting_review":
                return manifest
            if not manifest.get("job_id"):
                # A submit that raised after the manifest was written (e.g. a refused max_tokens)
                # leaves no job behind. That is a terminal fact, not a reason to crash the
                # gateway's startup reconcile (omen-linux, 2026-09-27).
                manifest["status"] = "failed"
                manifest["failure"] = "no execution job was recorded for this submission"
                self._event(manifest, "outcome.final", {"status": "failed",
                            "reason_sha256": _digest(manifest["failure"])})
                self._write(manifest)
                return manifest
            if manifest.get("items"):
                try:
                    self._reconcile_items(manifest)
                except Exception as exc:   # a failed stage, named; raising would leave the work queued with no waiter
                    self._items_fail(manifest, f"{type(exc).__name__}: {exc}")
                self._write(manifest)
                return manifest
            job = self.execution.get_job(str(manifest["job_id"]))
            if manifest.get("carry"):
                try:
                    self._reconcile_carry(manifest, job)
                except Exception as exc:
                    # A refused next-stage dispatch or a render step that raised is a failed stage, named; raising
                    # here instead would leave the work queued with no waiter and break reconcile_all at mount.
                    self._carry_fail(manifest, None, f"{type(exc).__name__}: {exc}")
            elif job is None and self._revision_pending(manifest):
                self._keep_original(manifest, f"revision job missing: {manifest['job_id']}")
            elif job is None:
                manifest["status"] = "failed"
                manifest["failure"] = "execution job missing"
            elif job["status"] in {"accepted", "queued", "dispatched", "running"}:
                manifest["status"] = "running" if job["status"] in {"dispatched", "running"} else "queued"
            elif job["status"] != "succeeded" and self._revision_pending(manifest):
                self._keep_original(manifest, f"revision job {job['status']}: {job.get('reason') or 'no reason given'}")
            elif self._revision_pending(manifest) and (manifest["revision"].get("coverage") or {}).get("state") == "pending":
                self._reconcile_coverage(manifest, job)
            elif self._revision_pending(manifest):
                self._reconcile_revision(manifest, job)
            elif job["status"] != "succeeded":
                manifest["status"] = "failed"
                manifest["failure"] = job.get("reason") or f"execution ended {job['status']}"
                self._event(manifest, "attempt.recorded", {"job_id": job["job_id"], "ok": False,
                            "reason_sha256": _digest(manifest["failure"])})
                self._event(manifest, "outcome.final", {"status": "failed"})
            else:
                metadata, raw = self._result(job)
                if manifest.get("delivery"):
                    self._reconcile_delivery(manifest, job, metadata, raw)
                    self._write(manifest)
                    return manifest
                try:
                    candidate = self._parse_candidate(raw, str(manifest["artifact_kind"]))
                except LocalWorkError as exc:
                    self._dispatch_repair(manifest, job, raw, exc)
                else:
                    try:
                        mechanical = self._validate_candidate(manifest, candidate)
                    except LocalWorkError as exc:
                        if "citation shape is invalid" in str(exc) and len(manifest["attempts"]) < 2:
                            self._dispatch_repair(manifest, job, raw, exc)
                        else:
                            manifest["status"] = "failed"
                            manifest["failure"] = str(exc)
                            self._event(manifest, "verification.recorded", {"passed": False,
                                        "reason_sha256": _digest(str(exc))})
                            self._event(manifest, "outcome.final", {"status": "failed"})
                    else:
                        observed = (job.get("invocations") or [{}])[-1]
                        manifest["route"].update({key: observed.get(key) for key in
                                                  ("backend", "model", "routed_by", "tokens_in", "tokens_out", "duration_ms",
                                                   "temperature", "structured_output_repairs")
                                                  if observed.get(key) is not None})
                        _observe_temperature(manifest, observed)
                        manifest["artifact"] = {key: metadata[key] for key in
                                                ("artifact_id", "sha256", "size", "media_type")}
                        manifest["status"] = "awaiting_review"
                        changes = mechanical.pop("changes", None)
                        if changes is not None:
                            manifest["changes"] = changes
                        if mechanical:
                            manifest["mechanical"] = mechanical
                        self._event(manifest, "attempt.recorded", {"job_id": job["job_id"], "ok": True})
                        self._event(manifest, "artifact.produced", {"artifact_id": metadata["artifact_id"],
                                    "sha256": metadata["sha256"], "size": metadata["size"]})
                        self._event(manifest, "verification.recorded", {"passed": True,
                                    "checks": ["schema", "citations", "declared_paths",
                                               "git_apply_check_recount" if mechanical.get("git_apply") == "recount"
                                               else "git_apply_check"]})
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
            try:
                manifest = json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                continue
            if manifest.get("status") not in FINAL and manifest.get("status") != "awaiting_review":
                reconciled = self.reconcile(str(manifest["work_id"]))
                if reconciled.get("status") in {"queued", "running"} and reconciled.get("job_id"):
                    self._spawn_auto_reconcile(str(reconciled["work_id"]), str(reconciled["job_id"]))
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
        if manifest.get("delivery"):
            run_dir = self._run_dir(work_id)
            texts = {}
            for key, ref in manifest["delivery_artifacts"].items():
                data = (run_dir / ref["file"]).read_bytes()
                if _digest(data) != ref["sha256"]:
                    raise LocalWorkError(f"delivery {key} artifact digest no longer matches manifest")
                texts[key] = data.decode("utf-8")
            files = {key: {**ref, "path": str(run_dir / ref["file"])}
                     for key, ref in manifest["delivery_artifacts"].items()}
            delivery = {"manifest": json.loads(texts["manifest"]), "files": files,
                        "summary": manifest["delivery_summary"]}
            revision = manifest.get("revision")
            if revision:
                # The kept answer is above; this names both versions on disk (the other one included).
                versions = {}
                for role, refs in (revision.get("files") or {}).items():
                    for key, ref in refs.items():
                        if _digest((run_dir / ref["file"]).read_bytes()) != ref["sha256"]:
                            raise LocalWorkError(f"revision {role} {key} digest no longer matches manifest")
                    versions[role] = {key: {**ref, "path": str(run_dir / ref["file"])} for key, ref in refs.items()}
                other = {"original": "revised", "revised": "original"}.get(revision.get("kept"))
                delivery["revision"] = {**{k: v for k, v in revision.items() if k != "files"}, "versions": versions,
                                        "other": other if other in versions else None}
            return {"work_id": work_id, "artifact": files["candidate"], "candidate": texts["candidate"],
                    "delivery": delivery}
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
