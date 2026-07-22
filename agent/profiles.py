"""Profile resolution and the per-profile system-prompt injection.

A *profile* is a company bundle living at ``profiles/<name>/`` containing a
``PROFILE.md`` (config, never secrets), a ``knowledge/`` pack, and optionally
``products/<slug>/`` directories. The brain resolves exactly one profile per
Telegram chat (session-bound) and loads ALL company/product/ICP/brand/voice
facts from that bundle — never from ``plugin/`` (which is de-branded).

This module is pure stdlib + best-effort parsing: it must never raise on a
malformed ``PROFILE.md`` (a profile with an unparsable products block simply
exposes no capabilities), and the only hard error is ``profile_dir`` on a
missing profile (callers rely on that to validate ``/profile`` switches).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid an import cycle at runtime; only needed for type hints
    from .config import Config


def list_profiles(profiles_root: Path) -> list[str]:
    """Return the names of all valid profiles, sorted.

    A directory is a profile only if it contains a ``PROFILE.md``. This skips
    stray files, ``.gitkeep`` scaffolding, and partially-created bundles.
    """
    if not profiles_root.is_dir():
        return []
    names: list[str] = []
    for child in sorted(profiles_root.iterdir()):
        if child.is_dir() and (child / "PROFILE.md").is_file():
            names.append(child.name)
    return names


def profile_dir(profiles_root: Path, name: str) -> Path:
    """Return the directory for ``name``; raise ``ValueError`` if it is not a profile.

    Used to validate a profile before binding a session to it. The check requires
    both the directory AND its ``PROFILE.md`` to exist, so a bare ``mkdir`` does
    not pass for a switch.
    """
    candidate = profiles_root / name
    if not candidate.is_dir() or not (candidate / "PROFILE.md").is_file():
        available = ", ".join(list_profiles(profiles_root)) or "(none)"
        raise ValueError(f"Unknown profile '{name}'. Available profiles: {available}.")
    return candidate


# --- products: fenced-list parsing -------------------------------------------
#
# PROFILE.md carries products inside a triple-backtick fence, under a `products:`
# key, one entry per line in inline-mapping form, e.g.:
#
#   products:
#     - { slug: acme-gateway, name: Acme Gateway, capabilities: [gateway, llm-safety], flagship: true }
#     - { slug: acme-pay,     name: Acme Pay,     capabilities: [agent-payments] }
#
# We deliberately parse this with regexes (no YAML dep): the format is small and
# fixed, and any line we cannot parse is skipped rather than aborting the load.

# Matches `- { ... }` items inside the products block.
_PRODUCT_ITEM_RE = re.compile(r"^\s*-\s*\{(?P<body>.*)\}\s*$")
# Matches `products:` possibly followed by an inline value (e.g. `products: []`).
_PRODUCTS_KEY_RE = re.compile(r"^\s*products:\s*(?P<inline>.*?)\s*$")
# Matches a top-level `key:` line that ends the products block (heuristic).
_TOP_KEY_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*:\s")


def _split_top_level_commas(body: str) -> list[str]:
    """Split an inline-mapping body on commas that are NOT inside ``[...]``.

    ``slug: x, name: y, capabilities: [a, b]`` → three fields, keeping the
    bracketed list intact. A tiny bracket-depth tracker is enough; the format
    never nests braces.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _parse_product_item(body: str) -> dict | None:
    """Parse one inline-mapping body (the text between the braces) into a dict.

    Recognises ``slug``, ``name``, ``capabilities`` (a ``[...]`` list), and any
    other simple scalar key (e.g. ``flagship: true``). Returns ``None`` when no
    ``slug`` is present — a product without a slug is meaningless to the
    capability resolver.
    """
    result: dict = {"slug": None, "name": None, "capabilities": []}
    for field_text in _split_top_level_commas(body):
        if ":" not in field_text:
            continue
        key, _, value = field_text.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if key == "capabilities":
            result["capabilities"] = _parse_list(value)
        elif key in ("slug", "name"):
            result[key] = _strip_quotes(value)
        else:
            # Preserve extra flags (e.g. flagship) as coerced scalars.
            result[key] = _coerce_scalar(value)
    if not result.get("slug"):
        return None
    # Default a missing display name to the slug so callers always have a label.
    if not result.get("name"):
        result["name"] = result["slug"]
    return result


def _parse_list(value: str) -> list[str]:
    """Parse a ``[a, b, c]`` inline list into a list of trimmed strings."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    items = [_strip_quotes(v.strip()) for v in value.split(",")]
    return [i for i in items if i]


def _strip_quotes(value: str) -> str:
    """Remove surrounding single/double quotes from a scalar token."""
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def _coerce_scalar(value: str):
    """Coerce ``true``/``false``/``null`` and ints; otherwise return the string."""
    raw = _strip_quotes(value)
    low = raw.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none", "~"):
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def load_products(profiles_root: Path, name: str) -> list[dict]:
    """Best-effort parse of the PROFILE.md ``products:`` fenced list.

    Returns a list of ``{slug, name, capabilities: [...]}`` (plus any extra
    flags such as ``flagship``). Returns ``[]`` when the profile has no products,
    an empty list (``products: []``), or an unparsable block — never raises.
    """
    try:
        profile_path = profile_dir(profiles_root, name) / "PROFILE.md"
        text = profile_path.read_text(encoding="utf-8")
    except (ValueError, OSError):
        return []

    products: list[dict] = []
    in_products_block = False
    in_fence = False  # whether the products block lives inside a ``` fence

    lines = text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()

        if not in_products_block:
            m = _PRODUCTS_KEY_RE.match(line)
            if m:
                inline = m.group("inline").strip()
                # `products: []` (or `products:` with an empty inline) → no products.
                if inline in ("[]", "[ ]"):
                    return []
                in_products_block = True
                # Detect whether we're inside a code fence by scanning preceding lines.
                in_fence = _is_inside_fence(lines, idx)
            continue

        # Inside the products block: collect `- { ... }` items.
        item = _PRODUCT_ITEM_RE.match(line)
        if item:
            parsed = _parse_product_item(item.group("body"))
            if parsed is not None:
                products.append(parsed)
            continue

        # Termination conditions for the block:
        if in_fence and stripped.startswith("```"):
            break  # closing fence ends the block
        if not in_fence and stripped and _TOP_KEY_RE.match(line) and not stripped.startswith("#"):
            break  # a new top-level key ends an un-fenced block
        # Blank lines and `#` comments are tolerated inside the block.

    return products


_GATE1_CHAT_ID_RE = re.compile(r"^\s*telegram_gate1_chat_id:\s*(\d+)\s*$", re.MULTILINE)


def read_profile_field(text: str, key: str) -> str | None:
    """Read a simple ``key: value`` scalar from PROFILE.md front-matter.

    Tolerates the values living inside ``` fences (we scan the whole file) and
    strips a trailing ``# inline comment`` and surrounding quotes. Returns the
    FIRST match, so callers must pass unambiguous keys (e.g. ``brand_name``, not
    bare ``name`` which also matches the identity block). ``None`` if absent.

    Public (not module-private) — also used by ``agent.readiness`` to check a
    pack's required ``settings`` inputs against a profile's PROFILE.md.
    """
    m = re.search(rf"^\s*{re.escape(key)}:\s*(?P<v>.+?)\s*$", text, re.MULTILINE)
    if not m:
        return None
    val = m.group("v")
    hash_idx = val.find(" #")  # strip a trailing inline comment
    if hash_idx != -1:
        val = val[:hash_idx]
    return _strip_quotes(val.strip()) or None


def load_gate1_chat_id(profiles_root: Path, name: str) -> int | None:
    """Read ``telegram_gate1_chat_id`` from profiles/<name>/PROFILE.md.

    Returns the integer chat ID, or ``None`` if the field is absent or the
    profile cannot be read. Never raises — missing config is handled by callers.
    """
    try:
        profile_path = profile_dir(profiles_root, name) / "PROFILE.md"
        text = profile_path.read_text(encoding="utf-8")
    except (ValueError, OSError):
        return None
    m = _GATE1_CHAT_ID_RE.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _is_inside_fence(lines: list[str], idx: int) -> bool:
    """Return True if ``lines[idx]`` sits inside a ``` code fence.

    Counts fence delimiters before ``idx``; an odd count means we are open.
    """
    open_count = 0
    for i in range(idx):
        if lines[i].lstrip().startswith("```"):
            open_count += 1
    return open_count % 2 == 1


# --- system prompt injection -------------------------------------------------


def system_prompt_for(name: str, cfg: Config) -> str:
    """Build the system-prompt fragment that binds the brain to ``name``.

    This is injected as ``ClaudeAgentOptions.system_prompt``. It does three jobs:
      1. Declares the active profile (tenant boundary).
      2. Forces all company/product/ICP/brand/voice facts to come from
         ``profiles/<name>/`` — never from the de-branded ``plugin/``.
      3. States the capability gate (product-bound skills run only when a product
         in PROFILE.md provides the required capability) and the state location
         (``content/<name>/``).

    We also list the resolved products + capabilities inline so the model can
    apply the gate without re-reading the file on every turn. The lists are
    best-effort — an unparsable products block just yields a shorter prompt.
    """
    products = load_products(cfg.profiles_root, name) if _profile_exists(cfg, name) else []

    # Render a compact capability map for the model.
    if products:
        product_lines = []
        for p in products:
            caps = ", ".join(p.get("capabilities", [])) or "—"
            flag = " (flagship)" if p.get("flagship") else ""
            product_lines.append(
                f"  - {p.get('name', p['slug'])} [{p['slug']}]{flag}: capabilities = {caps}"
            )
        products_block = "Products available in this profile:\n" + "\n".join(product_lines)
        all_caps = sorted({c for p in products for c in p.get("capabilities", [])})
        caps_line = "Capabilities provided by this profile: " + (", ".join(all_caps) or "(none)")
    else:
        products_block = "Products available in this profile: (none declared)"
        caps_line = "Capabilities provided by this profile: (none)"

    return (
        f"ACTIVE PROFILE = {name}. "
        f"Load ALL company/product/ICP/brand/voice facts from profiles/{name}/. "
        f"Never read company facts from plugin/. "
        f"Product-bound skills (requires_capability) run only if a product in PROFILE.md "
        f"products[] provides the capability. "
        f"Write runtime state under content/{name}/.\n\n"
        f"{products_block}\n"
        f"{caps_line}\n\n"
        f"FILE DELIVERY: The cockpit can deliver any file under content/{name}/ to the operator "
        f"as a Telegram document. To send one, append a sentinel ⟦FILE:/absolute/path/to/file.ext⟧ "
        f"at the very end of your response (on its own line, after all prose); the cockpit strips "
        f"it from the visible text and sends the file automatically. One sentinel per file; only "
        f"paths inside content/{name}/ are accepted. Emit a sentinel in BOTH of these cases:\n"
        f"  (1) Whenever you write a deliverable (PDF, DOCX, PPTX, CSV, …) the operator should receive.\n"
        f"  (2) Whenever the operator asks you to send, share, or 'give me' a file that you or a "
        f"prior run already produced (e.g. 'send the docx', 'give me the pdf here', 'try again') — "
        f"locate it under content/{name}/ and emit its sentinel. This holds for files made by ANY "
        f"skill and in follow-up turns, not only the run that created the file.\n"
        f"NEVER tell the operator you cannot send binary files, or that the chat only supports text "
        f"and images — the sentinel IS the delivery mechanism and it sends real .docx/.pdf/.pptx "
        f"bytes. Only if you genuinely cannot find the file under content/{name}/ may you say so, and "
        f"then state the exact path you expected."
    )


def _profile_exists(cfg: Config, name: str) -> bool:
    """True if ``name`` is a valid profile under ``cfg.profiles_root``."""
    try:
        profile_dir(cfg.profiles_root, name)
        return True
    except ValueError:
        return False
