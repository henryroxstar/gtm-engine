from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import RoleVocabulary

_ECON_RE = re.compile(
    r"\b(chief|founder|ciso|cto|cpo|ceo|cro|c-level)\b|(?<!vice )(?<!vice-)\bpresident\b",
    re.IGNORECASE,
)


def _has_anti_cue(t: str, seat: str, vocab: RoleVocabulary | None) -> bool:
    if "chief of staff" in t:
        return True
    if vocab and hasattr(vocab, "anti_cues") and vocab.anti_cues:
        for ac in vocab.anti_cues.get(seat, ()):
            if ac in t:
                return True
    return False


def _check_tenant_level_cues(t: str, vocab: RoleVocabulary | None) -> str | None:
    if not (vocab and getattr(vocab, "level_cues", None)):
        return None
    for level_name in ("economic-buyer", "evaluator", "champion"):
        for cue in vocab.level_cues.get(level_name, ()):
            if cue in t:
                return level_name
    return None


def _is_evaluator(t: str) -> bool:
    evaluator_exact_cues = (
        "chief architect",
        "principal architect",
        "staff architect",
        "lead architect",
        "staff engineer",
        "lead engineer",
        "principal engineer",
        "senior architect",
    )
    if any(cue in t for cue in evaluator_exact_cues):
        return True
    return ("staff" in t or "lead" in t or "principal" in t) and (
        "architect" in t or "engineer" in t
    )


def _is_economic_buyer(t: str) -> bool:
    return bool(_ECON_RE.search(t))


def _is_seat_champion(t: str, seat: str, vocab: RoleVocabulary | None) -> bool:
    champion_rank_cues = ("svp", "vp", "head", "director", "vice president")
    if not any(cue in t for cue in champion_rank_cues):
        return False
    if not vocab:
        return True
    seat_cues: list[str] = []
    seat_names: list[str] = [seat.replace("-", " ")]
    for s, personas, _ in getattr(vocab, "seat_rules", ()):
        if s == seat or s.rstrip("s") == seat.rstrip("s"):
            for p in personas:
                seat_names.append(p.replace("-", " "))
                for p_name, p_cues in getattr(vocab, "persona_rules", ()):
                    if p_name == p:
                        seat_cues.extend(p_cues)
    if not seat_cues:
        for p_name, p_cues in getattr(vocab, "persona_rules", ()):
            if p_name == seat or p_name.rstrip("s") == seat.rstrip("s"):
                seat_names.append(p_name.replace("-", " "))
                seat_cues.extend(p_cues)
    if not seat_cues and len(seat_names) == 1:
        return False

    for sc in seat_cues:
        if sc in t:
            return True
        clean_sc = (
            sc.replace("head of ", "").replace("director of ", "").replace("vp of ", "").strip()
        )
        if clean_sc and clean_sc in t:
            return True

    return any(sn in t for sn in seat_names)


def level_of(title: str, seat: str, vocab: RoleVocabulary | None = None) -> str:
    """Classify contact level into {champion, economic-buyer, evaluator, unknown} (R7.1)."""
    t = title.lower().strip()
    if not t or _has_anti_cue(t, seat, vocab):
        return "unknown"

    tenant_lvl = _check_tenant_level_cues(t, vocab)
    if tenant_lvl:
        return tenant_lvl

    if _is_evaluator(t):
        return "evaluator"

    if "deputy ciso" in t:
        return "champion"

    if _is_economic_buyer(t):
        return "economic-buyer"

    if _is_seat_champion(t, seat, vocab):
        return "champion"

    return "unknown"
