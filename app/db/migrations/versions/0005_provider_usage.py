"""Additive append-only provider usage metering for Phase 9. Never deletes data."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_provider_usage"
down_revision = "0004_rubric_status"
branch_labels = None
depends_on = None


def upgrade():
    schema = op.get_context().version_table_schema
    op.create_table(
        "provider_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("service", sa.String(length=20), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(24, 10), nullable=True),
        sa.Column("latency_ms", sa.Numeric(24, 4), nullable=True),
        sa.Column("usage_day", sa.Date(), server_default=sa.text("CURRENT_DATE"), nullable=False),
        sa.CheckConstraint("service IN ('openai','tavily')", name="usage_service"),
        sa.CheckConstraint("btrim(operation) <> ''", name="usage_operation_nonempty"),
        sa.CheckConstraint("tokens IS NULL OR tokens >= 0", name="usage_tokens_nonneg"),
        sa.CheckConstraint("cost_usd IS NULL OR cost_usd >= 0", name="usage_cost_nonneg"),
        sa.ForeignKeyConstraint(["school_id"], [f"{schema}.schools.id"], ondelete="RESTRICT"),
        schema=schema,
    )
    op.create_index("ix_usage_day_school_service", "provider_usage_events",
                    ["usage_day", "school_id", "service"], schema=schema)
    op.create_index("ix_usage_school_created", "provider_usage_events",
                    ["school_id", "created_at"], schema=schema)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled: preserve provider usage history")
