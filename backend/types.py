"""Small reusable type aliases shared across the backend package."""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer

# A client-supplied id (path, query, or body field) that must be a UUID at the
# boundary — FastAPI/Pydantic validates the raw string and 422s a malformed one
# before any handler code runs (`AfterValidator(str)` then coerces the validated
# `uuid.UUID` to its canonical lowercase `str`, which is what every downstream
# consumer — asyncpg binds, in-process run-state keys, JSON responses — expects).
#
# `PlainSerializer` tells pydantic the value is already a plain string at
# serialization time. Without it, `.model_dump()`/`.model_dump_json()` on a model
# using this alias emits a `PydanticSerializationUnexpectedValue` warning: pydantic's
# core schema still expects a `uuid.UUID` internally even though the validator has
# already replaced the value with a `str` (this was RunRequest.agent_id's prior
# latent warning, using the same Annotated shape inline without a serializer). The
# JSON Schema / OpenAPI shape is unaffected either way — still `type: string,
# format: uuid`, exactly as a bare `uuid.UUID` annotation produces.
UuidStr = Annotated[
    uuid.UUID,
    AfterValidator(str),
    PlainSerializer(lambda v: v, return_type=str),
]
