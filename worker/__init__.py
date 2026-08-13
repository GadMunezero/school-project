"""Tradeloom background worker.

A separate deployable that imports the backend package. It owns no domain logic of its own:
every task is a thin, observable wrapper around a service call, so the same code runs whether it
is triggered by HTTP or by the queue.
"""
