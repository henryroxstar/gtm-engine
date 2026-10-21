"""Reference-image conditioning for the Gemini image worker — the input half of C1.

``generate_image`` gained an ordered ``reference_images`` parameter on 2026-09-06. Each path
becomes an ``inlineData`` part sent AHEAD of the text part, in list order, so a prompt saying
"the subject from the first reference" addresses what the author meant. This module owns
everything between the list of strings the brain hands over and the list of parts the request
carries; it lives beside ``server.py`` rather than inside it because that file sits under the
§R10 file-length cap with little headroom, and a ratchet answered by moving the ratchet is not
a ratchet.

Three properties hold by construction here, and each has a test:

* **Order is the contract.** The list is never sorted, deduped or reordered. The same file
  passed twice is sent twice, because position is what the prompt addresses. The manifest the
  tool returns names the count and order actually sent, so a mismatch is visible, not inferred.
* **Paths are confined.** A file-path parameter on a tool that then ships the bytes to a third
  party is an exfiltration primitive. Every path resolves through
  :func:`gtm_core.confine.confined_source_file` against the resolved content root
  (``GTM_CONTENT_ROOT`` on the VPS; the per-workspace root the backend injects). Anything
  outside is refused and never read; a symlink is refused on its target, not its name.
* **Oversized input is refused, never resized.** Base64 inflates a body by 4/3 and the
  provider caps an inline request at 20 MB. Resizing here would put a fourth Pillow site in the
  tree and quietly change the pixels the author chose, so a file over the per-file cap, or a
  set whose ENCODED total is over the request cap, is refused naming the offender and its size.

Caps are module constants with env overrides, validated at import by the same guard family the
prices use — a zero cap would refuse every call and read as a broken tool.

Every failure is RETURNED as a ``[gemini-image-error] …`` string, never raised: the worker's
contract is that the brain always gets a tool result it can react to and the MCP connection
never drops.
"""

from __future__ import annotations

import base64
from pathlib import Path

from agent.mcp import nonneg_price_env, positive_int_env, positive_timeout_env
from gtm_core.confine import ConfinementError, confined_source_file
from gtm_core.paths import resolve_content_root

# The corpus-documented composition ceiling for this model. The API's hard per-request file
# limit is 3,600 — far past anything a prompt can address positionally, so it is not the bound.
MAX_REFERENCE_IMAGES = positive_int_env("GEMINI_IMAGE_MAX_REFERENCES", "14")
# Per file: the same 5 MiB the vision worker already bounds an image it opens and ships at.
MAX_REFERENCE_BYTES = positive_int_env("GEMINI_IMAGE_MAX_REFERENCE_BYTES", str(5 * 1024 * 1024))
# Total, measured on the ENCODED payload because base64 is what the request carries. Google's
# documented inline-request ceiling is 20 MB across prompt, system instructions and inline
# bytes; 2 MiB is reserved for the prompt, generationConfig and JSON overhead.
MAX_ENCODED_TOTAL_BYTES = positive_int_env(
    "GEMINI_IMAGE_MAX_ENCODED_TOTAL_BYTES", str(18 * 1024 * 1024)
)
# Input cost per reference image. With references attached the input side is no longer
# constant, and a flat fee would UNDER-report spend against the §R2 monthly cap — silently,
# because the row is still written and still looks right. Derived 2026-09-06 from the same
# pricing page as the worker's output prices: input is $2.00 / 1M tokens (text/image) and an
# input image is counted as 560 tokens, which the page states as "$0.0011 per image". A very
# large reference tiles to more tokens, so this is a floor; the override exists for it.
PRICE_PER_REF_USD = nonneg_price_env("GEMINI_IMAGE_PRICE_PER_REF_USD", "0.0011")
# Each reference adds upload and decode time on the provider's side before generation starts,
# so the worker's read budget grows with the reference count as well as the resolution band:
# 14 references on 1K adds 140s, still inside the 4K band's own 360s.
READ_TIMEOUT_PER_REF_S = positive_timeout_env("GEMINI_IMAGE_READ_TIMEOUT_PER_REF_S", "10.0")

# Bare image suffixes the provider documents for image input. Checked before any I/O.
_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}

_ERR = "[gemini-image-error]"


def resolve_references(
    paths: list[str] | None, *, content_root: Path | None = None
) -> tuple[list[dict], str]:
    """Turn an ordered list of reference paths into ordered ``inlineData`` parts.

    Returns ``(parts, "")`` on success or ``([], "[gemini-image-error] …")`` on the first
    refusal. ``None`` and ``[]`` both yield ``([], "")`` — the additive default, byte-identical
    to a request with no references. No file is opened until the whole list has passed the
    count check, and no file outside the root is ever opened at all.
    """
    if not paths:
        return [], ""
    if len(paths) > MAX_REFERENCE_IMAGES:
        return [], (
            f"{_ERR} {len(paths)} reference images exceed the cap of {MAX_REFERENCE_IMAGES}. "
            "Compose fewer references — position is what the prompt addresses, and a longer "
            "list is not a stronger one."
        )
    root = content_root if content_root is not None else resolve_content_root()

    parts: list[dict] = []
    encoded_total = 0
    for position, raw in enumerate(paths, start=1):
        label = f"reference {position} ({Path(str(raw)).name})"
        suffix = Path(str(raw)).suffix.lower()
        mime = _MEDIA_TYPES.get(suffix)
        if mime is None:
            return [], (
                f"{_ERR} {label}: unsupported image type {suffix!r} "
                f"(expected one of {', '.join(sorted(_MEDIA_TYPES))})."
            )
        try:
            resolved = confined_source_file(
                raw, content_root=root, max_bytes=MAX_REFERENCE_BYTES, action="read a reference"
            )
        except ConfinementError as exc:
            return [], f"{_ERR} {label}: {exc}"
        if resolved.stat().st_size == 0:
            return [], f"{_ERR} {label}: file is empty (0 bytes) — not an image."
        try:
            data = base64.b64encode(resolved.read_bytes()).decode("ascii")
        except OSError as exc:
            return [], f"{_ERR} {label}: could not be read ({exc.__class__.__name__})."
        encoded_total += len(data)
        if encoded_total > MAX_ENCODED_TOTAL_BYTES:
            return [], (
                f"{_ERR} the encoded size of the reference set passes the request cap at "
                f"{label}: {encoded_total} bytes encoded so far, cap {MAX_ENCODED_TOTAL_BYTES}. "
                "Send fewer or smaller references — they are refused, not resized."
            )
        parts.append({"inlineData": {"mimeType": mime, "data": data}})
    return parts, ""


def manifest_line(paths: list[str]) -> str:
    """One line naming the count and order actually sent — basenames only.

    A full path would put the tenant's directory tree into a string the brain may echo into a
    ledger or a message; the basename is enough to check position against the prompt.
    """
    names = " ".join(f"{i}={Path(str(p)).name}" for i, p in enumerate(paths, start=1))
    return f"references sent ({len(paths)}, in order): {names}"
