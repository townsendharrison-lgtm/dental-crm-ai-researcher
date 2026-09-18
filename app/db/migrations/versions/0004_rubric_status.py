"""Additive school rubric approval status for Phase 6. Never deletes data."""
from alembic import op
import sqlalchemy as sa

revision = "0004_rubric_status"
down_revision = "0003_page_fetch_cache"
branch_labels = None
depends_on = None


def upgrade():
    schema = op.get_context().version_table_schema
    op.add_column("schools", sa.Column("rubric_status", sa.String(length=20), server_default="draft", nullable=False), schema=schema)
    op.add_column("schools", sa.Column("rubric_approved_at", sa.DateTime(timezone=True), nullable=True), schema=schema)
    op.add_column("schools", sa.Column("rubric_approved_by", sa.Text(), nullable=True), schema=schema)
    op.create_check_constraint("schools_rubric_status", "schools", "rubric_status IN ('draft','approved')", schema=schema)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled: preserve rubric approval history")
