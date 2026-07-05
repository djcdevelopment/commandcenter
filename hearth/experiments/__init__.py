"""Experiments — matrix dataset generation across the mechnet's hardware.

matrix.py sweeps (planner x critic x prompt x laps x ordering) cells, each a
run_refine planner<->critic loop routed across boxes (AM4 B70s via am4-oxen,
OMEN via ollama), and scores each result with a held-out critic panel. The
memory-safe model residency (AM4 vllama up/down through its 22GB + verdict
gates) is driven by residency.py — never bypassing those gates.
"""
