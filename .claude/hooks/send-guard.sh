#!/usr/bin/env bash
# Claude Code PreToolUse(mcp__.*) hook — gtm-engine
#
# Prevents autonomous outbound messaging, publishing, or sequence enrollment from the desktop.
# PRD §3.A9, §8-B, R-3.
#
# Inspects tool leaf name (last segment after '__') against send-leaves.txt.
# If denied, exits 2 with a plain refusal message.

set -uo pipefail

PY="$( [ -x /usr/bin/python3 ] && echo /usr/bin/python3 || command -v python3 || true)"
[ -z "$PY" ] && exit 0

EVENT=$(cat)

TOOL_NAME=$(printf '%s' "$EVENT" | "$PY" -c \
  "import sys,json; p=json.load(sys.stdin); print(p.get('tool_name') or p.get('toolCall',{}).get('name') or '')" \
  2>/dev/null || true)

[ -z "$TOOL_NAME" ] && exit 0

# Only inspect MCP tools
case "$TOOL_NAME" in
  mcp__*) ;;
  *) exit 0 ;;
esac

# Leaf is the last segment after '__'
LEAF="${TOOL_NAME##*__}"
[ -z "$LEAF" ] && exit 0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LEAVES_FILE="$SCRIPT_DIR/send-leaves.txt"
PREFIXES_FILE="$SCRIPT_DIR/send-prefixes.txt"

DENIED=0
ASK=0
MISSING_LIST=0

if [ -f "$LEAVES_FILE" ] && [ -s "$LEAVES_FILE" ]; then
  if grep -Fxq "$LEAF" "$LEAVES_FILE"; then
    DENIED=1
  fi
else
  MISSING_LIST=1
  case "$LEAF" in
    send_message|reply|forward|slack_send_message|slack_schedule_message|reply_to_email|create_post|publish_clip|add_leads_to_sequence|import_prospects_to_sequence|add_dnc_items)
      DENIED=1
      ;;
  esac
fi

# Prefix families (generated from agent.permissions). Exact leaves above win, so the two
# exact enrolment verbs stay denied; `deny` = activation/status verbs (sending is a person in
# the provider UI); `ask` = enrolment variants a hosted connector adds, which the operator
# may run on purpose but must approve each time. Missing file: fail closed on both families.
if [ "$DENIED" -eq 0 ]; then
  if [ -f "$PREFIXES_FILE" ] && [ -s "$PREFIXES_FILE" ]; then
    while read -r decision prefix; do
      [ -z "$prefix" ] && continue
      case "$LEAF" in
        "$prefix"*)
          if [ "$decision" = "ask" ]; then ASK=1; else DENIED=1; fi
          break
          ;;
      esac
    done < "$PREFIXES_FILE"
  else
    case "$LEAF" in
      activate_*|resume_*|update_sequence_status*|add_leads_*|import_prospects_*) DENIED=1 ;;
    esac
  fi
fi

if [ "$DENIED" -eq 1 ]; then
  if [ "$MISSING_LIST" -eq 1 ]; then
    >&2 echo "The engine never sends, enrolls or posts from here. Draft it; the person sends it. (send-leaves.txt is missing or empty)"
  else
    >&2 echo "The engine never sends, enrolls or posts from here. Draft it; the person sends it."
  fi
  exit 2
fi

if [ "$ASK" -eq 1 ]; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"This enrolls people into an email sequence (their details go to the sequencer). Approve only if you asked for this import."}}'
  exit 0
fi

exit 0
