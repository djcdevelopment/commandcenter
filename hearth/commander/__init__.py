"""Commander intent lane — the commander issues intent, mechnet carries it.

Slice 1 (REFINE): a local refine<->critique convergence loop. The commander says
"refine & review this a bunch of times"; a local author model drafts, local
critic model(s) review, and the draft iterates until the critics stop finding
material issues (or a round budget is spent). No frontier model in the loop —
every turn runs on OMEN's local models via hearth.toolsurface.inference.

BUILD and BOTH modes are deliberately deferred (BUILD already exists as the
fleet lane, hearth.toolsurface.task_lane); this package starts with the net-new
capability. See POUR/campaign artifacts and project-idle-speculation-campaign.
"""
