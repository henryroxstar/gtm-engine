from gtm_core.role_vocabulary import DEFAULT_VOCABULARY, parse
from gtm_core.role_vocabulary.level import level_of


def test_level_of_precedence():
    vocab = DEFAULT_VOCABULARY
    assert level_of("Head of AI Platform", "ai-platform", vocab) == "champion"
    assert level_of("VP Engineering", "cto", vocab) == "champion"
    assert level_of("Chief Information Security Officer", "ciso", vocab) == "economic-buyer"
    assert level_of("Chief of Staff", "any", vocab) == "unknown"
    assert level_of("Chief Architect", "architect", vocab) == "evaluator"
    assert level_of("Deputy CISO", "security", vocab) == "champion"
    assert level_of("SVP Partnerships", "partnerships", vocab) == "champion"
    assert level_of("Director of Security", "security", vocab) == "champion"
    assert level_of("Director of AI Platform", "ai-platform", vocab) == "champion"
    assert level_of("Vice President of Engineering", "cto", vocab) == "champion"
    assert level_of("Head of Brand Innovation", "innovation", vocab) == "unknown"

    # a Head/Director of a function that doesn't match the seat → unknown
    assert level_of("Head of Marketing", "security", vocab) == "unknown"

    # a non-English title → unknown
    assert level_of("Directeur de la sécurité", "security", vocab) == "unknown"


def test_tenant_level_cues_override():
    doc = {
        "default_persona": "owner",
        "segments": ["enterprise", "unspecified"],
        "persona": [{"name": "owner", "cues": ["owner"]}],
        "seat": [{"name": "exec", "personas": ["owner"], "stakes": []}],
        "level": {"economic-buyer": ["boss"], "champion": ["lead"]},
    }
    vocab = parse(doc, "test")
    # Boss -> economic buyer now, overriding defaults if implemented that way
    # PRD says override the defaults.
    assert level_of("The Boss", "exec", vocab) == "economic-buyer"
