# Fleet Protocol Ambiguity

The current fleet protocol implementation is blocked on how to handle the missing fields in the intake payload. According to POUR-PLAN.md, the current intake format only supports plain markdown bodies and filename stems, but the system requires additional fields for:

- target repo URL
- target branch (e.g., stream/A1, stream/A2, etc.)
- GitHub credentials for a private repo
- per-run events.jsonl and ontology artifacts

The question is: How should the intake payload be structured to include these required fields while maintaining compatibility with the existing system? Should we extend the current markdown format with front matter, or should we introduce a new structured format (e.g., JSON) for the intake payloads?