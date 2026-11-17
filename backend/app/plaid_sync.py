"""Collect a complete Plaid Transactions Sync update before applying it.

This module deliberately does not store cursors or financial data. A caller must
serialize syncs per Item and apply the returned changes idempotently before
advancing its durable cursor.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


class PaginationMutation(Exception):
    """Plaid reported TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION."""


@dataclass(frozen=True)
class SyncUpdate:
    added: list[dict[str, Any]]
    modified: list[dict[str, Any]]
    removed: list[dict[str, Any]]
    next_cursor: str


def collect_update(
    fetch_page: Callable[[str | None], Mapping[str, Any]],
    cursor: str | None,
    *,
    max_restarts: int = 3,
    max_pages: int = 1000,
) -> SyncUpdate:
    """Fetch all pages, restarting from the original cursor after a mutation.

    ``fetch_page`` must translate Plaid's pagination-mutation error into
    PaginationMutation and propagate all other errors. Nothing is persisted
    until the caller receives a complete update.
    """
    if max_restarts < 0 or max_pages < 1:
        raise ValueError("Invalid pagination limits")
    for attempt in range(max_restarts + 1):
        current = cursor
        added: list[dict[str, Any]] = []
        modified: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        try:
            for _ in range(max_pages):
                page = fetch_page(current)
                next_cursor = page.get("next_cursor")
                has_more = page.get("has_more")
                if not isinstance(next_cursor, str) or not next_cursor or not isinstance(has_more, bool):
                    raise ValueError("Invalid Plaid sync pagination response")
                for key, target in (("added", added), ("modified", modified), ("removed", removed)):
                    entries = page.get(key)
                    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                        raise ValueError("Invalid Plaid sync transaction response")
                    target.extend(entries)
                if not has_more:
                    return SyncUpdate(added, modified, removed, next_cursor)
                if next_cursor == current:
                    raise ValueError("Plaid sync cursor did not advance")
                current = next_cursor
            raise ValueError("Plaid sync pagination limit exceeded")
        except PaginationMutation:
            if attempt == max_restarts:
                raise
    raise AssertionError("Unreachable")
