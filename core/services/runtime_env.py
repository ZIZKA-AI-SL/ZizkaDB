"""Which runtime mode this process runs in.

Development mode turns on the hardcoded dev API keys, the unauthenticated
``/v1/auth/dev-token`` route, one-click dashboard sign-in and per-process rate
limits. It is opt-in: only ``ENV=development`` (any case, surrounding spaces
ignored) enables it. Everything else, including an unset or misspelt ``ENV``,
is treated as production, so a deployment that forgets the variable fails
closed instead of open.
"""

import os


def env_name() -> str:
    """Normalised value of ``ENV``; empty when unset."""
    return os.getenv("ENV", "").strip().lower()


def is_dev_mode() -> bool:
    return env_name() == "development"
