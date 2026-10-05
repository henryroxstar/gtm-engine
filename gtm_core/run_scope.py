"""Run scope — which product a prospecting run is for, decided once, at the top.

Today ``--product`` is a file-lookup hint some commands accept. A run that drops it silently
reads the default product's files, which is right while a profile has one product and wrong the
day a second product has its own arguments: a Stream run would say Gateway's things, with nothing
to show it. This module turns the hint into a **scope**: resolved first (before any metered call),
asked for when there is a real choice, printed in plain words, and required by every reader that
could otherwise fall back without saying so.

**The default product owns the profile level.** Its targeting and messaging files are the
profile's ``knowledge/`` files, so it needs nothing under ``products/``. A *second product* is a
``PROFILE.md`` product other than the default whose ``products/<slug>/`` folder holds EVERY
``PRODUCT_REQUIRED`` file (one with only some is being set up: not offered, and naming it refuses
with ``product-not-ready``). Brand and product-doc folders never count. With zero second products a
profile behaves exactly as it did before this module existed, and prints nothing new
(the one-product-per-run design note, decision D5).

**A product is an argument, never ambient.** It comes from the CLI flag, the answer to the one
question this run asked, or the validated backend request. It is not read from a row, a marker
file or the environment (an AST test pins that), for the same reason an experiment overlay is not.

The manifest below is closed: a file under a second product's folder that is on no list is
refused, so a new knowledge file must be classified before any product can carry it.

CLI::

    python -m gtm_core.run_scope resolve --profile P [--product S] [--overlay X] [--unattended]

Exit 0 = resolved (silent when the profile has no second product), 3 = ask (options on stdout,
default first), 2 = refused (a message a person can act on, on stderr).
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .paths import _safe_segment, resolve_profiles_root
from .product_manifest import (  # re-exported: the manifest's names are part of this API
    _CONFIG_SUFFIXES,
    NOT_PRODUCT_FILE,  # noqa: F401
    PRODUCT_DERIVED,
    PRODUCT_OWN_ONLY,
    PRODUCT_REQUIRED,
    SHARED_BY_DEFAULT,
    TENANT_WIDE,
    classify,
)

#: Gate and field markers (CLAUDE.md, the publish/reply parsers). A product name is tenant data that
#: may have come from onboarding extraction of a scraped page, and it is printed into run headers.
_MARKER_RE = re.compile(r"[\u27e6\u27e7]")
_PRODUCTS_KEY_RE = re.compile(r"^\s*(products|default_product)\s*:", re.MULTILINE)
_FENCE_RE = re.compile(r"^```[^\n]*$", re.MULTILINE)


class ScopeError(ValueError):
    """A run scope was refused. ``code`` is stable; ``str(exc)`` is what a person reads."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProductRequired(ScopeError):
    """A reader was called with no product on a profile that has a second product."""


@dataclass(frozen=True)
class Product:
    slug: str
    name: str


@dataclass(frozen=True)
class RunScope:
    profile: str
    #: The slug the operator (or the backend) named; ``None`` when none was needed. This is the
    #: value to hand to ``resolve_knowledge_file`` — ``None`` keeps today's profile-level reads
    #: byte-identical for a profile with no second product.
    product: str | None
    product_display: str
    is_second_product: bool
    #: True when the profile has at least one second product, so the header and record speak.
    multi: bool
    overlay: str | None = None
    own_files: tuple[str, ...] = ()
    shared_files: tuple[str, ...] = ()
    ignored_dirs: tuple[str, ...] = ()
    default_display: str = ""
    #: A declared product other than the default that has NO prospecting files, named on a company
    #: with no ready second product. Readers still resolve as they always did (product docs and
    #: brand files legitimately take ``--product``), but a ledger WRITER treats it as a second
    #: product: a Stream run whose files were removed must not become an unguarded default write.
    named_non_default: bool = False

    @property
    def writes_as_second(self) -> bool:
        """What a WRITER of shared state asks: a second product, or a declared non-default product
        that has no files of its own. Both may add and never change what the default product holds."""
        return self.is_second_product or self.named_non_default

    @property
    def state_slug(self) -> str | None:
        """The key per-product files (run state, run exports) use: a second product's slug, else
        ``None`` — the default product keeps every legacy name."""
        return self.product if self.is_second_product else None


@dataclass(frozen=True)
class Ask:
    """More than one product could be meant. ``options`` lists the default product first."""

    options: tuple[Product, ...]
    default: str


@dataclass(frozen=True)
class Refusal:
    code: str
    message: str


@dataclass
class _Facts:
    profile: str
    products: list[Product] = field(default_factory=list)
    default: str | None = None
    #: Declared, not the default, and holding every required (non-derived) file: selectable.
    second: list[str] = field(default_factory=list)
    #: Declared, not the default, holding SOME required files: being set up, never selectable.
    partial: dict[str, list[str]] = field(default_factory=dict)
    #: Folders not in ``products[]`` (reported, never selectable).
    ignored: list[str] = field(default_factory=list)
    #: Undeclared folders that hold product files: a product nobody listed. Refuses every run.
    stray: list[str] = field(default_factory=list)


def _clean(text: object) -> str:
    """Text echoed to the operator: no gate markers, no line breaks, whatever the source held."""
    return " ".join(_MARKER_RE.sub("", str(text)).split())


def _has_content(path: Path) -> bool:
    """A file that says something. Empty, whitespace-only and comment-only files are placeholders."""
    try:
        return any(
            line.strip() and not line.lstrip().startswith("#")
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    except (OSError, UnicodeDecodeError):
        return False


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", text.strip().lower())


@functools.lru_cache(maxsize=32)
def _products_block(text: str) -> dict:
    """The fenced PROFILE.md block that declares products, parsed strictly.

    ``content_quality.sources._parse_profile_md`` is tolerant on purpose (a content run should
    survive a sloppy profile). This reader may not be: a YAML error there reads as "this company
    has no products", which switches every product guard off. So the block that names products must
    parse, and ``products`` must be a list of mappings with a slug, or the run refuses.
    """
    import yaml

    fences = [m.end() for m in _FENCE_RE.finditer(text)]
    for start, end in zip(fences[0::2], fences[1::2], strict=False):
        block = text[start : end - 3]
        if not _PRODUCTS_KEY_RE.search(block):
            continue
        try:
            parsed = yaml.safe_load(block)
        except yaml.YAMLError as exc:
            raise ScopeError(
                "profile-unreadable",
                "PROFILE.md's products block could not be read, so which products this company "
                f"sells is unknown. Fix the block and run again ({type(exc).__name__}).",
            ) from None
        products = parsed.get("products") if isinstance(parsed, dict) else None
        if products is not None and not (
            isinstance(products, list)
            and all(isinstance(p, dict) and p.get("slug") for p in products)
        ):
            raise ScopeError(
                "profile-unreadable",
                "PROFILE.md's products list is not a list of entries with a slug. Fix it and run "
                "again.",
            )
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _load_facts(profile: str, profiles_root: Path) -> _Facts:
    _safe_segment(profile, "profile")
    base = profiles_root / profile
    facts = _Facts(profile=profile)
    try:
        text = (base / "PROFILE.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError):
        raise ScopeError(
            "profile-unreadable",
            "PROFILE.md could not be read, so this company's products are unknown.",
        ) from None
    parsed = _products_block(text)
    for entry in parsed.get("products") or []:
        facts.products.append(Product(str(entry["slug"]), str(entry.get("name") or entry["slug"])))
    declared = parsed.get("default_product")
    slugs = [p.slug for p in facts.products]
    if declared and str(declared) in slugs:
        facts.default = str(declared)
    elif len(slugs) == 1:
        facts.default = slugs[0]
    needed = PRODUCT_REQUIRED - PRODUCT_DERIVED
    products_dir = base / "products"
    for p in facts.products:
        try:
            folder = products_dir / _safe_segment(p.slug, "product")
        except ValueError:
            continue
        if p.slug == facts.default:
            continue
        have = {f for f in PRODUCT_REQUIRED if _has_content(folder / f)}
        if needed <= have:
            facts.second.append(p.slug)
        elif have:
            facts.partial[p.slug] = sorted(needed - have)
    if products_dir.is_dir():
        known = set(slugs)
        for d in sorted(products_dir.iterdir()):
            if d.is_dir() and d.name not in known:
                facts.ignored.append(d.name)
                if any((d / f).is_file() for f in PRODUCT_REQUIRED):
                    facts.stray.append(d.name)
    return facts


def _display(facts: _Facts, slug: str | None) -> str:
    name = next((p.name for p in facts.products if p.slug == slug), slug or "")
    return _clean(name)


def _match(facts: _Facts, given: str) -> str:
    """One product slug from a slug or display name, or a refusal. Never guesses.

    A name with a separator or a NUL cannot equal a declared slug or display name after
    normalising, so it falls out as ``product-unknown`` with no special case; the mutation pass
    (2026-09-29) showed the earlier guard here changed only the message.
    """
    key = _norm(given)
    hits = {p.slug for p in facts.products if _norm(p.slug) == key or _norm(p.name) == key}
    if len(hits) == 1:
        return next(iter(hits))
    names = ", ".join(_clean(p.name) for p in facts.products) or "none declared"
    shown = _clean(given)
    if hits:
        raise ScopeError(
            "product-ambiguous", f"{shown!r} matches more than one product ({names}). Name one."
        )
    raise ScopeError(
        "product-unknown", f"{shown!r} is not one of this company's products ({names})."
    )


def _check_second_product(
    facts: _Facts, profiles_root: Path, slug: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Validate a second product's folder against the manifest. Returns (own, shared-fallbacks)."""
    display = _display(facts, slug)
    folder = profiles_root / facts.profile / "products" / _safe_segment(slug, "product")
    # Dotfiles are the OS's (a Finder `.DS_Store`), never knowledge: no resolver reads one.
    present = (
        {p.name for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")}
        if folder.is_dir()
        else set()
    )
    # Readiness (every required file present) was decided when the facts were loaded, so a product
    # reaches here complete; the mutation pass showed a second "missing file" check was unreachable.
    copied = sorted(present & TENANT_WIDE)
    if copied:
        raise ScopeError(
            "product-overrides-tenant-fact",
            f"{display} has its own copy of {', '.join(copied)}. Those are company-wide and no "
            "single product may override them. Remove the copy from the product folder.",
        )
    company_files = {
        f.name for f in (profiles_root / facts.profile / "knowledge").glob("*") if f.is_file()
    }
    unclassified = sorted(
        n
        for n in present
        if classify(n) is None and (n.endswith(_CONFIG_SUFFIXES) or n in company_files)
    )
    if unclassified:
        raise ScopeError(
            "unclassified-product-file",
            f"{display} holds {', '.join(unclassified)}, which the product manifest does not "
            "list. Classify the file in gtm_core/run_scope.py before a product may carry it.",
        )
    own = tuple(
        sorted(
            n
            for n in present
            if classify(n) in {"product-required", "shared-by-default", "product-own-only"}
        )
    )
    profile_knowledge = profiles_root / facts.profile / "knowledge"
    shared = tuple(
        sorted(
            n for n in SHARED_BY_DEFAULT if n not in present and (profile_knowledge / n).is_file()
        )
    )
    return own, shared


def _unnamed(
    facts: _Facts, profile: str, overlay: str | None, interactive: bool
) -> RunScope | Ask | Refusal:
    """No product named: fine while there is nothing to choose between, otherwise ask or refuse."""
    default_display = _display(facts, facts.default)
    if not facts.second:
        return RunScope(
            profile, None, default_display, False, False, overlay, default_display=default_display
        )
    if facts.default is None:
        return Refusal(
            "no-default-product",
            "This company has more than one product set up for prospecting, but its "
            "PROFILE.md names no default product. Add default_product first.",
        )
    ordered = [facts.default, *sorted(facts.second)]
    default_display = default_display or "the default product"
    if not interactive:
        names = ", ".join(_display(facts, s) for s in ordered)
        return Refusal(
            "product-required",
            f"This company has more than one product set up for prospecting ({names}). "
            f"A run for no product would use {default_display}'s targeting without saying "
            "so. Choose one and pass it as --product <slug>.",
        )
    return Ask(tuple(Product(s, _display(facts, s)) for s in ordered), facts.default)


def _not_ready(facts: _Facts, slug: str) -> ScopeError:
    return ScopeError(
        "product-not-ready",
        f"{_display(facts, slug)} is being set up but is not ready to prospect: it still needs "
        f"{', '.join(facts.partial[slug])}. Until then it is not offered and a run for it stops.",
    )


def _named(facts: _Facts, root: Path, profile: str, product: str, overlay: str | None) -> RunScope:
    """A product was named. Raises :class:`ScopeError` when it may not be run."""
    default_display = _display(facts, facts.default)
    if not facts.second:
        # No selectable second product: `--product X` keeps today's meaning (product-first file
        # lookup, profile fallback). A declared product is named by its slug whatever spelling
        # was given; anything else passes through with only the segment checked, as the resolver
        # already does. A product that is being set up refuses rather than falling back.
        try:
            slug = _match(facts, product)
        except ScopeError:
            slug = _safe_segment(product, "product")
        if slug in facts.partial:
            raise _not_ready(facts, slug)
        declared_other = slug != facts.default and any(p.slug == slug for p in facts.products)
        return RunScope(
            profile, slug, _display(facts, slug), False, False, overlay,
            default_display=default_display, named_non_default=declared_other,
        )  # fmt: skip
    slug = _match(facts, product)
    common = {"ignored_dirs": tuple(facts.ignored), "default_display": default_display}
    if slug == facts.default:
        return RunScope(profile, slug, _display(facts, slug), False, True, overlay, **common)
    if slug in facts.partial:
        raise _not_ready(facts, slug)
    if slug not in facts.second:
        raise ScopeError(
            "product-not-set-up",
            f"{_display(facts, slug)} has no prospecting files yet, so a run for it would use "
            f"{default_display}'s arguments. Add its files first.",
        )
    own, shared = _check_second_product(facts, root, slug)
    return RunScope(
        profile, slug, _display(facts, slug), True, True, overlay,
        own_files=own, shared_files=shared, **common,
    )  # fmt: skip


def resolve(
    profile: str,
    *,
    product: str | None = None,
    overlay: str | None = None,
    interactive: bool = False,
    profiles_root: Path | None = None,
) -> RunScope | Ask | Refusal:
    """Decide the run's product. The first call of a run, before any metered or MCP data call."""
    root = profiles_root or resolve_profiles_root()
    try:
        facts = _load_facts(profile, root)
        if facts.stray:
            raise ScopeError(
                "unlisted-product-folder",
                f"products/{facts.stray[0]} holds prospecting files but is not a product in "
                "PROFILE.md. Add it to products, or move the files out, before any run: a folder "
                "nobody declared cannot be told apart from the product a run is meant to use.",
            )
        if overlay is not None:
            _safe_segment(overlay, "overlay")
        if product is None:
            return _unnamed(facts, profile, overlay, interactive)
        return _named(facts, root, profile, product, overlay)
    except ScopeError as exc:
        return Refusal(exc.code, str(exc))
    except ValueError as exc:  # an unsafe segment
        return Refusal("product-unsafe", str(exc))


def product_file(
    profile: str,
    scope: RunScope,
    filename: str,
    *,
    profiles_root: Path | None = None,
    overlay: str | None = None,
) -> Path:
    """Resolve one knowledge file for ``scope``, refusing a fallback for a second product.

    For a ``PRODUCT_REQUIRED`` file under a second product, the answer must come from the
    product's own folder (or an explicit overlay): the resolver would otherwise hand back the
    default product's file whenever the product's is missing, which is exactly the silent
    substitution this module exists to stop. Everything else resolves as it always did.
    """
    from .paths import resolve_knowledge_file

    root = profiles_root or resolve_profiles_root()
    path = resolve_knowledge_file(root, profile, filename, product=scope.product, overlay=overlay)
    if scope.is_second_product and filename in PRODUCT_OWN_ONLY:
        # Its own copy or none: the default product's registry is never a second product's.
        own = root / profile / "products" / _safe_segment(scope.product or "", "product") / filename
        overlay_dir = root / profile / "experiments"
        return path if (path == own or overlay_dir in path.parents) else own
    if scope.is_second_product and filename in PRODUCT_REQUIRED:
        product_dir = root / profile / "products" / _safe_segment(scope.product or "", "product")
        overlay_dir = root / profile / "experiments"
        if path.parent != product_dir and overlay_dir not in path.parents:
            hint = (
                f" Generate it with `python -m gtm_core.messaging matrix --profile {profile} "
                f"--product {scope.product}`."
                if filename in PRODUCT_DERIVED
                else ""
            )
            raise ScopeError(
                "product-file-missing",
                f"{scope.product_display} has no {filename} of its own, so this run would read "
                f"{scope.default_display or 'the default product'}'s.{hint}",
            )
    return path


def require(
    profile: str,
    product: str | None,
    *,
    profiles_root: Path | None = None,
    overlay: str | None = None,
) -> RunScope:
    """The one call every product-aware reader makes.

    With ``product=None`` it returns the default scope only while the profile has no second
    product; otherwise it raises :class:`ProductRequired`. That closes the gap between CLI calls:
    each reader is its own process, so a dropped ``--product`` in a Stream run refuses here rather
    than reading the default product's files silently.
    """
    result = resolve(
        profile, product=product, overlay=overlay, interactive=False, profiles_root=profiles_root
    )
    if isinstance(result, RunScope):
        return result
    assert isinstance(result, Refusal)  # interactive=False never asks
    cls = (
        ProductRequired if result.code in {"product-required", "no-default-product"} else ScopeError
    )
    raise cls(result.code, result.message)


def header_lines(scope: RunScope) -> list[str]:
    """The plain-words lede for the run header. Empty when the profile has no second product.

    No slug, no path, no digits: the operator reads this, and PROSPECTING.md's contract is
    "never a path, never a count". The machine detail belongs in :func:`record_lines`.
    """
    if not scope.multi:
        return []
    lines = [f"Prospecting for {scope.product_display}."]
    if scope.is_second_product and scope.shared_files:
        # One sentence, not one per file: seven near-identical lines would read as noise, and the
        # point is only that the operator learns what this run did NOT get its own copy of.
        # Named in the manifest's own order so the sentence reads the same run to run.
        what = [SHARED_BY_DEFAULT[n] for n in SHARED_BY_DEFAULT if n in scope.shared_files]
        joined = ", ".join(what[:-1]) + " and " + what[-1] if len(what) > 1 else what[0]
        lines.append(f"Still using the company-wide {joined}, written for {scope.default_display}.")
    return lines


def record_lines(scope: RunScope) -> list[str]:
    """The record section: which files are the product's own, which are shared, what was ignored."""
    if not scope.multi:
        return []
    lines = [f"product: {scope.product} ({scope.product_display})"]
    if scope.own_files:
        lines.append("own files: " + ", ".join(scope.own_files))
    if scope.shared_files:
        lines.append("shared with the default product: " + ", ".join(scope.shared_files))
    if scope.ignored_dirs:
        lines.append(
            "ignored (not in PROFILE.md products): "
            + ", ".join(_clean(d) for d in scope.ignored_dirs)
        )
    return lines


ALL_PRODUCTS_TITLE = "All products"


def status_title(scope: RunScope) -> str:
    """The pasted status block is title-labelled until the ledger is per product."""
    return ALL_PRODUCTS_TITLE if scope.is_second_product else ""


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gtm_core.run_scope", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    r = sub.add_parser("resolve", help="decide the run's product")
    r.add_argument("--profile", required=True)
    r.add_argument("--product")
    r.add_argument("--overlay")
    r.add_argument("--unattended", action="store_true", help="never ask; refuse instead")
    r.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = resolve(
        args.profile, product=args.product, overlay=args.overlay, interactive=not args.unattended
    )
    if isinstance(result, Refusal):
        print(f"[run_scope] {result.message}", file=sys.stderr)
        print(f"[run_scope] code: {result.code}", file=sys.stderr)
        return 2
    if isinstance(result, Ask):
        if args.json:
            print(
                json.dumps(
                    {
                        "ask": [{"slug": o.slug, "name": o.name} for o in result.options],
                        "default": result.default,
                    }
                )
            )
        else:
            for o in result.options:
                mark = " (Recommended)" if o.slug == result.default else ""
                print(f"ask: {o.slug}|{o.name}{mark}")
        return 3
    if not result.multi:
        return 0  # a profile with no second product prints nothing new
    if args.json:
        print(
            json.dumps(
                {
                    "product": result.product,
                    "lede": header_lines(result),
                    "record": record_lines(result),
                    "status_title": status_title(result),
                }
            )
        )
        return 0
    print(f"product={result.product}")
    for line in header_lines(result):
        print(line)
    print("---")
    for line in record_lines(result):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
