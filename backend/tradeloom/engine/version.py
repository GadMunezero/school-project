"""Engine version.

Bump this whenever a change can alter the numbers a run produces — execution ordering, fill
pricing, cost application, metric definitions. Stored results record the version that produced
them, so results from different engine versions are never silently compared as equivalent.

History
-------
1  Initial release: event-driven core, next-bar-open and current-bar-close execution,
   market/limit/stop/stop-limit orders, weighted-average position accounting.
"""

ENGINE_VERSION = "1"

__all__ = ["ENGINE_VERSION"]
