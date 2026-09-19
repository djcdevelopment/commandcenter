# Retro (hearth-hermes-br-20260919-215719-70852cc4-5f05200a-cc-builder-2)

Built by agent_openai on model am4-dense-27b (completion: budget, 20 steps, 405s).
exit=0
    def test_stale_readiness_cannot_place_work_on_am4(self) -> None:
        result = propose_schedule([
            {"plan_id": "a", "task_class": "inference",
             "eligible_models": [{"backend": "am4-ollama", "model_id": "qwen2.5:14b"}]}
        ], resource_snapshot=_snapshot(age_s=90))
        self.assertFalse(result["ok"])
        self.assertFalse(next(m for m in result["machin

<!-- agent_openai completion: reason=budget steps=20 elapsed_s=405.0 model=am4-dense-27b -->
