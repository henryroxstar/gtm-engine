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

exec "$PY" -c '
import json
import re
import sys

raw = sys.stdin.read()
if not raw.strip():
    sys.exit(0)

try:
    p = json.loads(raw, strict=False)
except Exception:
    p = None

text = ""
if isinstance(p, dict):
    text = p.get("prompt") or p.get("user_input") or p.get("text") or ""
elif isinstance(p, str):
    text = p
elif p is None:
    text = raw
else:
    sys.exit(1)

if not text:
    sys.exit(0)

patterns = [
    r"sk-ant-api03-[A-Za-z0-9_-]{20,}",
    r"sk-ant-[A-Za-z0-9_-]{20,}",
    r"ghp_[A-Za-z0-9]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"AIza[0-9A-Za-z-_]{35}",
    r"(?i)(?:\bbearer\s+ey[A-Za-z0-9_.-]{20,}|\beyJ[A-Za-z0-9_.-]{20,})",
    r"""(?i)["\x27]?(?:password|secret|api_key|token)["\x27]?\s*[:=]\s*(?:["\x27][^"\x27]{6,}["\x27]|[^\s"\x27{]{6,})""",
    r"sk-[A-Za-z0-9_-]{30,}",
    r"xox[baprs]-[0-9A-Za-z-]{20,}",
    r"(?i)(?:dop_v1_[a-f0-9]{64}|dp\.(?:st|pt|ct)\.[A-Za-z0-9_.-]{20,})",
]

for pat in patterns:
    if re.search(pat, text):
        sys.stderr.write(
            "That looks like an API key or password. It belongs in a settings file or a connector, not in chat. Paste it into your private .env file or Settings \u2192 Connectors instead.\n"
        )
        sys.exit(2)

if p is None:
    sys.exit(1)

sys.exit(0)
'

