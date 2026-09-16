from __future__ import annotations


class OnboardingInputError(ValueError):
    """The caller's own source or request cannot be onboarded; the message is safe to show them."""


class OnboardingExtractError(ValueError):
    """The brain's output is not a usable draft; the message may carry model output, so log it only."""
