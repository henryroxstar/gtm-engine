"""Register a promoted profile as the workspace's ``profiles`` row.

Kept out of ``backend/routers/onboard.py`` so the live-DB tier can exercise the SQL with
only ``asyncpg`` installed (the router's import chain pulls the rate limiter).

The caller supplies an RLS-scoped connection (``workspace_scope``); the explicit
``workspace_id`` filters are defence-in-depth on top of RLS.
"""

from __future__ import annotations

from typing import Any

import asyncpg


async def register_promoted_profile(conn: Any, workspace_id: str, profile_name: str) -> bool:
    """Insert the profile row (idempotent) and make it the default only when the workspace
    has none. Returns the row's committed-to-be ``is_default``.

    The default is claimed with a conditional UPDATE inside a savepoint. Two concurrent
    promotes in a fresh workspace can both see "no default" — the loser then blocks on the
    ``profiles_one_default_per_workspace`` partial unique index and raises
    ``UniqueViolationError`` once the winner commits; the savepoint absorbs it so the loser
    registers as a non-default profile instead of failing the promote.
    """
    await conn.execute(
        """INSERT INTO profiles(workspace_id, profile_name)
           VALUES($1::uuid, $2)
           ON CONFLICT (workspace_id, profile_name) DO NOTHING""",
        workspace_id,
        profile_name,
    )
    try:
        async with conn.transaction():
            await conn.execute(
                """UPDATE profiles SET is_default = true
                   WHERE workspace_id = $1::uuid AND profile_name = $2
                     AND NOT EXISTS (
                         SELECT 1 FROM profiles
                         WHERE workspace_id = $1::uuid AND is_default
                     )""",
                workspace_id,
                profile_name,
            )
    except asyncpg.UniqueViolationError:
        pass
    return bool(
        await conn.fetchval(
            """SELECT is_default FROM profiles
               WHERE workspace_id = $1::uuid AND profile_name = $2""",
            workspace_id,
            profile_name,
        )
    )
