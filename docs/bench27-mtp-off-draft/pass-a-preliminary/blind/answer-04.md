The day and tool-night configurations in host/lab-configurations.toml share identical omen-dense-27b and omen-perception values; the AM4 profile switch moves am4-vllm from live to absent and the two AM4 tool seats from absent to live. sizing_map.py audits only the active configuration's live/absent expectations and does not cross-check day against tool-night or the lab-config values against its sizing invariants.

## Configuration comparison

The report compares the backends declared under configuration.day and configuration.tool-night in host/lab-configurations.toml against the sizing parameters and invariants in tools/ops/sizing_map.py. In configuration.day, omen-dense-27b is live with context_tokens 65536, parallel_slots 2, and max_tokens 16384, while omen-perception is live with parallel_slots 2 and no context_tokens or max_tokens. configuration.tool-night repeats those exact values for both backends. The AM4 profile changes from dense-tp2 to tool-pair, so am4-vllm is live in day and absent in tool-night, while am4-tool-4070ti and am4-tool-5070 are absent in day and live in tool-night.
> omen-dense-27b = { status = "live", context_tokens = 65536, parallel_slots = 2, max_tokens = 16384 } (host/lab-configurations.toml:17-17)
> omen-perception = { status = "live", parallel_slots = 2 } (host/lab-configurations.toml:22-22)
> am4-vllm = { status = "live", context_tokens = 16384, parallel_slots = 1, max_tokens = 4096 } (host/lab-configurations.toml:18-18)
> am4-tool-4070ti = { status = "absent" } (host/lab-configurations.toml:19-19)
> am4-tool-5070 = { status = "absent" } (host/lab-configurations.toml:20-20)
> am4-vllm = { status = "absent" } (host/lab-configurations.toml:33-33)
> am4-tool-4070ti = { status = "live", context_tokens = 24576, parallel_slots = 3, max_tokens = 4096 } (host/lab-configurations.toml:34-34)
> am4-tool-5070 = { status = "live", context_tokens = 16384, parallel_slots = 1, max_tokens = 6144 } (host/lab-configurations.toml:35-35)

## Declared values

For omen-dense-27b, both configurations declare context_tokens 65536, parallel_slots 2, and max_tokens 16384. For omen-perception, both configurations declare parallel_slots 2; the TOML entries for omen-perception do not include context_tokens or max_tokens.
> omen-dense-27b = { status = "live", context_tokens = 65536, parallel_slots = 2, max_tokens = 16384 } (host/lab-configurations.toml:17-17)
> omen-perception = { status = "live", parallel_slots = 2 } (host/lab-configurations.toml:22-22)

## Live/absent changes and invariant coverage

When the lab moves from day to tool-night, am4-vllm changes from live to absent, and am4-tool-4070ti and am4-tool-5070 change from absent to live; omen-vllm, omen-dense-27b, fx99-vllm, and omen-perception remain live in both. sizing_map.py does not compare day against tool-night: its configuration invariants read the active configuration and check only that expected live backends are serving and expected absent backends are not routable. The omen-dense-27b sizing invariants compare backends-linux.toml values to seat parameters, not the lab-config values, and the omen-perception invariant checks images_per_second and max_image_bytes from backends-linux.toml rather than the lab-config parallel_slots.
> am4-vllm = { status = "live", context_tokens = 16384, parallel_slots = 1, max_tokens = 4096 } (host/lab-configurations.toml:18-18)
> am4-tool-4070ti = { status = "absent" } (host/lab-configurations.toml:19-19)
> am4-tool-5070 = { status = "absent" } (host/lab-configurations.toml:20-20)
> am4-vllm = { status = "absent" } (host/lab-configurations.toml:33-33)
> am4-tool-4070ti = { status = "live", context_tokens = 24576, parallel_slots = 3, max_tokens = 4096 } (host/lab-configurations.toml:34-34)
> am4-tool-5070 = { status = "live", context_tokens = 16384, parallel_slots = 1, max_tokens = 6144 } (host/lab-configurations.toml:35-35)
> active_cfg = _val(rows, "active configuration") (tools/ops/sizing_map.py:664-664)
> if active_cfg is not None and not str(active_cfg).startswith("unprobed"): (tools/ops/sizing_map.py:665-665)
