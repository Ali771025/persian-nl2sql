"""Add query-to-knowledge provenance fields.

Revision ID: 20260717_0003
Revises: 20260716_0002
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260717_0003"
down_revision: str | None = "20260716_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {str(item["name"]) for item in inspector.get_columns("query_audit")}
    additions = (
        ("knowledge_snapshot_id", sa.String(64), "''", False),
        ("knowledge_generation", sa.Integer(), "0", False),
        ("schema_version", sa.String(120), None, True),
        ("recipe_id", sa.String(100), None, True),
    )
    for name, column_type, default, nullable in additions:
        if name in columns:
            continue
        op.add_column(
            "query_audit",
            sa.Column(
                name,
                column_type,
                nullable=nullable,
                server_default=sa.text(default) if default is not None else None,
            ),
        )
    indexes = {str(item["name"]) for item in sa.inspect(bind).get_indexes("query_audit")}
    if "ix_query_audit_snapshot_created_at" not in indexes:
        op.create_index(
            "ix_query_audit_snapshot_created_at",
            "query_audit",
            ["knowledge_snapshot_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_query_audit_snapshot_created_at", table_name="query_audit")
    with op.batch_alter_table("query_audit") as batch:
        for name in (
            "recipe_id",
            "schema_version",
            "knowledge_generation",
            "knowledge_snapshot_id",
        ):
            batch.drop_column(name)
