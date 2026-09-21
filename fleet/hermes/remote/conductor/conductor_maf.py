#!/usr/bin/env python3
"""conductor_maf.py - MAF-native conductor (PRODUCTION engine).
Watches inbox/*.md, runs plan->build->assay via real Microsoft Agent Framework.
Every hop is born-captured (CaptureFabric -> durable corpus + worker-side) and
traced via MAF OTel. Crash-isolated: a failed work item never kills the daemon.
Modes: --serve (daemon) | --plan-id X --plan '...' (one-shot test)."""
import argparse, asyncio, json, logging, os, subprocess, sys, time
from hermes_run_policy import validate as validate_run_policy, persisted_target, write_snapshot, review_result
from pathlib import Path
os.environ.setdefault("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT","http://am4.tail8e749c.ts.net:4318/v1/traces")
os.environ.setdefault("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT","http://am4.tail8e749c.ts.net:4318/v1/metrics")
os.environ.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL","http/protobuf")
os.environ.setdefault("OTEL_SERVICE_NAME","cc-conductor")
os.environ.setdefault("ENABLE_OTEL","true")
BASE=Path("/home/claude/work/commandcenter"); sys.path.insert(0,str(BASE))
from agent_framework import MCPStdioTool, WorkflowBuilder, WorkflowContext, FileCheckpointStorage, executor
from opentelemetry.propagate import inject
from spine.capture import CaptureFabric
try:  # imp04: classify a build's git_commit_push result so an F/0 self-explains in result.json
    from build_diagnostics import classify_build, merge_into_result
except Exception:
    classify_build = None
try:  # imp10: only retry transport/connect noise, never a genuine build failure
    from retry_transient import default_classifier as _is_transient
except Exception:
    def _is_transient(_e): return False
try:  # imp06: auto fast-forward the assay winner into farmer-repo main (refuse on divergence)
    from promote_winner import promote_winner as _promote_winner
except Exception:
    _promote_winner = None
try:  # L1: dispatch pulls from the ordered backlog while the fleet has WIP room
    from backlog import pump_to_inbox as _backlog_pump
except Exception:
    _backlog_pump = None
try:  # L4: risk is QA's second axis — grade says how good, risk says how safe to promote
    from risk_score import compute as _risk_compute
except Exception:
    _risk_compute = None
logging.basicConfig(level=logging.INFO, format="%(asctime)s [maf] %(levelname)s %(message)s")
log=logging.getLogger("cc-conductor-maf")
INBOX=BASE/"inbox"; RUNS=BASE/"runs"; CAPTURE=BASE/"idea-pipeline"/"capture.ndjson"; FLEET=BASE/"fleet.json"
FARMER_REPO=Path(os.getenv("FARMER_REPO_PATH", str(BASE/"farmer-repo")))
                                                     # imp06: local bare repo to promote winners into.
                                                     # To pour against a different target repo, set
                                                     # FARMER_REPO_PATH before starting the conductor
                                                     # (commandcenter-ontology repo on OMEN: docs/conductor-pour-howto.md) — the daemon reads
                                                     # it once at startup; there is no per-request override.
CCMETA_OPEN="<!-- CCMETA"
CCMETA_CLOSE="-->"
MAX_IN_FLIGHT=int(os.getenv("MAX_IN_FLIGHT","2"))   # imp07: bounded concurrent inbox processing
PROMOTE_LOCK=asyncio.Lock()                          # serialize promotions to main
try:
    from sd_watchdog import Watchdog                  # network-built (wd-sd-watchdog): systemd sd_notify liveness
except Exception:
    Watchdog=None
FABRIC=CaptureFabric(CAPTURE, caller="cc-conductor"); SCAN=3
sys.path.insert(0,str(BASE/"scripts"))
from otel_setup import setup_otel, force_flush as _otel_flush   # MAF owns the providers (one bootstrap fleet-wide)
TR=setup_otel("cc-conductor")
def tp_now():
    c={}; inject(c); return c.get("traceparent","")
def parse(res):
    if isinstance(res,str):
        try: return json.loads(res)
        except Exception: return {"_raw":res}
    for c in (res or []):
        t=getattr(c,"text",None)
        if not t: continue
        try: return json.loads(t)
        except Exception: continue
    return {"_raw":str(res)}
def _extract_ccmeta(plan_text):
    """Optional JSON metadata header, currently just a per-request builder subset.
    The target repo is NOT settable here — that's FARMER_REPO_PATH at daemon
    startup (commandcenter-ontology repo on OMEN: docs/conductor-pour-howto.md), so a work item can never silently
    redirect where a build lands.

    Format:
    <!-- CCMETA
    {"builders": ["cc-builder-2", "am4-worker-1"]}
    -->
    <builder prompt body>
    """
    text=plan_text.lstrip()
    if not text.startswith(CCMETA_OPEN):
        return {}, plan_text
    close=text.find(CCMETA_CLOSE)
    if close==-1:
        raise ValueError("CCMETA header missing closing -->")
    header=text[len(CCMETA_OPEN):close].strip()
    meta=json.loads(header) if header else {}
    if not isinstance(meta, dict):
        raise ValueError("CCMETA JSON must be an object")
    if "builders" in meta and meta["builders"] is not None:
        if not isinstance(meta["builders"], list) or not all(isinstance(b, str) for b in meta["builders"]):
            raise ValueError("CCMETA builders must be a list of node names")
    body=text[close+len(CCMETA_CLOSE):]
    return validate_run_policy(meta), body.lstrip("\r\n")
def _select_builders(builders, target_meta):
    allow=target_meta.get("builders") or []
    if not allow:
        return builders                       # default pool: exclude_from_build_pool still applies
    allowed=set(allow)
    picked=[b for b in builders if b[0] in allowed]
    present={b[0] for b in picked}
    # finding #1 (pour-c2 2026-07-03): an explicit allow-list OVERRIDES exclude_from_build_pool.
    # load_nodes() trims excluded nodes from the ready set before we ever see them, so without
    # this an allow-listed excluded node (e.g. cc-builder-4's mixtral debut) silently no-ops.
    # Re-admit only a named, healthy, real worker; the default pool is untouched (returns above).
    for name in sorted(allowed-present):
        ref=_node_extras(name)
        if ref.get("mcp_ready") and "worker" in ref.get("roles",[]):
            host=str(ref.get("tailnet",""))
            if _ssh_healthy(host, jump=ref.get("jump")):
                picked.append((name,host)); present.add(name)
            else:
                log.warning("allow-listed %s failed health probe - not re-admitted", name)
    missing=sorted(allowed-present)
    if missing:
        log.warning("requested builders missing from ready set: %s", ",".join(missing))
    return picked
def _node_extras(name):
    """Per-node reachability overrides from fleet.json. VMs never join the tailnet:
    same-host siblings resolve via mshome.net; off-host VMs declare 'jump' (ProxyJump
    via their tailnet-member hypervisor) + 'farmer_host' (their path back to the
    conductor, e.g. a cc-farmer-style tunnel on their host)."""
    try:
        for ref in json.loads(FLEET.read_text())["nodes"]:
            if ref.get("name")==name: return ref
    except Exception: pass
    return {}
def worker_tool(name, host):
    # Non-NAT workers (AM4) reach the conductor via the cc-farmer reverse-tunnel
    # ssh alias (cc-farmer-tunnel.service) — tailscale-ssh check-mode blocks direct
    # worker->conductor ssh on the tailnet.
    # NAT siblings (172.x or Hyper-V mshome.net names) reach the conductor by its
    # own sibling-DNS name — survives subnet moves, unlike a captured NAT IP.
    ex=_node_extras(name)
    _c_addr = ex.get("farmer_host") or ("cc-conductor.mshome.net" if (host.startswith("172.") or host.endswith(".mshome.net")) else "cc-farmer")
    farmer_env="FARMER_REPO_SSH=claude@"+_c_addr+":"+str(FARMER_REPO)
    args=(["-J",ex["jump"]] if ex.get("jump") else [])+["-i","/home/claude/.ssh/id_ed25519","-o","StrictHostKeyChecking=no","-o","ConnectTimeout=10","-o","ServerAliveInterval=15","-o","ServerAliveCountMax=3",
          "claude@"+host,farmer_env,"NODE_NAME="+name,"/home/claude/fleet-worker-node/.venv/bin/python",
          "/home/claude/fleet-worker-node/scripts/worker-mcp-server.py"]
    return MCPStdioTool(name=name, command="ssh", args=args, request_timeout=200)
def _ssh_healthy(host, timeout=6, jump=None):
    """Cheap liveness probe for a fleet node: can we ssh in and run true?
    Assay selection health-gates on this so a flaky first-listed node never
    silently owns the grading hop."""
    try:
        r=subprocess.run(["ssh"]+(["-J",jump] if jump else [])+["-i","/home/claude/.ssh/id_ed25519","-o","StrictHostKeyChecking=no",
                          "-o","BatchMode=yes","-o",f"ConnectTimeout={timeout}",
                          "claude@"+str(host),"true"],capture_output=True,timeout=timeout+4)
        return r.returncode==0
    except Exception:
        return False
def load_nodes(_resweep=True):
    fleet=json.loads(FLEET.read_text()); assay=None; builders=[]
    jumps={ref.get("name"):ref.get("jump") for ref in fleet["nodes"]}
    # Assay: first HEALTHY mcp-ready node with the assay role (fleet.json order
    # is preference order). A dead probe falls through to the next candidate.
    assay_candidates=[(ref["name"],ref.get("tailnet","")) for ref in fleet["nodes"]
                      if ref.get("mcp_ready") and "assay" in ref.get("roles",[])]
    for name,host in assay_candidates:
        if _ssh_healthy(host, jump=jumps.get(name)):
            assay=(name,host); break
        log.warning("assay candidate %s (%s) failed health probe - trying next",name,host)
    if assay is None and assay_candidates:
        assay=assay_candidates[0]
        log.warning("no assay candidate passed health probe - falling back to %s",assay[0])
    for ref in fleet["nodes"]:
        if not ref.get("mcp_ready"): continue
        host=str(ref.get("tailnet",""))
        # Builders = the OMEN-hosted Hyper-V VMs (172.x NAT — prefix survives subnet moves on host reboot),
        # except the assay node. (OMEN/claudefarm1 pulled from the pool: the data showed
        # its 14b is a router, not a builder — slow + low quality, gated the A/B wall.
        # It now serves the routing tier via route_plan(). am4/omen lack 'worker' anyway.)
        if ("worker" in ref.get("roles",[]) and not ref.get("exclude_from_build_pool")
                and (assay is None or ref["name"]!=assay[0])):
            builders.append((ref["name"],host))
    # NAT re-discovery (2026-07-02): OMEN reboots regenerate the Hyper-V /20
    # (172.19 -> 172.29 -> 172.30 so far) and builder VMs only self-register on
    # FIRST boot, so every 172.x entry goes stale at once. If any NAT-addressed
    # selection fails its probe, sweep the conductor's current /20 for the
    # siblings (fleet_rediscover, cooldown-guarded) and reselect once.
    if _resweep:
        sel=builders+([assay] if assay else [])
        if any((str(h).startswith("172.") or str(h).endswith(".mshome.net")) and not _ssh_healthy(h, jump=jumps.get(_n)) for _n,h in sel):
            try:
                import fleet_rediscover
                if fleet_rediscover.attempt():
                    log.warning("fleet re-addressed by rediscovery sweep - reselecting nodes")
                    return load_nodes(_resweep=False)
            except Exception as e:
                log.warning("fleet_rediscover failed: %s",str(e)[:150])
    return builders, assay
async def call(t, target, tool, **kw):
    tp=tp_now()
    with FABRIC.capture(target=target, tool=tool, payload={**kw,"traceparent":tp}, traceparent=tp) as h:
        res=parse(await t.call_tool(tool, traceparent=tp, **kw)); h["result"]=res; return res
async def build_one(name, host, plan_id, plan_text, mode="build", runner_preset=None, max_age_s=None):
    started = time.monotonic()
    with TR.start_as_current_span("build."+name, attributes={"worker":name,"plan.id":plan_id}):
        # Connect + branch setup is the transient-prone step (SSH/MCP). Retry once
        # on transport noise (imp10 classifier) so infra blips don't grade as F;
        # a genuine setup error still returns an honest failure. (Also stops a
        # connect error from escaping build_one and sinking the whole gather.)
        t=None; br=None
        for _attempt in range(2):
            try:
                t=worker_tool(name,host)
                await asyncio.wait_for(t.connect(), timeout=30)
                br=await asyncio.wait_for(
                    call(t,name,"git_setup_branch",plan_id=plan_id),
                    timeout=45,
                )
                break
            except Exception as e:
                try:
                    if t: await t.close()
                except Exception: pass
                t=None
                if _attempt==0 and (isinstance(e, asyncio.TimeoutError) or _is_transient(e)):
                    log.warning("build %s transient setup, retrying: %s",name,str(e)[:120])
                    await asyncio.sleep(3); continue
                log.warning("build %s setup failed: %s",name,str(e)[:200])
                return name,{"ok":False,"error":str(e)[:200]}
        try:
            task_id=plan_id+"-"+name
            options = {"runner_preset": runner_preset} if runner_preset else {}
            if max_age_s is not None:
                remaining = int(max_age_s - (time.monotonic() - started))
                if remaining < 1:
                    return name, {'ok':False,'error':'deadline exhausted during setup'}
                options['max_age_s'] = remaining
            rp=await call(t,name,"run_plan",plan=plan_text,plan_id=task_id,workdir=br.get("workspace",""),mode=mode,**options)
            if rp.get("status") != "launched":
                return name, {"ok": False, "error": "worker refused launch", "launch": rp}
            supervised=bool((rp or {}).get("supervised"))
            done="## DONE: "+task_id; ok=False; sig=None
            for _ in range(90):
                await asyncio.sleep(5)
                prog=await call(t,name,"get_progress")
                sig=(prog.get("done_signals") or {}).get(task_id)  # imp02/imp03: structural (authoritative)
                if sig is not None:
                    ok=(sig.get("rc")==0) and not sig.get("timed_out"); break
                # Legacy "## DONE" scrape only for un-supervised nodes; a supervised
                # build waits for its sentinel so we capture the real rc/timed_out.
                if not supervised and any(done in str(d) for d in prog.get("done_runs",[])): ok=True; break
            pr=await call(t,name,"git_commit_push",plan_id=plan_id,message="build "+plan_id)
            entry={"ok":ok,"branch":br.get("branch"),"push_ok":pr.get("ok"),
                   "runner":(rp or {}).get("runner"),"runner_model":(rp or {}).get("runner_model"),
                   "runner_preset":(rp or {}).get("runner_preset"),"resolved_runner":(rp or {}).get("resolved_runner"),
                   "committed":pr.get("committed"),"pushed":pr.get("pushed"),
                   "commits_ahead":pr.get("commits_ahead"),"empty_build":pr.get("empty_build"),
                   "done_signal":sig is not None,
                   "agent_rc":(sig or {}).get("rc"),"agent_timed_out":(sig or {}).get("timed_out")}
            if classify_build: entry=merge_into_result(entry, classify_build(pr, ok))
            return name,entry
        except Exception as e:
            log.warning("build %s failed: %s",name,str(e)[:200]); return name,{"ok":False,"error":str(e)[:200]}
        finally:
            try: await t.close()
            except Exception: pass
async def run_assay(name, host, plan_id, worker_names):
    with TR.start_as_current_span("assay", attributes={"plan.id":plan_id}):
        t=None
        try:
            # connect inside the try: assay is best-effort (same standard as route/tiebreak) —
            # a dead assay node must degrade to {"error"}, never sink the whole workflow.
            # Hard client-side deadline (soak test 2026-07-02/03: two runs hung ~5h in
            # this exact call, well past request_timeout=200 on the MCPStdioTool — the
            # SSH transport can wedge on a silently-dropped connection without surfacing
            # an error). build_one() already has an explicit bounded poll for the same
            # reason; assay lacked the equivalent belt-and-suspenders.
            t=worker_tool(name,host)
            await asyncio.wait_for(t.connect(), timeout=30)
            return await asyncio.wait_for(
                call(t,name,"assay_compare_branches",plan_id=plan_id,workers=worker_names), timeout=200)
        except asyncio.TimeoutError:
            log.warning("assay timed out (hard deadline) for %s",name); return {"error":"assay-timeout"}
        except Exception as e: log.warning("assay failed: %s",str(e)[:200]); return {"error":str(e)[:200]}
        finally:
            try:
                if t: await t.close()
            except Exception: pass
def force_flush():
    try: _otel_flush()
    except Exception: pass

# ── routing tier (OMEN small model, over MCP/MAF — no side-door) ───────────────
# The A2A routing hop rides the standard: the conductor reaches the am4 MCP server's
# classify_task tool via a MAF MCPStdioTool through call(), so the decision is
# born-captured + traced BY CONSTRUCTION (capture is a property of the transport, not
# something route_plan must remember). The OMEN inference is the leaf inside that tool.
# Advisory for now (does not gate the fan-out); the captured {router-prediction vs
# actual-winner} pairs are the corpus for turning this into real routing later.
def am4_tool():
    args=["-i","/home/claude/.ssh/id_ed25519","-o","StrictHostKeyChecking=no","-o","ConnectTimeout=10","-o","ServerAliveInterval=15","-o","ServerAliveCountMax=3",
          "derek@192.168.12.233",
          "/home/derek/am4-fleet-node/.venv/bin/python",
          "/home/derek/am4-fleet-node/scripts/am4-mcp-server.py"]
    return MCPStdioTool(name="am4", command="ssh", args=args, request_timeout=60)

async def route_plan(plan_id, plan_text):
    """OMEN routing decision via the am4 MCP server (classify_task), captured by the
    MCP transport. Best-effort: routing never blocks the build if the router is down."""
    return {"error":"route-disabled-temporarily"}
    with TR.start_as_current_span("route_node", attributes={"plan.id":plan_id}):
        t=am4_tool()
        try:
            await asyncio.wait_for(t.connect(), timeout=20)
            return await asyncio.wait_for(
                call(t, "am4", "classify_task", plan=plan_text),
                timeout=40,
            )
        except asyncio.TimeoutError:
            log.warning("[%s] route timed out (non-blocking)",plan_id)
            return {"error":"route-timeout"}
        except Exception as e:
            log.warning("[%s] route failed (non-blocking): %s",plan_id,str(e)[:150])
            return {"error":str(e)[:150]}
        finally:
            try: await t.close()
            except Exception: pass

# L2: appended to every dispatched plan — the builder's side of the question loop.
# A builder that writes QUESTION.md is asking, not failing; the operator's answer
# re-queues the plan as a new lap with the answer inline (file-based pause/resume).
QPROTO=("\n\n## Fleet protocol: blocked-on-ambiguity\n"
        "If - and ONLY if - you are genuinely blocked by ambiguity or missing information, "
        "write the single blocking question to a file named QUESTION.md in the workspace root, "
        "commit it, and stop. An operator answer will re-queue this task with the answer inline. "
        "Never guess on destructive or irreversible choices; for everything else, prefer building "
        "with a stated assumption over asking.\n")

def _scan_questions(plan_id, workers):
    """L2: surface each builder's QUESTION.md (if any) as a board artifact in
    board/questions.ndjson (append-only; last record per qid wins)."""
    out=[]
    for w in workers:
        br=f"ccfarm/{plan_id}/{w}/lap1"
        try:
            q=subprocess.run(["git","-C",str(FARMER_REPO),"show",br+":QUESTION.md"],
                             capture_output=True,text=True,timeout=10)
        except Exception: continue
        if q.returncode!=0 or not q.stdout.strip(): continue
        out.append({"qid":"q-"+plan_id+"-"+w,"plan_id":plan_id,"worker":w,
                    "question":q.stdout.strip()[:2000],"branch":br,"status":"pending",
                    "ts":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())})
    if out:
        qf=BASE/"board"/"questions.ndjson"; qf.parent.mkdir(parents=True,exist_ok=True)
        with qf.open("a") as f:
            for r in out: f.write(json.dumps(r)+"\n")
        log.info("[%s] %d builder question(s) surfaced on the board",plan_id,len(out))
    return out

def _tied_top(assay_res):
    """Workers tied for the top score (>=2 => a real tie worth breaking). Only a
    positive-score tie is worth a critic call — two failures (0/F) are not."""
    sb=assay_res.get("scoreboard") or []
    if len(sb)<2: return []
    top=sb[0].get("score")
    if not top or top<=0: return []
    tied=[r for r in sb if r.get("score")==top]
    return tied if len(tied)>=2 else []

def _branch_files(plan_id, worker, max_files=6, max_bytes=6000):
    """Read a candidate branch's source out of the bare farmer-repo (skip build
    junk + the retro) so the critic can judge the actual code."""
    br="ccfarm/"+plan_id+"/"+worker+"/lap1"; files={}
    try:
        names=subprocess.run(["git","-C",str(FARMER_REPO),"ls-tree","-r","--name-only",br],
                             capture_output=True,text=True,timeout=10).stdout.split()
    except Exception: return files
    for n in names:
        if n=="retro.md" or n.endswith(".pyc") or "__pycache__" in n: continue
        try:
            c=subprocess.run(["git","-C",str(FARMER_REPO),"show",br+":"+n],
                            capture_output=True,text=True,timeout=10).stdout
        except Exception: continue
        files[n]=c[:max_bytes]
        if len(files)>=max_files: break
    return files

async def _tiebreak(plan_id, plan_text, assay_res):
    """When >=2 candidates tie on the top behavior score, the AM4 local critic (14b)
    picks the winner. Reached via the am4 MCP tool through call(), so the tiebreak is
    born-captured + traced BY CONSTRUCTION (no side-door) — same standard as routing.
    Best-effort: any failure or invalid pick falls back to the score-order winner."""
    tied=_tied_top(assay_res)
    if not tied: return None
    labels=[r.get("worker") for r in tied]
    with TR.start_as_current_span("tiebreak", attributes={"plan.id":plan_id,"tied":",".join(labels)}):
        cands=[{"worker":r.get("worker"),"files":_branch_files(plan_id,r.get("worker"))} for r in tied]
        t=am4_tool()
        try:
            await t.connect()
            verdict=await call(t,"am4","critique_tiebreak",plan=plan_text,candidates=cands)
            # L0/debias-lite (from r3-critic-debias): a second call with the candidate
            # order REVERSED. Agreement across orderings => real winner; disagreement
            # => position bias, fall back to order and say so. Full rotation averaging
            # lives in scripts/debias.py for the sync planning ensemble.
            verdict2=await call(t,"am4","critique_tiebreak",plan=plan_text,candidates=list(reversed(cands)))
        except Exception as e:
            log.warning("[%s] tiebreak failed (fallback to order): %s",plan_id,str(e)[:150])
            return {"tied":labels,"method":"am4-critic","winner":None,"fallback":"order","error":str(e)[:150]}
        finally:
            try: await t.close()
            except Exception: pass
        w1=verdict.get("winner") if verdict.get("winner") in labels else None
        w2=verdict2.get("winner") if verdict2.get("winner") in labels else None
        w=w1 if (w1 is not None and w1==w2) else None
        bias=(w1 is not None or w2 is not None) and w is None
        log.info("[%s] tiebreak among %s -> %s (fwd=%s rev=%s%s)",plan_id,labels,
                 w or "NO-CONSENSUS->order",w1,w2," POSITION-BIAS" if bias else "")
        return {"tied":labels,"method":"am4-critic-debiased","winner":w,"why":verdict.get("why"),
                "critic_model":verdict.get("critic_model"),"bias_detected":bias,
                "fallback":None if w else "order"}

def _workflow_for(plan_id, plan_text, builders, assay, storage, target_meta):
    """Per-plan MAF graph: route -> builder fan-out -> finalize (assay/tiebreak/promote).
    A checkpoint resume must rebuild the IDENTICAL graph, so builders/assay come from
    the run's nodes.json snapshot on resume — never a fresh fleet.json read."""
    @executor(id="route")
    async def route_node(msg: dict, ctx: WorkflowContext[dict]) -> None:
        routing = ({"skipped": "explicit local builders"} if target_meta.get("operator") in ("hermes", "jev")
                   else await route_plan(plan_id, plan_text))
        log.info("[%s] route: %s",plan_id, routing.get("difficulty","?")+"/"+str(routing.get("recommended_runner")) if isinstance(routing,dict) else routing)
        await ctx.send_message({"routing":routing})
    def _mk(name, host):
        @executor(id="build_"+name)
        async def _build(msg: dict, ctx: WorkflowContext[list[dict] if len(builders) == 1 else dict]) -> None:
            n,entry=await build_one(name,host,plan_id,plan_text,runner_preset=target_meta.get("runner_preset"),
                                    max_age_s=target_meta.get('max_age_s') if target_meta.get('operator') in ('hermes', 'jev') else None)
            item = {"worker":n,"entry":entry,"routing":msg.get("routing")}
            await ctx.send_message([item] if len(builders) == 1 else item)
        return _build
    build_execs=[_mk(n,h) for n,h in builders]
    @executor(id="finalize")
    async def finalize(res: list[dict], ctx: WorkflowContext[None, dict]) -> None:
        results={r["worker"]:r["entry"] for r in res}
        routing=next((r.get("routing") for r in res if r.get("routing") is not None),{})
        winner=None; assay_res={}
        if assay:
            assay_res=await run_assay(assay[0],assay[1],plan_id,[b[0] for b in builders]); winner=assay_res.get("winner")
            tb = ({"method": "human-review", "tied": _tied_top(assay_res), "winner": None}
                  if target_meta.get("operator") in ("hermes", "jev") else await _tiebreak(plan_id, plan_text, assay_res))
            if tb:
                assay_res["tiebreak"]=tb
                if tb.get("winner"): winner=tb["winner"]
        questions=await asyncio.to_thread(_scan_questions, plan_id, [b[0] for b in builders])   # L2
        promotion=await _promote(plan_id, winner, assay_res, questions, target_meta, results)
        await ctx.yield_output({"routing":routing,"builds":results,"assay":assay_res,"winner":winner,
                                "questions":questions,"promotion":promotion,"target":target_meta})
    wb=WorkflowBuilder(name=plan_id, start_executor=route_node, checkpoint_storage=storage)
    if len(build_execs) == 1:
        wb.add_edge(route_node, build_execs[0])
        wb.add_edge(build_execs[0], finalize)
    else:
        wb.add_fan_out_edges(route_node, build_execs)
        wb.add_fan_in_edges(build_execs, finalize)
    return wb.build()

async def run_workflow(plan_id, plan_text):
    target_meta, plan_body = _extract_ccmeta(plan_text)
    if plan_id.startswith(("hermes-", "hearth-hermes-")) and target_meta.get("operator") not in ("hermes", "jev"):
        raise ValueError("Hermes-tagged run is missing mandatory policy")
    plan_text=plan_body
    if "blocked-on-ambiguity" not in plan_text: plan_text=plan_text+QPROTO   # L2 (idempotent, resume-safe)
    run_dir=RUNS/plan_id; run_dir.mkdir(parents=True,exist_ok=True)
    snap=run_dir/"nodes.json"
    if snap.exists():   # crashed mid-run earlier: reuse the node snapshot so the graph matches its checkpoints
        s=json.loads(snap.read_text()); builders=[tuple(b) for b in s["builders"]]; assay=tuple(s["assay"]) if s.get("assay") else None
        target_meta = persisted_target(s, target_meta)
    else:
        builders, assay = load_nodes()
        builders=_select_builders(builders, target_meta)
        if not builders: log.error("no build workers available"); return {"error":"no builders"}
        if target_meta.get("operator") in ("hermes", "jev") and sorted(b[0] for b in builders) != sorted(target_meta["builders"]):
            raise ValueError("qualified pair unavailable; no substitutions")
        write_snapshot(snap, builders, list(assay) if assay else None, target_meta)
    storage=FileCheckpointStorage(run_dir/"checkpoints")
    with TR.start_as_current_span("maf.workflow.plan_build_assay", attributes={"plan.id":plan_id}) as root:
        rtid=format(root.get_span_context().trace_id,"032x")
        log.info("[%s] start trace=%s builders=%s assay=%s",plan_id,rtid,[b[0] for b in builders],assay[0] if assay else None)
        result=None
        cps=await storage.list_checkpoints(workflow_name=plan_id)
        if cps:   # resume at the last completed superstep instead of re-running finished builds
            latest=sorted(cps,key=lambda c:c.timestamp)[-1]
            log.info("[%s] resuming from checkpoint %s (%d found)",plan_id,latest.checkpoint_id,len(cps))
            try:
                result=await _workflow_for(plan_id,plan_text,builders,assay,storage,target_meta).run(checkpoint_id=latest.checkpoint_id)
                if not result.get_outputs(): result=None   # checkpoint was already past the yield: run clean
            except Exception as e:
                log.warning("[%s] resume failed (%s) — fresh run",plan_id,str(e)[:150]); result=None
        if result is None:
            result=await _workflow_for(plan_id,plan_text,builders,assay,storage,target_meta).run({"plan_id":plan_id})
        outs=result.get_outputs()
        body=outs[-1] if outs else {"error":"workflow yielded no output"}
    out={"plan_id":plan_id,"trace_id":rtid,**body}
    (run_dir/"result.json").write_text(json.dumps(out,indent=2))
    log.info("[%s] DONE winner=%s promoted=%s",plan_id,out.get("winner"),(out.get("promotion") or {}).get("promoted")); force_flush(); return out
async def _promote(plan_id, winner, assay_res=None, questions=None, target_meta=None, builds=None):
    """imp06: fast-forward the winning branch into farmer-repo main (refuse on divergence).
    Health-gated: an all-zero/empty scoreboard means the ASSAY failed, not the
    candidates — a "winner" from that is fleet-order noise and must never merge
    (seen live 2026-07-01: assay node lost farmer access, every run graded F/0,
    and order-fallback winners fast-forwarded ungated).
    L2-gated: a winner with a pending QUESTION.md is asking, not done — never merge.
    L4-gated: grade says how good, risk says how safe. 'high' risk holds for the
    operator (board approve releases it); missing risk data counts as high."""
    meta = validate_run_policy(target_meta or {})
    if plan_id.startswith(("hermes-", "hearth-hermes-")) and meta.get("operator") not in ("hermes", "jev"):
        raise ValueError("Hermes promotion policy missing")
    if meta.get("promotion_policy") == "manual":
        return await asyncio.to_thread(review_result, meta, plan_id, winner, builds or {}, FARMER_REPO)
    if not winner or _promote_winner is None: return None
    wb=f"ccfarm/{plan_id}/{winner}/lap1"
    if assay_res is not None:
        sb=assay_res.get("scoreboard") or []
        if not sb or all((s.get("score") or 0)==0 for s in sb):
            log.warning("[%s] promote REFUSED: assay unhealthy (empty/all-zero scoreboard) — not promoting order-fallback winner %s",plan_id,winner)
            return {"promoted":False,"reason":"assay-unhealthy","scoreboard_size":len(sb),"winner_branch":wb}
    if any((q or {}).get("worker")==winner for q in (questions or [])):
        log.info("[%s] promote deferred: winner %s has a pending question on the board",plan_id,winner)
        return {"promoted":False,"reason":"question-pending","winner_branch":wb}
    risk=None
    if _risk_compute is not None:
        try:
            risk=await asyncio.to_thread(_risk_compute, str(FARMER_REPO), wb, "main",
                                         runs_dir=str(RUNS), worker=winner)
        except Exception as e:
            risk={"score":100,"level":"high","factors":["risk compute failed: "+str(e)[:120]]}
        if risk.get("level")=="high":
            hold={"plan_id":plan_id,"winner":winner,"branch":wb,"risk":risk,"status":"pending",
                  "ts":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
            hf=BASE/"board"/"holds.ndjson"; hf.parent.mkdir(parents=True,exist_ok=True)
            with hf.open("a") as f: f.write(json.dumps(hold)+"\n")
            log.warning("[%s] promote HELD for operator (risk %s): %s",plan_id,risk.get("score"),
                        "; ".join(risk.get("factors") or [])[:200])
            return {"promoted":False,"reason":"risk-hold","risk":risk,"winner_branch":wb}
    async with PROMOTE_LOCK:
        try:
            with TR.start_as_current_span("promote",attributes={"plan.id":plan_id,"winner":winner}):
                r=_promote_winner(str(FARMER_REPO), wb, "main", mode="ff-only")
            if risk is not None: r["risk"]=risk
            log.info("[%s] promote: %s",plan_id,r.get("notes")); return r
        except Exception as e:
            log.warning("[%s] promote refused/failed: %s",plan_id,str(e)[:160])
            return {"promoted":False,"error":str(e)[:200],"winner_branch":wb}
async def serve():
    INBOX.mkdir(parents=True,exist_ok=True); (INBOX/"processed").mkdir(exist_ok=True)
    log.info("cc-conductor-maf serve loop started - inbox %s (max_in_flight=%s)",INBOX,MAX_IN_FLIGHT)
    _wd=None
    if Watchdog is not None:
        try: _wd=Watchdog(); await _wd.start()        # sd_notify READY=1 + periodic WATCHDOG=1 (no-op if not under systemd)
        except Exception as _e: log.warning("watchdog start failed (non-fatal): %s",str(_e)[:120])
    sem=asyncio.Semaphore(MAX_IN_FLIGHT); inflight=set(); tasks=set()   # imp07: bounded concurrency
    async def _process(pf, plan_id):
        async with sem:
            log.info("inbox: picked up %s",pf.name)
            try:
                await run_workflow(plan_id, pf.read_text())
            except Exception as e:
                log.error("workflow %s errored (isolated): %s",plan_id,str(e)[:200])
                # Terminal failures must not look like eternally running work.
                failed = RUNS/plan_id/'result.json'
                if not failed.exists():
                    failed.parent.mkdir(parents=True, exist_ok=True)
                    failed.write_text(json.dumps({'plan_id':plan_id,'status':'error',
                        'error':type(e).__name__, 'promotion':{'promoted':False,
                        'reason':'workflow-failed'}},indent=2))
            finally:
                try: pf.rename(INBOX/"processed"/pf.name)
                except Exception: pass
                inflight.discard(plan_id)
    while True:
        try:
            if _backlog_pump is not None:   # L1: backlog head flows into inbox while WIP room exists
                try:
                    pulled=_backlog_pump(INBOX, MAX_IN_FLIGHT-len(inflight))
                    if pulled: log.info("backlog: pulled %s into inbox",", ".join(pulled))
                except Exception as e:
                    log.warning("backlog pump failed (non-fatal): %s",str(e)[:120])
            for pf in sorted(INBOX.glob("*.md")):
                plan_id=pf.stem
                if plan_id in inflight: continue
                if (RUNS/plan_id/"result.json").exists():
                    try: pf.rename(INBOX/"processed"/pf.name)
                    except Exception: pass
                    continue
                inflight.add(plan_id)
                task=asyncio.create_task(_process(pf, plan_id))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except Exception as e:
            log.error("serve loop error (continuing): %s",str(e)[:200])
        await asyncio.sleep(SCAN)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--serve",action="store_true"); ap.add_argument("--plan-id"); ap.add_argument("--plan")
    a=ap.parse_args()
    if a.serve: asyncio.run(serve())
    elif a.plan and a.plan_id: print(json.dumps(asyncio.run(run_workflow(a.plan_id,a.plan)),indent=2)[:1600])
    else: ap.print_help()
if __name__=="__main__": main()
