Deliver a short source-grounded engineering note (maximum 450 words) for the
fleet-capacity correction. This is real task work, not a synthetic probe.

Use HEARTH to read these two exact files, once each:
C:/work/commandcenter-hermes-fx99/fleet/inventory.toml (max_bytes 22000)
C:/work/commandcenter-hermes-fx99/knowledge/am4_gpu_catalog.json (max_bytes 14000)
Then query_knowledge with host="am4", topic="capacity", limit=2,
knowledge_dir="C:/work/commandcenter/knowledge".

Fresh operator observations (2026-09-19): AM4 has TWO physical cards, RTX5070
(GPU-a1f65cc0-44d9-7854-6785-7d93e686da2f) and RTX4070Ti
(GPU-dafbdbfc-23af-0c97-112d-dc17695c2aa8). Its native Dense27B server is READY
at :18090 with n_ctx=131072, total_slots=1, CUDA q4_0 KV. Its old managed :18084
service and Ollama are stopped. Facade alias am4-dense-27b now maps to :18090.
Primary Hermes, compression, and builders using this alias share ONE physical
slot. OMEN keeps eight production slots unchanged. FX99's controller uses CPU,
not a new GPU model. A current /api/ps read showed coder7B resident on FX99;
earlier Mistral residency was stale, so do not assert current residency from docs.

Identify the stale fields, the smallest code/catalog correction, and three
specific new regression tests. Explicitly separate fresh observations from
source facts and unmeasured performance. No dispatch, shell, writes or model
rotations yet. Finish your note after these three tool calls; do not reread or
expand scope. The final answer is the deliverable and will be saved verbatim.
