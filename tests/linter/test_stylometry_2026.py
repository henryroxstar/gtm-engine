from content_linter import lint_prose_quality


def test_vocabulary_density_scoring():
    # 3+ AI words in a paragraph should fail
    text = "This significant and crucial update offers comprehensive insights into the landscape. We must leverage and elevate our robust tools."
    violations = lint_prose_quality(text)
    assert any(v.rule == "ai-vocabulary-density" for v in violations)

    # Less than 3 should pass
    text_ok = "This is a significant update for our robust tools."
    violations_ok = lint_prose_quality(text_ok)
    assert not any(v.rule == "ai-vocabulary-density" for v in violations_ok)


def test_reveal_bridges():
    bridges = [
        "The result? We won.",
        "It's not X, it's Y.",
        "Stop guessing, start knowing.",
        "Here's what we did.",
        "Here's how we won.",
    ]
    for b in bridges:
        violations = lint_prose_quality(b)
        assert any(v.rule == "reveal-bridge" for v in violations), f"Failed to flag: {b}"


def test_performed_sincerity():
    sincere = ["Let me be honest with you.", "To be vulnerable for a second, I failed."]
    for s in sincere:
        violations = lint_prose_quality(s)
        assert any(v.rule == "performed-sincerity" for v in violations), f"Failed to flag: {s}"


def test_staccato_runs():
    staccato = "Short. Punchy. Done."
    violations = lint_prose_quality(staccato)
    assert any(v.rule == "staccato-run" for v in violations)


def test_staccato_does_not_fire_on_normal_prose():
    """Normal prose with short sentences (each >3 words) must not trigger staccato."""
    normal = "I ran to the store. He walked home quickly. She stayed behind the counter."
    violations = lint_prose_quality(normal)
    assert not any(v.rule == "staccato-run" for v in violations), (
        f"False positive: normal prose flagged as staccato: {[v for v in violations if v.rule == 'staccato-run']}"
    )
