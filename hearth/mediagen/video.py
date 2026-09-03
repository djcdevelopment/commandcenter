import json
import asyncio
from hearth.schemas.validate import validate
from hearth.observation.telemetry import trace_span

def generate_visual_storyboard(document_text: str) -> dict:
    from hearth.callers.local import call_local_qwen
    from hearth.prompts import load_template

    prompt = load_template("visual_storyboard_v1.jinja").render(document_text=document_text)
    
    with trace_span("hearth.job.storyboard.generate"):
        response = call_local_qwen(
            messages=[
                {"role": "system", "content": "You are a storyboard artist and director."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4
        )
        try:
            contract = json.loads(response)
            validate(contract, schema_id="mediagen.visual-storyboard.v1")
            return contract
        except Exception as e:
            raise ValueError(f"Failed to generate valid storyboard contract: {e}\nResponse: {response}") from e
