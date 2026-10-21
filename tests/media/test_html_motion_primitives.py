"""Tests for HTML Motion Primitives (spring singularity, WAAPI sequence, splitText, perceptualScale).

Covers Test Plan T8, T9, T10, T11.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOTION_JS_PATH = ROOT / "gtm_core" / "screen_ui" / "html" / "_shared" / "motion.js"


def _run_node_script(script: str) -> dict:
    cmd = ["node", "-e", script]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, (
        f"Node script failed ({proc.returncode}):\n{proc.stderr}\nScript:\n{script}"
    )
    return json.loads(proc.stdout)


def test_t8_critically_damped_spring_singularity():
    """T8: Spring function produces finite, monotonic values for zeta >= 1.0 without NaN/Infinity."""
    script = f"""
    const m = require({json.dumps(str(MOTION_JS_PATH))});
    const ts = [0.0, 0.25, 0.5, 0.75, 1.0];
    const results = ts.map(t => ({{ t, val: m.spring(t, 1.0, 8) }}));
    console.log(JSON.stringify(results));
    """
    results = _run_node_script(script)
    assert len(results) == 5

    prev = -1.0
    for item in results:
        v = item["val"]
        assert isinstance(v, (int, float))
        assert not (v != v)  # not NaN
        assert v != float("inf") and v != float("-inf")
        assert 0.0 <= v <= 1.0
        assert v >= prev  # monotonic non-decreasing
        prev = v

    assert results[0]["t"] == 0.0
    assert results[0]["val"] == 0.0
    assert results[-1]["t"] == 1.0
    assert results[-1]["val"] == 1.0


def test_t9_waapi_sequence_clobber_fix_and_gap_holding():
    """T9: sequence() compiles into single animation, holding values during idle gaps without clobbering entrance."""
    script = f"""
    const m = require({json.dumps(str(MOTION_JS_PATH))});
    let captured = null;
    const mockEl = {{
      animate: (keyframes, options) => {{
        captured = {{ keyframes, options }};
        return captured;
      }}
    }};

    const phases = [
      {{
        startS: 0.0,
        endS: 0.9,
        fn: (p) => ({{ translateY: `${{120 * (1 - p)}}px`, opacity: p }})
      }},
      {{
        startS: 4.2,
        endS: 5.0,
        fn: (p) => ({{ translateY: `${{-80 * p}}px`, opacity: 1 - p }})
      }}
    ];

    const anim = m.sequence(mockEl, phases, 5.0);
    const keyframes = anim.keyframes;

    // Filter idle window keyframes: t in [0.9, 4.2] => offset in [0.18, 0.84]
    const idleFrames = keyframes.filter(k => k.offset >= 0.18 - 1e-4 && k.offset <= 0.84 + 1e-4);

    console.log(JSON.stringify({{
      isArray: Array.isArray(keyframes),
      totalFrames: keyframes.length,
      startFrame: keyframes[0],
      duration: anim.options.duration,
      idleFrames
    }}));
    """
    res = _run_node_script(script)
    assert res["isArray"] is True
    assert res["totalFrames"] > 0
    assert res["duration"] == 5000

    # At t = 0s, opacity is 0 (asserting exit track backwards fill does not clobber entrance)
    assert res["startFrame"]["offset"] == 0
    assert res["startFrame"]["opacity"] == 0
    assert res["startFrame"]["translateY"] == "120px"

    # In idle window [0.9s, 4.2s], translateY is strictly 0px and opacity is strictly 1
    assert len(res["idleFrames"]) >= 2
    for frame in res["idleFrames"]:
        assert frame["translateY"] == "0px"
        assert frame["opacity"] == 1


def test_t10_safe_word_splitting_and_whitespace_preservation():
    """T10: splitText preserves inter-word whitespace, uses text nodes, sets aria-label, refuses XSS."""
    script = f"""
    class Node {{
      constructor(nodeType, textContent = "") {{
        this.nodeType = nodeType;
        this._textContent = textContent;
        this.childNodes = [];
        this.attributes = {{}};
        this.className = "";
        this.tagName = "";
      }}
      get textContent() {{
        if (this.nodeType === 3) return this._textContent;
        return this.childNodes.length > 0 ? this.childNodes.map(c => c.textContent).join("") : this._textContent;
      }}
      set textContent(v) {{
        this._textContent = v;
        this.childNodes = [];
      }}
      appendChild(child) {{
        this.childNodes.push(child);
        return child;
      }}
      setAttribute(k, v) {{ this.attributes[k] = v; }}
      getAttribute(k) {{ return this.attributes[k]; }}
      querySelectorAll(sel) {{
        const res = [];
        const walk = (n) => {{
          for (const c of n.childNodes) {{
            if (sel.startsWith(".") && c.className.split(" ").includes(sel.slice(1))) res.push(c);
            else if (c.tagName && c.tagName.toLowerCase() === sel.toLowerCase()) res.push(c);
            walk(c);
          }}
        }};
        walk(this);
        return res;
      }}
    }}

    global.document = {{
      createElement: (tag) => {{ const el = new Node(1); el.tagName = tag; return el; }},
      createTextNode: (t) => new Node(3, t)
    }};

    const m = require({json.dumps(str(MOTION_JS_PATH))});
    const el = new Node(1, "Instant Verification");
    const units = m.splitText(el, {{ by: "words" }});

    const splitUnitCount = el.querySelectorAll(".gtm-split-unit").length;
    const unitTexts = units.map(u => u.textContent);
    const middleChild = el.childNodes[1];
    const isMiddleWhitespaceTextNode = middleChild && middleChild.nodeType === 3 && middleChild.textContent === " ";
    const ariaLabel = el.getAttribute("aria-label");

    // Hostile string
    const hostile = new Node(1, '<img src=x onerror=alert(1)>');
    m.splitText(hostile, {{ by: "words" }});
    const imgCount = hostile.querySelectorAll("img").length;

    console.log(JSON.stringify({{
      splitUnitCount,
      unitTexts,
      isMiddleWhitespaceTextNode,
      ariaLabel,
      imgCount
    }}));
    """
    res = _run_node_script(script)
    assert res["splitUnitCount"] == 2
    assert res["unitTexts"] == ["Instant", "Verification"]
    assert res["isMiddleWhitespaceTextNode"] is True
    assert res["ariaLabel"] == "Instant Verification"
    assert res["imgCount"] == 0


def test_t11_perceptual_scale_interpolation():
    """T11: perceptualScale performs geometric scale interpolation and throws RangeError on non-positive scale."""
    script = f"""
    const m = require({json.dumps(str(MOTION_JS_PATH))});
    const mid = m.perceptualScale(1.0, 4.0, 0.5);

    let err1 = null;
    try {{
      m.perceptualScale(0, 4, 0.5);
    }} catch (e) {{
      err1 = e.name;
    }}

    let err2 = null;
    try {{
      m.perceptualScale(1, -2, 0.5);
    }} catch (e) {{
      err2 = e.name;
    }}

    console.log(JSON.stringify({{
      mid,
      err1,
      err2
    }}));
    """
    res = _run_node_script(script)
    assert res["mid"] == 2.0
    assert res["err1"] == "RangeError"
    assert res["err2"] == "RangeError"
