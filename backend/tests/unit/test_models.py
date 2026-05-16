from __future__ import annotations

from datetime import UTC, datetime

from backend.models.api import SkillListItem, UploadResponse
from backend.models.audit import AuditRecord
from backend.models.skill import Bundle, Classification, SkillDoc


def test_skill_doc_roundtrip():
    doc = SkillDoc(
        id="x:1.0.0:abc",
        skill_id="x",
        name="X",
        uploader="u@org",
    )
    dumped = doc.model_dump(mode="json")
    again = SkillDoc.model_validate(dumped)
    assert again.skill_id == "x"
    assert again.status == "pending"
    assert again.classifier_status == "queued"


def test_classification_defaults():
    c = Classification()
    assert c.category == "uncategorized"
    assert c.classifier_version == "stub-v1"
    assert c.duplicate_candidates == []


def test_bundle_required_fields():
    b = Bundle(
        blob_url="http://x",
        checksum_sha256="abc",
        size_bytes=10,
        file_count=1,
    )
    assert b.size_bytes == 10


def test_audit_record_has_id_and_timestamp():
    a = AuditRecord(skill_id="x", action="upload", actor="u@org")
    assert a.id
    assert a.at.tzinfo is not None
    assert a.action == "upload"
    # New `actor_oid` field defaults to None for back-compat / system actors.
    assert a.actor_oid is None


def test_audit_record_accepts_actor_oid():
    a = AuditRecord(
        skill_id="x",
        action="approve",
        actor="alice@org",
        actor_oid="00000000-0000-0000-0000-000000000001",
    )
    assert a.actor_oid == "00000000-0000-0000-0000-000000000001"


def test_audit_record_accepts_admin_session_start_action():
    a = AuditRecord(skill_id="_system", action="admin_session_start", actor="alice@org")
    assert a.action == "admin_session_start"


def test_upload_response_serializes():
    r = UploadResponse(
        skill_id="x",
        version="1.0.0",
        status="pending",
        classifier_status="queued",
        uploaded_at=datetime.now(UTC),
    )
    out = r.model_dump(mode="json")
    assert out["skill_id"] == "x"
    assert out["status"] == "pending"


def test_skill_list_item_omits_secrets():
    # pending_bundle_b64 is on SkillDoc but not on SkillListItem — this protects
    # us from accidentally leaking staged bytes over the API.
    fields = SkillListItem.model_fields.keys()
    assert "pending_bundle_b64" not in fields
    assert "skill_md_text" not in fields
