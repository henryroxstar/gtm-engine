#!/usr/bin/env bash
# Claude Code UserPromptSubmit hook — gtm-engine
#
# Prevents accidental pasting of API keys, tokens, and passwords into chat prompts.
# Wording & contract: PRD §3.A9, R-2.
#
# If a secret shape is detected, the prompt is blocked with exit code 2 and a plain
# guidance message is written to stderr. The secret token is NEVER echoed or logged.

set -uo pipefail

PY="$( [ -x /usr/bin/python3 ] && echo /usr/bin/python3 || command -v python3 || true)"
[ -z "$PY" ] && exit 0

EVENT=$(cat)

# Extract prompt text
PROMPT=$(printf '%s' "$EVENT" | "$PY" -c \
  "import sys,json; p=json.load(sys.stdin); print(p.get('prompt') or p.get('user_input') or p.get('text') or '')" \
  2>/dev/null || true)

[ -z "$PROMPT" ] && exit 0

# Check secret patterns via python for reliable multi-pattern matching without shell escaping issues
IS_SECRET=$(printf '%s' "$PROMPT" | "$PY" -c "
import sys, re

text = sys.stdin.read()

patterns = [
    r'sk-ant-api03-[A-Za-z0-9_-]{20,}',
    r'sk-ant-[A-Za-z0-9_-]{20,}',
    r'ghp_[A-Za-z0-9]{20,}',
    r'gh[pousr]_[A-Za-z0-9]{20,}',
    r'AIza[0-9A-Za-z-_]{35}',
    r'(?i)\bbearer\s+ey[A-Za-z0-9_.-]{20,}',
    r'(?i)(?:password|secret|api_key|token)\s*[:=]\s*[\'\"]?[^\s\'\"]{6,}',
    r'sk-[A-Za-z0-9_-]{30,}',
    r'xox[baprs]-[0-9A-Za-z-]{20,}',
    r'dop_v1_[a-f0-9]{64}',
]

for pat in patterns:
    if re.search(pat, text):
        sys.exit(1)
sys.exit(0)
" 2>/dev/null && echo "clean" || echo "secret")

if [ "$IS_SECRET" = "secret" ]; then
  >&2 echo "That looks like an API key or password. It belongs in a settings file or a connector, not in chat. Paste it into your private .env file or Settings → Connectors instead."
  exit 2
fi

exit 0
