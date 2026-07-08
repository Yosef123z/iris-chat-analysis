"""
Tests for POST /api/v1/owner/reports/sync

Covers:
- Sync stores report for a single business
- Re-syncing replaces the previous report for the same business
- Two businesses remain isolated
- Missing / mismatched fields return 422 validation errors
- Storage directory isolation (uses tmp_path via conftest client fixture)
"""

import pytest

from app.core.provider import get_owner_report_service
from app.services.owner_report_service import OwnerReportService
from tests.conftest import make_sync_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def do_sync(client, payload: dict) -> dict:
    response = client.post("/api/v1/owner/reports/sync", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Tests: sync endpoint — happy path
# ---------------------------------------------------------------------------


def test_sync_stores_report_and_returns_ok(client):
    payload = make_sync_payload()
    data = do_sync(client, payload)
    assert data == {"status": "ok"}


def test_sync_again_same_business_replaces_old_report(client, tmp_path):
    """Re-syncing for the same business_id must replace — not append."""
    owner_report_svc = OwnerReportService(storage_dir=tmp_path / "owner_reports")

    first = make_sync_payload(summary="First summary")
    owner_report_svc.sync_report(
        __import__("app.models.owner_chat", fromlist=["OwnerReportSyncRequest"])
        .OwnerReportSyncRequest.model_validate(first)
    )
    first_stored = owner_report_svc.get_report("biz-restaurant-demo")
    assert first_stored is not None
    assert first_stored.report.summary == "First summary"

    second = make_sync_payload(summary="Second summary — updated")
    owner_report_svc.sync_report(
        __import__("app.models.owner_chat", fromlist=["OwnerReportSyncRequest"])
        .OwnerReportSyncRequest.model_validate(second)
    )
    second_stored = owner_report_svc.get_report("biz-restaurant-demo")
    assert second_stored is not None
    assert second_stored.report.summary == "Second summary — updated"

    # Only one pickle file should exist (replace, not append)
    pkl_files = list((tmp_path / "owner_reports").glob("*.pkl"))
    assert len(pkl_files) == 1


def test_two_businesses_are_isolated(tmp_path):
    """Reports synced for different business IDs must not cross-contaminate."""
    svc = OwnerReportService(storage_dir=tmp_path / "owner_reports")
    from app.models.owner_chat import OwnerReportSyncRequest

    payload_a = make_sync_payload("biz-a", "Business A", summary="Revenue was 50k.")
    payload_b = make_sync_payload("biz-b", "Business B", summary="Revenue was 80k.")

    svc.sync_report(OwnerReportSyncRequest.model_validate(payload_a))
    svc.sync_report(OwnerReportSyncRequest.model_validate(payload_b))

    report_a = svc.get_report("biz-a")
    report_b = svc.get_report("biz-b")

    assert report_a is not None
    assert report_b is not None
    assert report_a.report.summary == "Revenue was 50k."
    assert report_b.report.summary == "Revenue was 80k."
    # Isolation: A's data is not in B's report
    assert "50k" not in report_b.report.summary
    assert "80k" not in report_a.report.summary


# ---------------------------------------------------------------------------
# Tests: persistence (load after restart)
# ---------------------------------------------------------------------------


def test_persisted_report_survives_service_restart(tmp_path):
    """OwnerReportService.load_persisted_reports() must restore synced data."""
    from app.models.owner_chat import OwnerReportSyncRequest

    original = OwnerReportService(storage_dir=tmp_path / "owner_reports")
    original.sync_report(OwnerReportSyncRequest.model_validate(make_sync_payload()))

    restarted = OwnerReportService(storage_dir=tmp_path / "owner_reports")
    loaded = restarted.load_persisted_reports()

    assert loaded == 1
    stored = restarted.get_report("biz-restaurant-demo")
    assert stored is not None
    assert stored.business_name == "Demo Restaurant"


def test_two_businesses_persist_and_reload_in_isolation(tmp_path):
    from app.models.owner_chat import OwnerReportSyncRequest

    original = OwnerReportService(storage_dir=tmp_path / "owner_reports")
    original.sync_report(OwnerReportSyncRequest.model_validate(
        make_sync_payload("biz-a", "Business A", summary="A summary")
    ))
    original.sync_report(OwnerReportSyncRequest.model_validate(
        make_sync_payload("biz-b", "Business B", summary="B summary")
    ))

    restarted = OwnerReportService(storage_dir=tmp_path / "owner_reports")
    loaded = restarted.load_persisted_reports()
    assert loaded == 2

    assert restarted.get_report("biz-a").report.summary == "A summary"
    assert restarted.get_report("biz-b").report.summary == "B summary"


# ---------------------------------------------------------------------------
# Tests: validation errors
# ---------------------------------------------------------------------------


def test_sync_missing_business_id_returns_422(client):
    payload = make_sync_payload()
    del payload["business_id"]
    response = client.post("/api/v1/owner/reports/sync", json=payload)
    assert response.status_code == 422


def test_sync_mismatched_report_business_id_returns_422(client):
    """report.businessId must equal business_id at the top level."""
    payload = make_sync_payload(business_id="biz-a")
    payload["report"]["businessId"] = "biz-b"  # mismatch
    response = client.post("/api/v1/owner/reports/sync", json=payload)
    assert response.status_code == 422


def test_sync_via_http_endpoint_stores_report(client):
    """End-to-end: POST to /api/v1/owner/reports/sync is routed correctly."""
    payload = make_sync_payload()
    data = do_sync(client, payload)
    assert data["status"] == "ok"


def test_sync_accepts_and_stores_metrics(client):
    """Backend can sync report + metrics; metrics must be stored."""
    from tests.conftest import make_sync_payload_with_metrics

    payload = make_sync_payload_with_metrics()
    data = do_sync(client, payload)
    assert data == {"status": "ok"}

    svc = client.app.dependency_overrides[get_owner_report_service]()
    stored = svc.get_report("biz-restaurant-demo")
    assert stored is not None
    assert stored.metrics is not None
    assert stored.metrics["ordersToday"] == 12
    assert stored.metrics["menuItemsList"][0]["name"] == "Classic Burger"


def test_sync_backward_compatible_with_report_only_payload(client):
    """Old payloads without metrics must still succeed and not crash storage."""
    payload = make_sync_payload()
    data = do_sync(client, payload)
    assert data == {"status": "ok"}

    svc = client.app.dependency_overrides[get_owner_report_service]()
    stored = svc.get_report("biz-restaurant-demo")
    assert stored is not None
    assert stored.metrics is None


def test_persisted_metrics_survive_service_restart(tmp_path):
    """Metrics must be reloaded from persisted owner report artifacts."""
    from app.models.owner_chat import OwnerReportSyncRequest
    from tests.conftest import make_sync_payload_with_metrics

    original = OwnerReportService(storage_dir=tmp_path / "owner_reports")
    original.sync_report(OwnerReportSyncRequest.model_validate(make_sync_payload_with_metrics()))

    restarted = OwnerReportService(storage_dir=tmp_path / "owner_reports")
    loaded = restarted.load_persisted_reports()
    assert loaded == 1
    stored = restarted.get_report("biz-restaurant-demo")
    assert stored is not None
    assert stored.metrics["openTicketsCount"] == 3
