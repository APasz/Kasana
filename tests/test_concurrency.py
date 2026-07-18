"""Regression coverage for the async-to-thread bridge."""

from kasana.katalog.metadata import ItemMatchContext
from kasana.katalog.models import ZaisanKind
from kasana.shared.concurrency import run_blocking


async def test_run_blocking_returns_worker_result() -> None:
    assert await run_blocking(lambda: "completed") == "completed"


async def test_run_blocking_returns_metadata_context() -> None:
    context = ItemMatchContext(
        item_id=1,
        title="Title",
        release_year=None,
        item_kind=ZaisanKind.MOVIE,
        root_tags=frozenset(),
        directory_title=None,
        path_year=None,
        external_identifiers=frozenset(),
    )
    assert await run_blocking(lambda: (context,)) == (context,)
