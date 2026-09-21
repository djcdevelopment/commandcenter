"""The work-item policy, and what "resolved" means for a D-113 companion.

WI-G2b condition 3. The WI-G2a candidate checked the D-113 companions by SHAPE:
forty hex characters was an "immutable base commit", and an isolated path was
"isolated" if it merely lay outside master. So a proposal naming an object that
exists in no repository, or a directory that does not exist, validated — and the
per-work-item prohibitions D-113 mentions ("high-impact authorities ... may be
prohibited by the work item") lived only in the prose of a brief.

This module is the register and the resolver:

* :func:`load_policy` reads `hearth/etc/work-items.toml`, validates it against
  `work-items.v1`, and RECOMPUTES every entry digest. An entry edited without
  updating its digest is refused, naming the entry — the register cannot drift
  underneath a validation that cites it.
* :func:`resolve_object` resolves a 40-character object id in the work item's
  declared repository with `git cat-file` — existence AND type. An abbreviated
  id is refused before git sees it: an abbreviation is ambiguous by
  construction, and this control plane cites full identities.
* :func:`check_isolated_path` requires an absolute path that EXISTS, lies
  outside the control-plane repository, and lies inside one of the work item's
  permitted roots — which also keeps it out of every other work item's worktree.

Nothing here is best-effort. A missing git binary, an unreadable repository, or
a timeout is a refusal with the reason, never a pass.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from hearth.operator import canonical, paths

CONTRACT_VERSION = "work-items.v1"
POLICY_FILENAME = "work-items.toml"

OBJECT_ID_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_TIMEOUT_S = 30


class WorkItemPolicyError(ValueError):
    """Raised when the work-item policy cannot be read or cannot be trusted."""


def policy_path() -> Path:
    return paths.ETC / POLICY_FILENAME


def entry_digest(entry: dict) -> str:
    """SHA-256 over the canonical JSON of one entry with `digest` removed."""
    body = {key: value for key, value in entry.items() if key != "digest"}
    return canonical.sha256_hex(canonical.canonical_json(body))


def load_policy(path: Optional[Path] = None) -> dict:
    """The work-item policy, contract-checked and digest-verified.

    Returns ``{contract_version, policy_version, digest, path, work_items:[...]}``
    where `digest` is the newline-normalized digest of the file itself — the
    value bound into a validation result.
    """
    target = Path(path) if path else policy_path()
    if not target.is_file():
        raise WorkItemPolicyError(
            f"no work-item policy at {paths.repo_relative(target)}; a mode:test proposal "
            "is judged against a register, and there is none")
    try:
        document = paths.load_toml(target)
    except Exception as exc:  # noqa: BLE001 - a broken register must say so
        raise WorkItemPolicyError(
            f"work-item policy {paths.repo_relative(target)} is not readable TOML: "
            f"{exc}") from exc
    try:
        canonical.validate_contract(document, CONTRACT_VERSION,
                                    label=paths.repo_relative(target))
    except canonical.CanonicalError as exc:
        raise WorkItemPolicyError(str(exc)) from exc

    entries = []
    seen: set[str] = set()
    for entry in document["work_item"]:
        declared = str(entry["digest"])
        computed = entry_digest(entry)
        if declared != computed:
            raise WorkItemPolicyError(
                f"work item {entry['id']} declares digest {declared[:16]} but its own "
                f"declaration hashes to {computed[:16]}: the register was edited without "
                "updating the entry. Recompute it (`python -c \"from hearth.operator "
                "import work_items; work_items.print_digests()\"`) and review the diff.")
        if entry["id"] in seen:
            raise WorkItemPolicyError(
                f"work item {entry['id']} is declared twice; one identity, one entry")
        seen.add(str(entry["id"]))
        entries.append(dict(entry))

    return {
        "contract_version": str(document["contract_version"]),
        "policy_version": str(document["policy_version"]),
        "path": paths.repo_relative(target),
        "digest": canonical.source_sha256(target),
        "work_items": entries,
    }


def released(policy: dict) -> dict[str, dict]:
    """The released entries, by id."""
    return {str(entry["id"]): entry for entry in policy["work_items"]
            if entry["status"] == "released"}


def find(policy: dict, work_item: str) -> Optional[dict]:
    for entry in policy["work_items"]:
        if str(entry["id"]) == str(work_item):
            return entry
    return None


def binding(policy: dict, work_item: Optional[str], entry: Optional[dict]) -> dict:
    """What a validation result records about the register that judged it."""
    return {
        "contract_version": policy["contract_version"],
        "policy_version": policy["policy_version"],
        "path": policy["path"],
        "digest": policy["digest"],
        "work_item": str(work_item) if work_item is not None else None,
        "entry_digest": str(entry["digest"]) if entry else None,
    }


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(repository), *arguments],
                              capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise WorkItemPolicyError(
            "git is not available on PATH, so no object id can be resolved; an "
            "unresolved reference is refused rather than assumed (D-113)") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkItemPolicyError(
            f"git did not answer within {GIT_TIMEOUT_S}s in {repository}; the object id "
            "is unresolved, which is a refusal") from exc


def resolve_object(repository: str, object_id: str, kind: str) -> Optional[str]:
    """Resolve `object_id` as `kind` ("commit" or "tree") in `repository`.

    Returns None when it resolves. Otherwise returns the reason it does not, in
    the words the validation record will carry. Never raises for a bad id: an
    unresolvable reference is a rejection, not a crash.
    """
    text = str(object_id)
    if not OBJECT_ID_RE.match(text):
        return (f"{text!r} is not a full 40-character lowercase object id; an abbreviated "
                "or symbolic reference is ambiguous by construction, and this control "
                "plane cites full identities")
    root = Path(str(repository))
    if not root.is_dir():
        return (f"the declared repository {repository} does not exist on this machine, so "
                f"{text[:12]} cannot be resolved")
    inside = _git(root, "rev-parse", "--git-dir")
    if inside.returncode != 0:
        return (f"{repository} is not a git repository "
                f"({(inside.stderr or '').strip() or 'git rev-parse failed'})")

    # The existence check the decision names, peeled to the kind asked for...
    peeled = _git(root, "cat-file", "-e", f"{text}^{{{kind}}}")
    if peeled.returncode != 0:
        return (f"{text} is not a resolvable {kind} in {repository} "
                f"(git cat-file -e {text[:12]}^{{{kind}}} exited "
                f"{peeled.returncode}: {(peeled.stderr or '').strip() or 'no such object'})")
    # ...and the type of the object itself, because a commit id PEELS to a tree:
    # `cat-file -e <commit>^{tree}` succeeds and would call a commit a tree.
    typed = _git(root, "cat-file", "-t", text)
    actual = (typed.stdout or "").strip()
    if typed.returncode != 0 or actual != kind:
        return (f"{text} is a {actual or 'missing'} object in {repository}, not a {kind}")
    return None


def check_isolated_path(entry: dict, path_text: str, *, master: Path) -> Optional[str]:
    """Is `path_text` an isolated location this work item may use?

    Returns None when it is, or the reason it is not. Resolution, existence and
    containment, in that order, because "outside master" alone let a path that
    does not exist — and another work item's worktree — count as isolation.
    """
    text = str(path_text)
    candidate = Path(text)
    if not candidate.is_absolute():
        return (f"{text!r} is a repository-relative path, which is inside the control "
                "plane repository by definition")
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        return f"{text!r} cannot be resolved ({exc})"
    if not resolved.exists():
        return (f"{text} does not exist; an isolated input or output is a real location "
                "this run reads or writes, not a name")
    try:
        resolved.relative_to(master.resolve())
        return f"{text} is inside the control plane repository {master}"
    except (ValueError, OSError):
        pass

    permitted = [Path(str(root)) for root in entry["permitted_isolated_roots"]]
    for root in permitted:
        try:
            resolved.relative_to(root.resolve())
            return None
        except (ValueError, OSError):
            continue
    return (f"{text} is outside every root {entry['id']} may use "
            f"({', '.join(str(root) for root in permitted)}); another work item's worktree "
            "is another writer's surface, not this run's isolation")


def prohibited(entry: dict, authorities) -> list[str]:
    """Which of `authorities` this work item prohibits outright."""
    forbidden = {str(name) for name in entry["prohibited_authorities"]}
    return sorted({str(name) for name in authorities} & forbidden)


def print_digests(path: Optional[Path] = None) -> None:  # pragma: no cover - a tool
    """Print the digest each entry SHOULD carry, for editing the register."""
    target = Path(path) if path else policy_path()
    document = paths.load_toml(target)
    for entry in document["work_item"]:
        print(f"{entry['id']}\t{entry_digest(entry)}")
