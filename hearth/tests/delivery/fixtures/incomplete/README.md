# Replay fixtures: suspected-incomplete text (audit 2026-10-16)

Raw `delivery-output.json` of three cap-3 night deliveries whose paragraph text ended where the model wrote a literal
double quote under the constrained-output schema (night-02 a83187bb on am4-vllm; night-04 25fd75d0 and night-07
921d1a59 on omen-dense-27b-b), their brief.v2 objects, and the pinned sources concatenated as one file. The balanced
cases (legitimate endings, escaped quotes, complete short answers) live in hearth/tests/delivery/test_incomplete.py.
Nothing here is a repair: the detector reports a suspicion with its evidence.
