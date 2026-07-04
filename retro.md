# Retro (pour-ga-am4-worker-1)

Built by agent_openai on model vllama-planner (completion: budget, 8 steps, 405s).
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.workflow.project_capacity import (
    KNOWN_BAD_MIN_FAILURES,
    KNOWN_GOOD_MIN_SUCCESS_RATE,
    collect_event_files,
    extract_observations,
    extract_scheduler_decisions,
)
from tools.workflow.project_findings import (
    BIAS_CONSISTENCY,
    HIGH_CONFIDENCE_MIN_SAMPLES,
    _confidence_

<!-- agent_openai completion: reason=budget steps=8 elapsed_s=405.0 model=vllama-planner -->
