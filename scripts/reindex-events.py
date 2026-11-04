#!/usr/bin/env python3
"""Re-queue failed and dead embeddings so the embed worker retries them now.

Resets the retry state (attempt count and next retry time) so each event gets
the full retry budget again. Events written with embeddings off ('skipped')
are left alone. Same as running, inside the worker container:

    docker exec zizkadb_embed_worker python workers/embed_worker.py --requeue
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from workers.embed_worker import REQUEUE_SQL, requeue  # noqa: E402,F401


if __name__ == "__main__":
    print(asyncio.run(requeue()))
