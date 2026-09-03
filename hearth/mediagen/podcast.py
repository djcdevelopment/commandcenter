"""Podcast vertical slice implementation."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
from jinja2 import Environment, FileSystemLoader
from kokoro import KPipeline

from hearth.execution.ids import new_invocation_id
from hearth.imagegen.session import ImageSessionController
from hearth.observation.telemetry import trace_span
from hearth.schemas.validate import validate

ARC_CHAT = "http://127.0.0.1:8082/v1/chat/completions"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
OUTPUT_DIR = Path(r"E:\omen\imagegen\data\outputs")

# Lazy pipeline singleton
_pipeline: Optional[KPipeline] = None


def _get_pipeline() -> KPipeline:
    global _pipeline
    if _pipeline is None:
        # Load the English pipeline
        # Kokoro is lightweight so CPU is fine and very fast.
        _pipeline = KPipeline(lang_code='a', device='cpu')
    return _pipeline


def _query_arcserve(messages: list[dict], max_tokens: int = 4096) -> str:
    """Send a chat completion request to the local ArcServe model."""
    token = ImageSessionController._arc_token()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        
    payload = {
        "model": "qwen3-30b-a3b",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"}
    }
    
    req = urllib.request.Request(ARC_CHAT, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ArcServe request failed. Is the LLM running? {exc}") from exc


def generate_podcast_script(document_text: str) -> dict:
    """Generate the script contract via LLM."""
    doc_sha = hashlib.sha256(document_text.encode("utf-8")).hexdigest()
    
    env = Environment(loader=FileSystemLoader(PROMPTS_DIR))
    template = env.get_template("podcast_script_v1.jinja")
    prompt = template.render(document_text=document_text, document_sha256=doc_sha)
    
    messages = [
        {"role": "system", "content": "You are a senior technical podcast producer. Always output strictly valid JSON."},
        {"role": "user", "content": prompt}
    ]
    
    with trace_span("hearth.mediagen.script_generation", attributes={"doc.sha256": doc_sha}) as span:
        response_text = _query_arcserve(messages)
        try:
            contract = json.loads(response_text)
        except json.JSONDecodeError as exc:
            span.set_attribute("error", "json_decode")
            raise ValueError(f"LLM did not return valid JSON: {response_text}") from exc
            
        span.set_attribute("contract.title", contract.get("title", ""))
        span.set_attribute("contract.turns", len(contract.get("turns", [])))
        
        # Enforce Creator OS authority boundary
        validate(contract, schema_id="mediagen.podcast-script.v1")
        return contract


def synthesize_podcast(contract: dict, output_path: Path) -> dict:
    """Synthesize the script contract into an audio artifact."""
    with trace_span("hearth.mediagen.audio_synthesis", attributes={"script.title": contract.get("title")}) as span:
        pipeline = _get_pipeline()
        
        all_audio = []
        sample_rate = 24000
        turns = contract.get("turns", [])
        speakers = contract.get("speakers", {})
        
        for turn_idx, turn in enumerate(turns):
            speaker_key = turn["speaker"]
            text = turn["text"]
            pause_ms = turn.get("pause_after_ms", 250)
            
            # Resolve voice_id from speaker definition
            voice_id = speakers[speaker_key].get("voice_id", "af_heart")
            
            # Synthesize turn (Kokoro yields chunks)
            generator = pipeline(text, voice=voice_id, speed=1.0)
            for _, _, audio in generator:
                all_audio.append(audio)
                
            # Add silence
            if pause_ms > 0:
                silence_samples = int((pause_ms / 1000.0) * sample_rate)
                all_audio.append(np.zeros(silence_samples, dtype=np.float32))
                
        # Concatenate and save
        if not all_audio:
            raise ValueError("No audio generated.")
            
        final_audio = np.concatenate(all_audio)
        
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), final_audio, sample_rate)
        
        # Calculate duration
        duration_s = len(final_audio) / sample_rate
        span.set_attribute("audio.duration_s", duration_s)
        span.set_attribute("audio.sample_rate", sample_rate)
        
        return {
            "duration_seconds": duration_s,
            "sample_rate": sample_rate,
            "channels": 1,
            "file_size_bytes": output_path.stat().st_size,
            "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        }
