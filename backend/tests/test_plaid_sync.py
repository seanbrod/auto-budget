import pytest

from app.plaid_sync import PaginationMutation, collect_update


def page(cursor, *, more=False, added=None, modified=None, removed=None):
    return {"next_cursor": cursor, "has_more": more, "added": added or [],
            "modified": modified or [], "removed": removed or []}


def test_collects_every_page_before_returning():
    calls = []
    pages = {None: page("first", more=True, added=[{"transaction_id": "a"}]),
             "first": page("last", modified=[{"transaction_id": "b"}],
                           removed=[{"transaction_id": "c"}])}

    def fetch(cursor):
        calls.append(cursor)
        return pages[cursor]

    result = collect_update(fetch, None)
    assert calls == [None, "first"]
    assert result.next_cursor == "last"
    assert [result.added[0]["transaction_id"], result.modified[0]["transaction_id"],
            result.removed[0]["transaction_id"]] == ["a", "b", "c"]


def test_mutation_discards_partial_results_and_restarts_from_original_cursor():
    calls = []

    def fetch(cursor):
        calls.append(cursor)
        if len(calls) == 1:
            return page("partial", more=True, added=[{"transaction_id": "stale"}])
        if len(calls) == 2:
            raise PaginationMutation()
        return page("complete", added=[{"transaction_id": "fresh"}])

    result = collect_update(fetch, "original")
    assert calls == ["original", "partial", "original"]
    assert result.added == [{"transaction_id": "fresh"}]
    assert result.next_cursor == "complete"


def test_mutations_are_bounded():
    def fetch(_cursor):
        raise PaginationMutation()

    with pytest.raises(PaginationMutation):
        collect_update(fetch, None, max_restarts=1)


def test_other_errors_and_invalid_pages_never_return_a_cursor():
    with pytest.raises(RuntimeError):
        collect_update(lambda _: (_ for _ in ()).throw(RuntimeError("network error")), "old")
    with pytest.raises(ValueError):
        collect_update(lambda _: page("old", more=True), "old")
    with pytest.raises(ValueError):
        collect_update(lambda _: {"next_cursor": "new", "has_more": False}, "old")
