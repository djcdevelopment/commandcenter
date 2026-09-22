"""Persisted, fail-closed review policy. No inference or network side effects."""
import json

PAIR = ["cc-builder-2", "cc-builder-3"]
PRESET = "omen-resident-hearth"


def validate(meta):
    policy = meta.get("promotion_policy", "auto")
    if policy not in ("auto", "manual"):
        raise ValueError("unknown promotion policy")
    hermes = meta.get("operator") in ("hermes", "jev")
    if hermes and (meta.get("promotion_policy") != "manual" or
                   meta.get("runner_preset") != PRESET or not isinstance(meta.get("builders"), list)
                   or not meta["builders"] or len(set(meta["builders"])) != len(meta["builders"])
                   or not set(meta["builders"]) <= set(PAIR)):
        raise ValueError("Hermes requires manual promotion and explicit OMEN-backed builders")
    if meta.get("runner_preset") not in (None, PRESET, "am4-shared-27b"):
        raise ValueError("unknown runner preset")
    return dict(meta)


def persisted_target(snapshot, incoming):
    """A changed request cannot weaken a saved run, even after checkpoint failure."""
    incoming = validate(incoming)
    saved = snapshot.get("target")
    if saved is None:
        if incoming.get("operator") in ("hermes", "jev"):
            raise ValueError("legacy snapshot lacks Hermes policy; use a new run id")
        return incoming
    saved = validate(saved)
    for field in ("operator", "promotion_policy", "runner_preset", "builders"):
        if saved.get(field) != incoming.get(field):
            raise ValueError("run policy changed since snapshot: " + field)
    return saved


def write_snapshot(path, builders, assay, target):
    value = {"builders": builders, "assay": assay, "target": validate(target)}
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out)


def review_result(meta, plan_id, winner, builds, repo):
    """No git write: retain every local candidate and its concrete commit."""
    import subprocess
    import fnmatch
    validate(meta)
    candidates = []
    for worker, build in builds.items():
        branch = f"ccfarm/{plan_id}/{worker}/lap1"
        result = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", branch],
                                capture_output=True, text=True, timeout=10)
        listed = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", branch],
                                capture_output=True, text=True, timeout=10)
        files = listed.stdout.splitlines() if listed.returncode == 0 else []
        changed = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "main..."+branch],
                                 capture_output=True, text=True, timeout=10)
        changed_files = changed.stdout.splitlines() if changed.returncode == 0 else []
        required = meta.get("requires") or []
        missing = [pattern for pattern in required if not any(fnmatch.fnmatchcase(name,pattern) for name in files)]
        unchanged = [pattern for pattern in required if not any(fnmatch.fnmatchcase(name,pattern) for name in changed_files)]
        candidates.append({"worker": worker, "branch": branch,
                           "commit": result.stdout.strip() if result.returncode == 0 else None,
                           "build": build, "missing_deliverables": missing,
                           "unchanged_deliverables": unchanged,
                           "artifact_status": "present-unreviewed" if required and not missing and not unchanged
                                              else "missing-or-unchanged-deliverables"})
    return {"promoted": False, "reason": "manual-review", "status": "awaiting_review",
            "winner": winner, "candidates": candidates}
