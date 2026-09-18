"""Additive page-fetch cache for Phase 3 web research. Never deletes existing data."""
from alembic import op
import sqlalchemy as sa

revision = "0003_page_fetch_cache"
down_revision = "0002_domain_models"
branch_labels = None
depends_on = None


def upgrade():
    schema = op.get_context().version_table_schema
    op.create_table(
        "page_fetch_cache",
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("fetch_method", sa.String(length=20), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("byte_size", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("url_hash ~ '^[0-9a-f]{64}$'", name="page_cache_sha256"),
        sa.CheckConstraint("url ~ '^https?://[^[:space:]]+$'", name="page_cache_url"),
        sa.CheckConstraint("btrim(content_text) <> ''", name="page_cache_text"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="page_cache_content_hash"),
        sa.CheckConstraint("fetch_method IN ('httpx','playwright')", name="page_cache_method"),
        sa.CheckConstraint("byte_size >= 0", name="page_cache_size"),
        sa.PrimaryKeyConstraint("url_hash"),
        schema=schema,
    )


def downgrade():
    raise RuntimeError("Destructive downgrade disabled: preserve page cache and research data")
