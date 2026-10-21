"""Tests for backend/dispatch_headless.py CLI and dispatch logic."""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

from backend.dispatch_headless import (
    build_parser,
    dispatch_headless_run,
)


def test_parser_defaults():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.pack == "headless-content"
    assert args.variant == "content_pipeline"
    assert args.profile is None
    assert args.dry_run is False
    assert args.inputs == "{}"
    assert args.context == "{}"


def test_parser_custom_args():
    parser = build_parser()
    args = parser.parse_args(
        [
            "--pack",
            "custom-pack",
            "--variant",
            "v2",
            "--profile",
            "acme",
            "--dry-run",
            "--inputs",
            '{"seed": "keyword"}',
            "--context",
            '{"source": "cron"}',
        ]
    )
    assert args.pack == "custom-pack"
    assert args.variant == "v2"
    assert args.profile == "acme"
    assert args.dry_run is True
    assert json.loads(args.inputs) == {"seed": "keyword"}
    assert json.loads(args.context) == {"source": "cron"}


def test_dispatch_headless_run_executes_insert():
    async def _test():
        mock_conn = MagicMock()
        mock_conn.transaction.return_value.__aenter__ = AsyncMock()
        mock_conn.transaction.return_value.__aexit__ = AsyncMock()
        mock_conn.execute = AsyncMock()
        generated_run_id = uuid.uuid4()
        mock_conn.fetchrow = AsyncMock(return_value={"id": generated_run_id})

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock()

        ws_id = str(uuid.uuid4())
        run_id = await dispatch_headless_run(
            pool=mock_pool,
            workspace_id=ws_id,
            profile_name="acme",
            pack="headless-content",
            variant="content_pipeline",
            dry_run=True,
            inputs={"test": 1},
            context={"ctx": 2},
        )

        assert run_id == str(generated_run_id)
        assert mock_conn.fetchrow.await_count == 1
        call_args = mock_conn.fetchrow.call_args[0]
        assert "INSERT INTO runs" in call_args[0]
        assert call_args[2] == uuid.UUID(ws_id)
        assert call_args[3] == "acme"
        assert call_args[4].startswith("[pack] headless-content/content_pipeline")
        payload = json.loads(call_args[5])
        assert payload["mode"] == "pack"
        assert payload["dry_run"] is True
        assert payload["inputs"] == {"test": 1}

    asyncio.run(_test())
