"""Structured, four-part operator-facing refusal copy.

Every stop says:
  1. What was stopped / refused (what)
  2. Why it was stopped (why)
  3. What the operator can do next (next_step)
  4. What it cost, if anything (cost)
  5. Optionally an alternative (alternative)
  6. Technical detail for <details> (technical)

render() produces:
  "{what} That's because {why}. You can {next_step}[, or {alternative}]. {cost}"
render() NEVER raises.
"""

from __future__ import annotations

from dataclasses import dataclass


def _format_sentence(text: str) -> str:
    s = text.strip()
    if not s:
        return ""
    s = s.rstrip(".")
    if s.endswith(("!", "?")):
        return s
    return s + "."


@dataclass(frozen=True)
class Refusal:
    what: str
    why: str
    next_step: str
    alternative: str | None = None
    cost: str = "Nothing was spent."
    technical: str | None = None

    def render(self) -> str:
        try:
            parts: list[str] = []
            what_str = str(self.what or "").strip()
            if what_str:
                parts.append(_format_sentence(what_str))
            why_str = str(self.why or "").strip()
            if why_str:
                why_clause = why_str.rstrip(".!?")
                parts.append(_format_sentence(f"That's because {why_clause}"))
            next_str = str(self.next_step or "").strip()
            alt_str = str(self.alternative or "").strip() if self.alternative else ""
            if next_str and alt_str:
                next_clause = next_str.rstrip(".!?")
                alt_clause = alt_str.rstrip(".!?")
                parts.append(_format_sentence(f"You can {next_clause}, or {alt_clause}"))
            elif next_str:
                next_clause = next_str.rstrip(".!?")
                parts.append(_format_sentence(f"You can {next_clause}"))
            elif alt_str:
                alt_clause = alt_str.rstrip(".!?")
                parts.append(_format_sentence(f"You can {alt_clause}"))
            cost_str = str(self.cost or "").strip()
            if cost_str:
                parts.append(_format_sentence(cost_str))
            out = " ".join(parts).strip()
            return out or "I stopped. Nothing was spent."
        except Exception:
            return "I stopped. Nothing was spent."

    def details(self) -> str:
        try:
            if not self.technical:
                return ""
            tech = str(self.technical).strip()
            return f"<details><summary>Details</summary>\n\n{tech}\n</details>"
        except Exception:
            return ""
