"""Create the operational metadata schema.

Revision ID: 20260713_0001
Revises:
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "query_audit" not in existing_tables:
        op.create_table(
            "query_audit",
            sa.Column("request_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("principal_hash", sa.String(64), nullable=False),
            sa.Column("tenant_hash", sa.String(64), nullable=True),
            sa.Column("roles_json", sa.JSON(), nullable=False),
            sa.Column("question_hash", sa.String(64), nullable=False),
            sa.Column("intent", sa.String(32), nullable=False),
            sa.Column("generation_mode", sa.String(32), nullable=True),
            sa.Column("tables_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(40), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("row_count", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("request_id", name="pk_query_audit"),
        )
    inspector = sa.inspect(bind)
    audit_indexes = _index_names(inspector, "query_audit")
    for name, columns in (
        ("ix_query_audit_principal_hash", ["principal_hash"]),
        ("ix_query_audit_created_at", ["created_at"]),
        ("ix_query_audit_status_created_at", ["status", "created_at"]),
    ):
        if name not in audit_indexes:
            op.create_index(name, "query_audit", columns, unique=False)

    inspector = sa.inspect(bind)
    if "query_feedback" not in set(inspector.get_table_names()):
        op.create_table(
            "query_feedback",
            sa.Column("request_id", sa.String(64), nullable=False),
            sa.Column("principal_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("rating", sa.String(20), nullable=False),
            sa.Column("reason_code", sa.String(40), nullable=False),
            sa.Column("expected_recipe_id", sa.String(100), nullable=True),
            sa.ForeignKeyConstraint(
                ["request_id"],
                ["query_audit.request_id"],
                name="fk_query_feedback_request_id_query_audit",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint(
                "request_id",
                "principal_hash",
                name="pk_query_feedback",
            ),
        )
    inspector = sa.inspect(bind)
    feedback_indexes = _index_names(inspector, "query_feedback")
    for name, columns in (
        ("ix_query_feedback_created_at", ["created_at"]),
        ("ix_query_feedback_rating", ["rating"]),
    ):
        if name not in feedback_indexes:
            op.create_index(name, "query_feedback", columns, unique=False)

    inspector = sa.inspect(bind)
    if "admin_job" not in set(inspector.get_table_names()):
        op.create_table(
            "admin_job",
            sa.Column("job_id", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(40), nullable=False),
            sa.Column("state", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(120), nullable=True),
            sa.PrimaryKeyConstraint("job_id", name="pk_admin_job"),
        )
        op.create_index(
            "ix_admin_job_kind_state",
            "admin_job",
            ["kind", "state"],
            unique=False,
        )
        op.create_index(
            "ix_admin_job_created_at",
            "admin_job",
            ["created_at"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_admin_job_created_at", table_name="admin_job")
    op.drop_index("ix_admin_job_kind_state", table_name="admin_job")
    op.drop_table("admin_job")
    op.drop_index("ix_query_feedback_rating", table_name="query_feedback")
    op.drop_index("ix_query_feedback_created_at", table_name="query_feedback")
    op.drop_table("query_feedback")
    op.drop_index("ix_query_audit_status_created_at", table_name="query_audit")
    op.drop_index("ix_query_audit_created_at", table_name="query_audit")
    op.drop_index("ix_query_audit_principal_hash", table_name="query_audit")
    op.drop_table("query_audit")
