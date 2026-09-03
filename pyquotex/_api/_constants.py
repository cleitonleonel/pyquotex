"""Shared module-level constants used by stable_api facade and _api mixins."""

import itertools
import time

# Default timeout (seconds) for async polling loops
DEFAULT_TIMEOUT: int = 30

# Monotonically increasing counter for WebSocket request indices.
# Seeded from the current millisecond timestamp so indices remain
# browser-style large integers while being globally unique across
# all workers and loop iterations within a process (fixes #85).
_request_counter = itertools.count(int(time.time() * 1000))
