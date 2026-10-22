#!/usr/bin/env python3
"""Re-queue failed and dead embeddings so the embed worker retries them now.

Resets the retry state (attempt count and next retry time) so each event gets
the full retry budget again. Events written with embeddings off ('skipped')
are left alone.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))


REQUEUE_SQL = """
    UPDATE events
    SET index_status = 'pending', index_attempts = 0, index_retry_at = NULL
    WHERE index_status IN ('failed', 'pending', 'dead')
"""


async def main() -> None:
    from db.connection import init_db, close_db, get_pool

    await init_db()
    pool = get_pool()
    result = await pool.execute(REQUEUE_SQL)
    print(result)
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
