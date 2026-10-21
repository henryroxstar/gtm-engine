"""Command-line interface for the Composable Visual Engine (CVE)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from gtm_core.confine import ConfinementError
from gtm_core.paths import resolve_content_root, resolve_profiles_root
from gtm_core.visuals.brand_embed import FontMissingError
from gtm_core.visuals.compiler import DoesNotFitError
from gtm_core.visuals.rasterize import rasterize_cve_spec
from gtm_core.visuals.schema import CVESpec


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for CVE operations."""
    parser = argparse.ArgumentParser(description="Composable Visual Engine (CVE) CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render", help="Render a CVE visual spec")
    render_parser.add_argument("--spec", required=True, help="Path to cve_spec.json")
    render_parser.add_argument("--out", required=True, help="Output image/PDF path")
    render_parser.add_argument("--profile", required=True, help="Active profile name")
    render_parser.add_argument("--target", default=None, help="Viewport target override")
    render_parser.add_argument(
        "--strict-brand", action="store_true", help="Fail if exact font binaries missing"
    )

    args = parser.parse_args(argv)

    if args.command == "render":
        # Kill-switch check
        if os.getenv("GTM_DETERMINISTIC_VISUALS_ENABLED", "true").lower() == "false":
            sys.stderr.write("GTM_DETERMINISTIC_VISUALS_ENABLED is false; aborting render.\n")
            sys.exit(2)

        spec_path = Path(args.spec)
        spec_dict = json.loads(spec_path.read_text(encoding="utf-8"))
        spec_dict["profile"] = args.profile
        if args.target is not None:
            spec_dict["viewport"] = args.target
        elif "viewport" not in spec_dict:
            spec_dict["viewport"] = "social-4x5"
        spec = CVESpec.model_validate(spec_dict)

        out_path = Path(args.out)
        content_root = resolve_content_root()
        profiles_root = resolve_profiles_root()

        try:
            result = rasterize_cve_spec(
                spec=spec,
                output_path=out_path,
                content_root=content_root,
                profiles_root=profiles_root,
                strict_brand=args.strict_brand,
            )
            status = "cached" if result["cached"] else "rendered"
            print(f"[{status}] {result['path']} (sha256: {result['sha256'][:12]})")
        except DoesNotFitError as exc:
            sys.stderr.write(
                f"Error: Visual layout exceeds canvas budget.\nDiagnostics: {json.dumps(exc.diagnostics, indent=2)}\n"
            )
            sys.exit(1)
        except FontMissingError as exc:
            sys.stderr.write(f"Error: Required brand font missing: {exc}\n")
            sys.exit(1)
        except ConfinementError as exc:
            sys.stderr.write(f"Security Error: {exc}\n")
            sys.exit(1)


if __name__ == "__main__":
    main()
