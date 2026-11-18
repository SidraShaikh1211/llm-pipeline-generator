from __future__ import annotations

from agent_worker.persistence.models import AuditLog, Base, Job, JobEvent, User


def test_metadata_has_expected_tables() -> None:
    names = set(Base.metadata.tables)
    assert names == {"users", "jobs", "job_events", "audit_log"}


def test_jobs_table_has_required_columns() -> None:
    cols = {c.name for c in Job.__table__.columns}
    expected = {
        "id",
        "user_id",
        "spec",
        "target_repo",
        "devbox_host",
        "stage",
        "plan",
        "pr_url",
        "error",
        "tokens_used",
        "workspace_path",
        "created_at",
        "updated_at",
        "finished_at",
    }
    assert expected <= cols


def test_audit_log_metadata_column_named_metadata() -> None:
    cols = {c.name for c in AuditLog.__table__.columns}
    assert "metadata" in cols
    assert "audit_metadata" not in cols


def test_user_and_job_event_tables_exist() -> None:
    assert User.__tablename__ == "users"
    assert JobEvent.__tablename__ == "job_events"
