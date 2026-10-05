from dataclasses import dataclass

from gtm_core.signal_quality import derive_signal_fit


@dataclass
class DummySource:
    id: str
    url: str
    premise: str
    attestation: str = "pool"


class DummyRegistry:
    def __init__(self, sources: list[DummySource]):
        self.sources = tuple(sources)
        self.by_id = {s.id: s for s in sources}


def test_derive_signal_fit_registered_sources() -> None:
    sources = [
        DummySource(
            id="src-1",
            url="https://sec.example/filing-1",
            premise="agents-in-operation",
            attestation="agentic",
        ),
        DummySource(
            id="src-2",
            url="https://sec.example/filing-2",
            premise="cross-org-agents",
            attestation="agentic",
        ),
        DummySource(
            id="src-3",
            url="https://registry.example/workflow-1",
            premise="cross-org-regulated-workflow",
            attestation="pool",
        ),
        DummySource(
            id="src-4",
            url="https://news.example/article-1",
            premise="",
            attestation="pool",
        ),
    ]
    reg = DummyRegistry(sources)

    # 1. attestation="agentic", premise="agents-in-operation", agent_kind="ai" -> 3
    assert derive_signal_fit("src-1", reg, agent_kind="ai") == 3
    assert derive_signal_fit("https://sec.example/filing-1", reg, agent_kind="ai") == 3

    # 2. attestation="agentic", premise="cross-org-agents", agent_kind="ai" -> 3
    assert derive_signal_fit("src-2", reg, agent_kind="ai") == 3

    # 3. attestation="pool", premise="cross-org-regulated-workflow", agent_kind="ai" -> 2
    assert derive_signal_fit("src-3", reg, agent_kind="ai") == 2

    # 4. attestation="pool", premise="cross-org-regulated-workflow", agent_kind="none" -> 1
    assert derive_signal_fit("src-3", reg, agent_kind="none") == 1

    # 5. Source without premise, agent_kind="none" -> 0
    assert derive_signal_fit("src-4", reg, agent_kind="none") == 0


def test_derive_signal_fit_unregistered_sources() -> None:
    reg = DummyRegistry([])

    # Unregistered with recorded_fit=2 -> 2
    assert (
        derive_signal_fit(
            "https://unregistered.example/item",
            reg,
            agent_kind="ai",
            recorded_fit=2,
        )
        == 2
    )

    # Unregistered with recorded_fit=None -> 0
    assert (
        derive_signal_fit(
            "https://unregistered.example/item",
            reg,
            agent_kind="ai",
            recorded_fit=None,
        )
        == 0
    )
