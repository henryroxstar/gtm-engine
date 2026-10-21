"""CLI tests for gtm_core.diagrams.

Tests invoking `python -m gtm_core.diagrams render` and `extract`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_cli_extract_mermaid(tmp_path: Path):
    mmd_file = tmp_path / "test.mmd"
    mmd_file.write_text(
        """graph TD
    A[Client] -->|HTTP| B[API Gateway]
    B --> C[(Database)]
""",
        encoding="utf-8",
    )

    cmd = [sys.executable, "-m", "gtm_core.diagrams", "extract", str(mmd_file), "--json"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)

    data = json.loads(res.stdout)
    assert "nodes" in data or "diagrams" in data


def test_cli_render_mermaid_to_svg(tmp_path: Path):
    mmd_file = tmp_path / "test.mmd"
    mmd_file.write_text(
        """graph LR
    User[User App] --> Svc[Core Service]
""",
        encoding="utf-8",
    )
    out_svg = tmp_path / "output.svg"

    cmd = [
        sys.executable,
        "-m",
        "gtm_core.diagrams",
        "render",
        "--input",
        str(mmd_file),
        "--out",
        str(out_svg),
        "--format",
        "svg",
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)

    assert out_svg.exists()
    content = out_svg.read_text(encoding="utf-8")
    assert "<svg" in content
    assert "Core Service" in content


def test_cli_render_creates_nested_parent_dirs(tmp_path: Path):
    mmd_file = tmp_path / "test.mmd"
    mmd_file.write_text("graph LR\n  A --> B\n", encoding="utf-8")
    out_svg = tmp_path / "nested" / "sub" / "folder" / "output.svg"

    cmd = [
        sys.executable,
        "-m",
        "gtm_core.diagrams",
        "render",
        "--input",
        str(mmd_file),
        "--out",
        str(out_svg),
        "--format",
        "svg",
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert out_svg.exists()
    assert "<svg" in out_svg.read_text(encoding="utf-8")


def test_cli_missing_input_file_returns_error(tmp_path: Path):
    cmd = [
        sys.executable,
        "-m",
        "gtm_core.diagrams",
        "render",
        "--input",
        str(tmp_path / "nonexistent.mmd"),
        "--out",
        str(tmp_path / "out.svg"),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 2
    assert "error" in res.stderr.lower()


def test_cli_render_drawio_to_svg(tmp_path: Path):
    drawio_file = tmp_path / "test.drawio"
    drawio_file.write_text(
        """<mxfile host="app">
  <diagram id="page-1" name="Architecture">
    <mxGraphModel>
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="2" value="API Gateway" vertex="1" parent="1">
          <mxGeometry x="100" y="100" width="120" height="80" as="geometry"/>
        </mxCell>
        <mxCell id="3" value="Worker Pool" vertex="1" parent="1">
          <mxGeometry x="300" y="100" width="120" height="80" as="geometry"/>
        </mxCell>
        <mxCell id="4" value="Dispatches" edge="1" source="2" target="3" parent="1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>""",
        encoding="utf-8",
    )
    out_svg = tmp_path / "drawio_out.svg"

    cmd = [
        sys.executable,
        "-m",
        "gtm_core.diagrams",
        "render",
        "--input",
        str(drawio_file),
        "--out",
        str(out_svg),
        "--format",
        "svg",
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert out_svg.exists()
    svg_text = out_svg.read_text(encoding="utf-8")
    assert "<svg" in svg_text
    assert "API Gateway" in svg_text
    assert "Worker Pool" in svg_text
