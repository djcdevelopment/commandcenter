"""hearth.health — the guard dog's coherence spells (Watchfire/Flare).

Cross-source correlation gap-checks. `gaps` holds the pure, IO-free rules (the
spellbook); the on-demand `preflight` HEARTH tool (in hearth.toolsurface) and,
later, the scheduled watchdog both cast them.
"""
