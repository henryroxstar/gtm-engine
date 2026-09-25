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
                parts.append(what_str.rstrip(".") + ".")
            why_str = str(self.why or "").strip()
            if why_str:
                parts.append(f"That's because {why_str.rstrip('.')}.")
            next_str = str(self.next_step or "").strip()
            if next_str:
                if self.alternative and str(self.alternative).strip():
                    alt_str = str(self.alternative).strip().rstrip(".")
                    parts.append(f"You can {next_str.rstrip('.')}, or {alt_str}.")
                else:
                    parts.append(f"You can {next_str.rstrip('.')}.")
            cost_str = str(self.cost or "").strip()
            if cost_str:
                parts.append(cost_str.rstrip(".") + ".")
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
