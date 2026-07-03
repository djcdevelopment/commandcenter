# Retro (pour-a2-cc-builder-2)

Built by agent_openai on model vllama-planner (completion: budget, 7 steps, 405s).
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.workflow.project_capacity import (
    classify_known_bad,
    classify_known_good,
    collect_event_files,
    extract_observations,
    extract_scheduler_decisions,
    reduce_capacity,
    reduce_prediction_comparisons,
    _combo_key,
    _confidence,
)
from tools.workflow.project_state import

<!-- agent_openai completion: reason=budget steps=7 elapsed_s=405.0 model=vllama-planner -->
