"""Allow admin manual overrides on school_raw_facts."""
from alembic import op

revision = "0007_manual_facts"
down_revision = "0006_pending_evidence"
branch_labels = None
depends_on = None


def upgrade():
    schema = op.get_context().version_table_schema
    op.drop_constraint("facts_source_type", "school_raw_facts", schema=schema, type_="check")
    op.drop_constraint("facts_source_required", "school_raw_facts", schema=schema, type_="check")
    op.drop_constraint("facts_url", "school_raw_facts", schema=schema, type_="check")
    op.create_check_constraint(
        "facts_source_type",
        "school_raw_facts",
        "source_type IN ('document','web','manual')",
        schema=schema,
    )
    op.create_check_constraint(
        "facts_source_required",
        "school_raw_facts",
        "(source_type = 'document' AND document_id IS NOT NULL) OR "
        "(source_type = 'web' AND source_url IS NOT NULL) OR "
        "(source_type = 'manual')",
        schema=schema,
    )
    op.create_check_constraint(
        "facts_url",
        "school_raw_facts",
        "source_url IS NULL OR source_url ~ '^(https?://[^[:space:]]+|manual:[^[:space:]]+)$'",
        schema=schema,
    )


def downgrade():
    raise RuntimeError("Destructive downgrade disabled: preserve manual fact history")
