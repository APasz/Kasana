from kasana.katalog.public import KatalogClient, LibraryItemKind, ProgressUpdate


def test_public_surface_exposes_transport_contracts_and_typed_client() -> None:
    update = ProgressUpdate(position_seconds=12.5, duration_seconds=90.0)

    assert LibraryItemKind.EPISODE.value == "episode"
    assert update.completed is False
    assert KatalogClient.__name__ == "KatalogClient"
