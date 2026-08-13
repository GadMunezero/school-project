"""Tenant-scoped data access. Every query here is confined to one organization."""

from tradeloom.repositories.base import Repository, TenantRepository

__all__ = ["Repository", "TenantRepository"]
