"""Task modules. Importing this package registers every task with the Celery app."""

from worker.tasks import analytics, backtests, imports, maintenance

__all__ = ["analytics", "backtests", "imports", "maintenance"]
