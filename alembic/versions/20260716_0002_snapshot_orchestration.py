"""Add observable, single-flight snapshot job orchestration.

Revision ID: 20260716_0002
Revises: 20260713_0001
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260716_0002"
down_revision: str | None = "20260713_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {str(item["name"]) for item in inspector.get_columns("admin_job")}
    additions = (
        ("phase", sa.String(40), "'queued'"),
        ("progress_percent", sa.Integer(), "0"),
        ("heartbeat_at", sa.DateTime(timezone=True), None),
        ("snapshot_id", sa.String(64), "''"),
        ("attempt", sa.Integer(), "1"),
        ("correlation_id", sa.String(64), "''"),
    )
    for name, column_type, default in additions:
        if name in columns:
            continue
        op.add_column(
            "admin_job",
            sa.Column(
                name,
                column_type,
                nullable=default is None,
                server_default=sa.text(default) if default is not None else None,
            ),
        )
    # No in-process runner survives a deployment migration. Close stale active rows
    # before creating the cross-replica single-flight constraint.
    op.execute(
        sa.text(
            """
            UPDATE admin_job
            SET state = 'failed',
                phase = 'interrupted',
                error_code = COALESCE(error_code, 'MigrationInterrupted'),
                finished_at = CURRENT_TIMESTAMP
            WHERE state IN ('queued', 'running')
            """
        )
    )
    indexes = {str(item["name"]) for item in sa.inspect(bind).get_indexes("admin_job")}
    if "uq_admin_job_active_kind" not in indexes:
        op.create_index(
            "uq_admin_job_active_kind",
            "admin_job",
            ["kind"],
            unique=True,
            sqlite_where=sa.text("state IN ('queued', 'running')"),
            postgresql_where=sa.text("state IN ('queued', 'running')"),
            mssql_where=sa.text("state IN ('queued', 'running')"),
        )


def downgrade() -> None:
    op.drop_index("uq_admin_job_active_kind", table_name="admin_job")
    with op.batch_alter_table("admin_job") as batch:
        for name in (
            "correlation_id",
            "attempt",
            "snapshot_id",
            "heartbeat_at",
            "progress_percent",
            "phase",
        ):
            batch.drop_column(name)
