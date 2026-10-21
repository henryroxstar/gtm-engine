from gtm_core.visuals.compiler import VIEWPORT_SPECS, LayoutCompiler
from gtm_core.visuals.schema import CVESpec, StatCalloutBlock


def test_compiler_viewport_dimensions():
    spec_social = CVESpec(
        title="Social Test",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[StatCalloutBlock(id="s1", value="1", label="L")],
    )
    compiler_social = LayoutCompiler(spec_social)
    res_social = compiler_social.compile()
    assert res_social["viewport"]["width"] == 1080
    assert res_social["viewport"]["height"] == 1350

    spec_pdf = CVESpec(
        title="PDF Test",
        profile="mockcorp",
        viewport="onepager-pdf",
        blocks=[StatCalloutBlock(id="s2", value="2", label="L")],
    )
    compiler_pdf = LayoutCompiler(spec_pdf)
    res_pdf = compiler_pdf.compile()
    assert res_pdf["viewport"]["width"] == 1240
    assert res_pdf["viewport"]["height"] == 1754


def test_compiler_deck_viewport_dimensions():
    spec_deck = CVESpec(
        title="Deck Test",
        profile="mockcorp",
        viewport="deck-16x9",
        blocks=[StatCalloutBlock(id="s3", value="3", label="L")],
    )
    compiler_deck = LayoutCompiler(spec_deck)
    res_deck = compiler_deck.compile()
    assert res_deck["viewport"]["width"] == 1920
    assert res_deck["viewport"]["height"] == 1080


def test_viewport_specs_structure():
    assert "social-4x5" in VIEWPORT_SPECS
    assert "onepager-pdf" in VIEWPORT_SPECS
    assert "deck-16x9" in VIEWPORT_SPECS
    for name, cfg in VIEWPORT_SPECS.items():
        assert "width" in cfg
        assert "height" in cfg
        assert "max_rows" in cfg
