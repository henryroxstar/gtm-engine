import pytest

from gtm_core.role_vocabulary.level import is_cxo


@pytest.mark.parametrize(
    "title",
    [
        "Chief Executive Officer",
        "CEO",
        "Chief Technology Officer",
        "CTO",
        "Chief Information Security Officer",
        "CISO",
        "Chief Information Officer",
        "CIO",
        "Chief Product Officer",
        "CPO",
        "Chief Revenue Officer",
        "CRO",
        "Chief Operating Officer",
        "COO",
        "Chief Data Officer",
        "Co-Founder & CEO",
        "Chief Compliance Officer",
        "Vice President & Chief Technology Officer",
        "SVP, Chief Information Security Officer",
        "VP, Chief Information Officer",
        "EVP and Chief Product Officer",
        "Vice President & CTO",
        "C.E.O.",
        "C.T.O.",
        "C.I.S.O.",
        "C.I.O.",
        "C.P.O.",
        "Chief Information & Technology Officer",
        "Chief Information/Technology Officer",
        "Chief Strategy & Innovation Officer",
        "Chief Executive",
    ],
)
def test_is_cxo_positive(title: str) -> None:
    assert is_cxo(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "VP of Engineering",
        "Director of Information Security",
        "Head of AI",
        "Lead Architect",
        "Senior Software Engineer",
        "Security Analyst",
        "Chief Architect",
        "Principal Architect",
        "Chief of Staff",
        "Office of the CEO",
        "Office of the C.E.O.",
        "VP, Chief InfoSec Office Staff",
        "Deputy CISO",
        "Assistant to the CEO",
        "",
        "   ",
    ],
)
def test_is_cxo_negative(title: str) -> None:
    assert is_cxo(title) is False
