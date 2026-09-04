"""Contracts for Kanvas's shell-wide operational alert feed."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from _pytest.monkeypatch import MonkeyPatch
from nicegui.client import Client
from nicegui.page import page
from pydantic import ValidationError
from starlette.requests import Request

from kasana.kanvas import dashboard
from kasana.kanvas.components.browser import BrowserComponent
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.profiles import SessionProfile
from kasana.kanvas.routes import api_administration
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.system_alerts import (
    SystemAlertActionKind,
    SystemAlertActionView,
    SystemAlertCode,
    SystemAlertFeedView,
    SystemAlertHistoryView,
    SystemAlertSeverity,
    SystemAlertView,
)
from kasana.katalog.public import (
    KatalogClientError,
    KatalogClientErrorKind,
    StatusResponse,
    SystemIncidentCode,
    SystemIncidentFeed,
    SystemIncidentResponse,
    SystemIncidentSeverity,
    UserRole,
    UserSummary,
)


def _profile(role: UserRole = UserRole.OWNER) -> SessionProfile:
    return SessionProfile(UserSummary(id=1, username="tester", role=role))


def _status(**changes: object) -> StatusResponse:
    values: dict[str, object] = {
        "database_revision": "revision",
        "database_healthy": True,
        "enabled_root_count": 2,
        "unavailable_root_count": 0,
        "item_count": 12,
        "media_file_count": 14,
        "available_file_count": 12,
        "unresolved_audit_issue_count": 0,
        "active_job_count": 0,
        "failed_job_count": 0,
        "interrupted_job_count": 0,
    }
    values.update(changes)
    return StatusResponse.model_validate(values)


async def test_system_alert_feed_derives_safe_administrator_conditions(
    monkeypatch: MonkeyPatch,
) -> None:
    observed_at = datetime(2026, 9, 4, tzinfo=UTC)

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_arguments: object) -> None:
            pass

        async def status(self) -> StatusResponse:
            return _status()

        async def system_incidents(self) -> SystemIncidentFeed:
            return SystemIncidentFeed(
                active=(
                    SystemIncidentResponse(
                        id=1,
                        code=SystemIncidentCode.DATABASE_UNHEALTHY,
                        severity=SystemIncidentSeverity.ERROR,
                        title="Catalogue database needs attention",
                        detail=(
                            "Katalog reported a database health problem. "
                            "Investigate the database service."
                        ),
                        first_detected_at=observed_at,
                        last_detected_at=observed_at,
                    ),
                    SystemIncidentResponse(
                        id=2,
                        code=SystemIncidentCode.LIBRARY_ROOT_UNAVAILABLE,
                        severity=SystemIncidentSeverity.WARNING,
                        title="2 library roots unavailable",
                        detail=(
                            "2 configured library roots are not accessible. "
                            "Check the disks or mounts, then rescan."
                        ),
                        first_detected_at=observed_at,
                        last_detected_at=observed_at,
                        acknowledged_at=observed_at,
                        acknowledged_by_user_id=1,
                    ),
                    SystemIncidentResponse(
                        id=3,
                        code=SystemIncidentCode.MAINTENANCE_JOBS_FAILED,
                        severity=SystemIncidentSeverity.WARNING,
                        title="Maintenance jobs need attention",
                        detail=(
                            "1 failed job and 2 interrupted jobs. "
                            "Review the job details before retrying work."
                        ),
                        first_detected_at=observed_at,
                        last_detected_at=observed_at,
                    ),
                ),
                history=(
                    SystemIncidentResponse(
                        id=4,
                        code=SystemIncidentCode.LIBRARY_ROOT_UNAVAILABLE,
                        severity=SystemIncidentSeverity.WARNING,
                        title="Library root unavailable",
                        detail=(
                            "A configured library root is not accessible. "
                            "Check the disk or mount, then rescan."
                        ),
                        first_detected_at=observed_at,
                        last_detected_at=observed_at,
                        resolved_at=observed_at,
                    ),
                ),
            )

    def fake_client(*_args: object, **_kwargs: object) -> FakeClient:
        return FakeClient()

    monkeypatch.setattr("kasana.kanvas.services.katalog.KatalogClient", fake_client)
    service = KanvasKatalogService(Kanvas_Settings())

    administrator_feed = await service.system_alert_feed(is_administrator=True)
    viewer_feed = await service.system_alert_feed(is_administrator=False)

    assert administrator_feed.connected is True
    assert [alert.id for alert in administrator_feed.alerts] == [
        "database-unhealthy",
        "library-roots-unavailable",
        "maintenance-jobs-failed",
    ]
    root_alert = administrator_feed.alerts[1]
    assert root_alert.code is SystemAlertCode.LIBRARY_ROOT_UNAVAILABLE
    assert root_alert.detail == (
        "2 configured library roots are not accessible. Check the disks or mounts, then rescan."
    )
    assert root_alert.incident_id == 2
    assert root_alert.acknowledged_at == observed_at
    assert root_alert.action.href == "/administration/libraries"
    assert administrator_feed.history == (
        SystemAlertHistoryView(
            incidentId=4,
            code=SystemAlertCode.LIBRARY_ROOT_UNAVAILABLE,
            severity=SystemAlertSeverity.WARNING,
            title="Library root unavailable",
            detail=(
                "A configured library root is not accessible. Check the disk or mount, then rescan."
            ),
            firstDetectedAt=observed_at,
            lastDetectedAt=observed_at,
            resolvedAt=observed_at,
        ),
    )
    assert viewer_feed == SystemAlertFeedView(connected=True)


async def test_system_alert_feed_preserves_a_safe_connection_alert(
    monkeypatch: MonkeyPatch,
) -> None:
    class OfflineClient:
        async def __aenter__(self) -> OfflineClient:
            return self

        async def __aexit__(self, *_arguments: object) -> None:
            pass

        async def status(self) -> StatusResponse:
            raise KatalogClientError(KatalogClientErrorKind.UNAVAILABLE, "connection refused")

    def offline_client(*_args: object, **_kwargs: object) -> OfflineClient:
        return OfflineClient()

    monkeypatch.setattr("kasana.kanvas.services.katalog.KatalogClient", offline_client)

    feed = await KanvasKatalogService(Kanvas_Settings()).system_alert_feed(is_administrator=False)

    assert feed.connected is False
    assert feed.alerts[0].code is SystemAlertCode.KATALOG_UNAVAILABLE
    assert feed.alerts[0].action.kind is SystemAlertActionKind.RETRY
    assert "connection refused" not in feed.alerts[0].detail


async def test_system_alert_endpoint_scopes_the_feed_to_the_active_profile(
    monkeypatch: MonkeyPatch,
) -> None:
    captured_administrator_flags: list[bool] = []
    feed = SystemAlertFeedView(
        connected=True,
        alerts=(
            SystemAlertView(
                id="maintenance-jobs-failed",
                code=SystemAlertCode.MAINTENANCE_JOBS_FAILED,
                severity=SystemAlertSeverity.WARNING,
                title="Maintenance job needs attention",
                detail="1 failed job. Review the job details before retrying work.",
                action=SystemAlertActionView(
                    kind=SystemAlertActionKind.NAVIGATE,
                    label="Open jobs",
                    href="/administration/jobs",
                ),
            ),
        ),
    )

    class AlertCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def system_alert_feed(self, *, is_administrator: bool) -> SystemAlertFeedView:
            captured_administrator_flags.append(is_administrator)
            return feed

    async def current_profile(_request: object) -> SessionProfile:
        return _profile(UserRole.USER)

    monkeypatch.setattr(api_administration, "data_profile", current_profile)
    monkeypatch.setattr(api_administration, "KanvasKatalogService", AlertCatalogue)

    response = await dashboard.system_alerts_data(
        Request({"type": "http", "query_string": b"", "headers": []})
    )

    assert response.status_code == 200
    assert captured_administrator_flags == [False]
    assert json.loads(bytes(response.body)) == {
        "connected": True,
        "alerts": [
            {
                "id": "maintenance-jobs-failed",
                "code": "maintenance_jobs_failed",
                "severity": "warning",
                "title": "Maintenance job needs attention",
                "detail": "1 failed job. Review the job details before retrying work.",
                "action": {
                    "kind": "navigate",
                    "label": "Open jobs",
                    "href": "/administration/jobs",
                },
            }
        ],
        "history": [],
    }


def test_page_shell_mounts_system_alerts_only_for_signed_in_profiles() -> None:
    with Client(page("")) as client:
        with page_shell(Kanvas_Settings(), "/", "Home", _profile()):
            pass
        mounted = [
            element
            for element in client.elements.values()
            if element.tag == BrowserComponent.SYSTEM_ALERTS
        ]

    assert len(mounted) == 1
    properties = mounted[0]._props  # pyright: ignore[reportPrivateUsage]
    assert properties["source"] == "/kanvas/data/system-alerts"
    assert properties["acknowledgement-source"] == "/kanvas/data/system-alerts"

    with Client(page("")) as client:
        with page_shell(Kanvas_Settings(), "/", "Home"):
            pass
        anonymous_alerts = [
            element
            for element in client.elements.values()
            if element.tag == BrowserComponent.SYSTEM_ALERTS
        ]

    assert anonymous_alerts == []


async def test_system_alert_acknowledgement_requires_an_administrator(
    monkeypatch: MonkeyPatch,
) -> None:
    acknowledged: list[tuple[int, int]] = []

    class AlertCatalogue:
        def __init__(self, _settings: Kanvas_Settings, user_id: int | None = None) -> None:
            assert user_id is not None
            self._user_id = user_id

        async def acknowledge_system_incident(self, incident_id: int) -> None:
            acknowledged.append((incident_id, self._user_id))

    async def administrator_profile(_request: object) -> SessionProfile:
        return _profile(UserRole.ADMIN)

    request = Request({"type": "http", "query_string": b"", "headers": []})
    monkeypatch.setattr(api_administration, "data_profile", administrator_profile)
    monkeypatch.setattr(api_administration, "KanvasKatalogService", AlertCatalogue)

    acknowledged_response = await dashboard.acknowledge_system_alert_data(9, request)

    assert acknowledged_response.status_code == 204
    assert acknowledged == [(9, 1)]

    async def viewer_profile(_request: object) -> SessionProfile:
        return _profile(UserRole.USER)

    monkeypatch.setattr(api_administration, "data_profile", viewer_profile)

    forbidden_response = await dashboard.acknowledge_system_alert_data(10, request)

    assert forbidden_response.status_code == 403
    assert acknowledged == [(9, 1)]


@pytest.mark.parametrize(
    ("kind", "href"),
    (
        (SystemAlertActionKind.NAVIGATE, None),
        (SystemAlertActionKind.RETRY, "/administration"),
        (SystemAlertActionKind.NAVIGATE, "//untrusted.example"),
        (SystemAlertActionKind.NAVIGATE, "/\\untrusted.example"),
        (SystemAlertActionKind.NAVIGATE, "/administration\n"),
    ),
)
def test_system_alert_actions_reject_ambiguous_targets(
    kind: SystemAlertActionKind, href: str | None
) -> None:
    with pytest.raises(ValidationError):
        SystemAlertActionView(kind=kind, label="Continue", href=href)


def test_transient_system_alerts_cannot_refer_to_a_durable_incident() -> None:
    with pytest.raises(ValidationError):
        SystemAlertView(
            id="browser-offline",
            code=SystemAlertCode.BROWSER_OFFLINE,
            severity=SystemAlertSeverity.ERROR,
            title="Connection lost",
            detail="This device is offline.",
            action=SystemAlertActionView(kind=SystemAlertActionKind.RETRY, label="Retry"),
            incidentId=1,
        )


def test_system_incident_contract_requires_a_complete_acknowledgement() -> None:
    observed_at = datetime(2026, 9, 4, tzinfo=UTC)
    active_incident = SystemIncidentResponse(
        id=1,
        code=SystemIncidentCode.LIBRARY_ROOT_UNAVAILABLE,
        severity=SystemIncidentSeverity.WARNING,
        title="Library root unavailable",
        detail="A configured library root is not accessible.",
        first_detected_at=observed_at,
        last_detected_at=observed_at,
    )
    values = active_incident.model_dump()

    with pytest.raises(ValidationError):
        SystemIncidentResponse.model_validate(values | {"acknowledged_at": observed_at})
    with pytest.raises(ValidationError):
        SystemIncidentResponse.model_validate(values | {"acknowledged_by_user_id": 1})

    recovered_incident = active_incident.model_copy(update={"resolved_at": observed_at})
    with pytest.raises(ValidationError):
        SystemIncidentFeed(active=(recovered_incident,))
    with pytest.raises(ValidationError):
        SystemIncidentFeed(history=(active_incident,))
