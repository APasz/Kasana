import pytest

import kasana.katalog.public as katalog_public
from kasana.katalog.public import (
    MAX_PLAYBACK_STATE_BATCH_SIZE,
    MAX_SUBTITLE_TIMING_OFFSET_MILLISECONDS,
    CollectionCreate,
    KatalogClient,
    LibraryItemKind,
    ProgressUpdate,
    WatchOrderCreate,
    WatchOrderGenerationRequest,
)


def test_public_surface_exposes_transport_contracts_and_typed_client() -> None:
    update = ProgressUpdate(position_seconds=12.5, duration_seconds=90.0)

    assert LibraryItemKind.EPISODE.value == "episode"
    assert update.completed is False
    assert KatalogClient.__name__ == "KatalogClient"
    assert CollectionCreate(name="Stargate").name == "Stargate"
    assert MAX_PLAYBACK_STATE_BATCH_SIZE == 500
    assert MAX_SUBTITLE_TIMING_OFFSET_MILLISECONDS == 30_000
    assert "WatchOrderKind" not in katalog_public.__all__
    assert "WatchOrderGenerationMode" not in katalog_public.__all__


def test_watch_order_contract_rejects_legacy_kind_and_generator_fields() -> None:
    for field, value in (("kind", "custom"), ("generation_mode", "release")):
        with pytest.raises(ValueError):
            WatchOrderCreate.model_validate(
                {
                    "expected_collection_revision": 1,
                    "name": "Named route",
                    field: value,
                }
            )
    with pytest.raises(ValueError):
        WatchOrderGenerationRequest.model_validate({"expected_revision": 1, "mode": "air"})
    with pytest.raises(ValueError, match="Original release"):
        WatchOrderCreate(
            expected_collection_revision=1,
            name="Named route",
            generate_original_release=True,
            copy_from_order_id=1,
        )
