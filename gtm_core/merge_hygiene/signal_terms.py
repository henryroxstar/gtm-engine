from __future__ import annotations

import re

# --- signal relevance ----------------------------------------------------
#
# A THIRD concern, separate from both :func:`signal_clause` (is the value well-formed?)
# and :func:`signal_is_fresh` (is it recent?). A clause can be perfectly formed, dated and
# recent, and still be the wrong clause to open THIS body on.
#
# Found 2026-08-19 spot-checking the Run-500 send: 101 of 453 live rows (22%) carried a
# clause with no AI/agent/automation content at all, under a body whose second paragraph
# claims "agents acting on regulated records at {{Company}}". "Relay Partners advises
# fintech and wealth management firms on their sale transactions." followed by an agent
# governance claim is a non-sequitur the recipient reads as a template that ignored what
# it just said, which is the most reliable "this is generated" tell in the campaign.
#
# The terms are a PARAMETER, not a tenant fact: this module stays company-agnostic, and a
# campaign about something other than agents passes its own vocabulary. The default is the
# agentic-AI vocabulary because that is the shape every current pack sells into.

SIGNAL_TOPIC_TERMS: tuple[str, ...] = (
    "ai",
    "a.i.",
    "agent",
    "agents",
    "agentic",
    "automation",
    "automate",
    "automates",
    "automated",
    "autonomous",
    "bot",
    "bots",
    "chatbot",
    "copilot",
    "genai",
    "llm",
    "llms",
    "machine learning",
    "mcp",
    "model",
    "models",
    "assistant",
    "assistants",
    "intelligence",
    # Named products and vendors. A clause can be squarely on topic without ever using a
    # generic word: "Northwind Health deployed Claude, built by Anthropic, across claims" and
    # "an insurer put Salesforce Agentforce into production" were the shapes of the strongest
    # openers in the 08-18 list, and the first version of this
    # gate suppressed both. Vendor names are the vocabulary buyers actually use.
    "claude",
    "anthropic",
    "chatgpt",
    "openai",
    "codex",
    "copilot",
    "agentforce",
    "bedrock",
    "agentcore",
    "langchain",
    "aidoc",
    "abridge",
    "robotaxi",
    "robotaxis",
    "driverless",
    "self-driving",
    "rpa",
    "digital teammate",
    "digital teammates",
    "digital worker",
    "digital workers",
)

# "AI" glued to the end of a coined name — SinglepointAI, OpenAI, xAI, AIwithCare. The
# whole-word matcher cannot see these, and lowering it to a substring match would fire on
# Dubai, Mumbai and Chennai. Case is what separates them: a capital A-I next to lowercase
# letters is a product name, "ai" inside a place name never is.
_EMBEDDED_AI_RE = re.compile(r"(?:(?<=[a-z])AI\b|\bAI(?=[a-z]))")

# Verbs that make a clause an EVENT (something that happened on a date) rather than a
# standing description of what the company does. Every spec's section 1 claims the clause
# is "event-shaped, not a static capability statement"; 270 of 453 rows (60%) were not.
# Advisory only: a well-chosen standing fact still beats no clause, and promoting this to
# a block would suppress more rows than the campaign can afford to lose.
SIGNAL_EVENT_VERBS: tuple[str, ...] = (
    "launch",
    "launches",
    "launched",
    "ship",
    "ships",
    "shipped",
    "raise",
    "raises",
    "raised",
    "acquire",
    "acquires",
    "acquired",
    "partner",
    "partners",
    "partnered",
    "name",
    "names",
    "named",
    "appoint",
    "appoints",
    "appointed",
    "expand",
    "expands",
    "expanded",
    "announce",
    "announces",
    "announced",
    "hire",
    "hires",
    "hiring",
    "sign",
    "signs",
    "signed",
    "close",
    "closes",
    "closed",
    "deploy",
    "deploys",
    "deployed",
    "roll",
    "rolls",
    "rolled",
    "standardise",
    "standardises",
    "standardize",
    "standardizes",
    "trial",
    "trials",
    "trialing",
    "trialling",
    "pilot",
    "pilots",
    "piloting",
    "invest",
    "invests",
    "invested",
    "select",
    "selects",
    "selected",
    "integrate",
    "integrates",
    "integrated",
    "introduce",
    "introduces",
    "introduced",
    "debut",
    "debuts",
    "debuted",
    "open",
    "opens",
    "opened",
    "add",
    "adds",
    "added",
    "unveil",
    "unveils",
    "unveiled",
    "build",
    "builds",
    "building",
    "credit",
    "credits",
    "credited",
    "settle",
    "settles",
    "settled",
)


def _term_re(terms: tuple[str, ...]) -> re.Pattern[str]:
    """Whole-word alternation over ``terms``, longest first so "machine learning" wins."""
    ordered = sorted({t.strip().lower() for t in terms if t.strip()}, key=len, reverse=True)
    return re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(t) for t in ordered) + r")(?!\w)",
        re.IGNORECASE,
    )


_TOPIC_RE = _term_re(SIGNAL_TOPIC_TERMS)
_EVENT_RE = _term_re(SIGNAL_EVENT_VERBS)


def signal_on_topic(clause: str, terms: tuple[str, ...] = SIGNAL_TOPIC_TERMS) -> bool:
    """True when ``clause`` mentions at least one of ``terms``.

    Whole-word matched, so "AI" does not fire on "Dubai" and "model" does not fire on
    "remodelled". A clause that fails this cannot open a body that then claims something
    about the recipient's agents: the two paragraphs do not connect and the reader sees
    the seam.
    """
    if not (clause or "").strip():
        return False
    rx = _TOPIC_RE if terms is SIGNAL_TOPIC_TERMS else _term_re(terms)
    if rx.search(clause):
        return True
    return terms is SIGNAL_TOPIC_TERMS and bool(_EMBEDDED_AI_RE.search(clause))


def signal_is_event(clause: str) -> bool:
    """True when ``clause`` carries a verb that makes it a dated event rather than a
    standing description of the company.

    Advisory input, never a block: "Riverbend Health runs NVIDIA Blackwell infrastructure to
    power its foundation model and agentic AI program" has no event verb and is still a
    good opener. Use it to rank re-research, not to suppress.
    """
    if not (clause or "").strip():
        return False
    return bool(_EVENT_RE.search(clause))


def signal_stray_digits(clause: str, company: str = "") -> list[str]:
    """Digit-bearing tokens in ``clause`` that are NOT part of ``company``'s own name.

    The clause contract has always said "no digits". The intent was never to ban the
    numeral itself: it was to keep a **date or a metric** out of an opener, because
    "raised $45M in March" reads as a scraped record rather than something a person
    noticed. Written as a blanket ban it also excluded any company whose *name* contains a
    digit, which is not a defect at all.

    Found 2026-08-19: two companies whose names carry a digit — a "Tier2 Risk" and a
    "B2Gate" shape — both had a verified, well-sourced AI
    trigger and no sendable clause, because the contract required the clause to open on the
    company name and simultaneously forbade the digit inside it. Two rules, jointly
    unsatisfiable, and neither was ever enforced in code — the ban lived only in prose in
    the spec files, so nothing caught the collision or the dates it was written to stop.

    Returns the offending tokens (empty when the clause is clean), so a caller can name
    them rather than saying "no digits allowed" at a clause whose only digit is a brand.
    """
    text = (clause or "").strip()
    if not text:
        return []
    allowed = {t.lower() for t in re.findall(r"[\w.'-]*\d[\w.'-]*", company or "")}
    return [t for t in re.findall(r"[\w.'-]*\d[\w.'-]*", text) if t.lower() not in allowed]
