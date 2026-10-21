"""Mission Control deterministic motion-graphics video engine."""


def validate_script(*args, **kwargs):
    from .validate import validate_script as _validate_script

    return _validate_script(*args, **kwargs)


__all__ = ["validate_script"]
