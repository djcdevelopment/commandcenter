# Retro (hearth-spec-bankedfire-candidate-source-9f380bd9-omen-worker-1)

Built by agent_openai on model qwen3-coder:30b (completion: max_steps, 24 steps, 129s).
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from tools.workflow.project_capacity import collect_event_files, materialize_knowledge
from tools.workflow.reference_runner import SCENARIOS, run_reference_workflow


def process_work_item(work_item: Path, runs_root: Path, archive: bool, scenario: str,
                      policy_path: Path | N

<!-- agent_openai completion: reason=max_steps steps=24 elapsed_s=128.6 model=qwen3-coder:30b -->
