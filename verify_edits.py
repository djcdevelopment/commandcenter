import re

# Read CAPABILITY-ROADMAP.html
with open('CAPABILITY-ROADMAP.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Check each target with more robust pattern matching
assert re.search(r'Amendment 1', content), 'Amendment 1 sentence not found in CAPABILITY-ROADMAP.html'
assert re.search(r'Decision dimensions', content), 'Amendment 2 block title not found in CAPABILITY-ROADMAP.html'
assert re.search(r'New scheduler decision dimensions carry the same three obligations', content), 'New guardrail Rule text not found in CAPABILITY-ROADMAP.html'
assert re.search(r're-derivation pending', content), 're-derivation pending not found in CAPABILITY-ROADMAP.html'
assert re.search(r'shipped 2026-07-02', content), 'shipped 2026-07-02 not found in CAPABILITY-ROADMAP.html'

# Read TWO-ECONOMIES-WIND-TUNNEL.html
with open('TWO-ECONOMIES-WIND-TUNNEL.html', 'r', encoding='utf-8') as f:
    content = f.read()

assert re.search(r'SUPERSEDED by \u03941\'', content), 'SUPERSEDED by \u03941\' not found in TWO-ECONOMIES-WIND-TUNNEL.html'

print('All edits verified successfully!')