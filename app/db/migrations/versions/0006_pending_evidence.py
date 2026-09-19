"""Allow pending_evidence rubric slots for full taxonomy coverage."""
from alembic import op

revision = "0006_pending_evidence"
down_revision = "0005_provider_usage"
branch_labels = None
depends_on = None


def upgrade():
    schema = op.get_context().version_table_schema
    # Drop then recreate checks so pending_evidence rows can exist without citations.
    op.drop_constraint("rubric_weight_source", "rubric_factors", schema=schema, type_="check")
    op.drop_constraint("rubric_grounding_required", "rubric_factors", schema=schema, type_="check")
    op.create_check_constraint(
        "rubric_weight_source",
        "rubric_factors",
        "weight_source IN ('stated','cross_school_inferred','qualitative_inferred',"
        "'manual_override','pending_evidence')",
        schema=schema,
    )
    op.create_check_constraint(
        "rubric_grounding_required",
        "rubric_factors",
        "weight_source IN ('manual_override','pending_evidence') OR cardinality(source_urls) > 0",
        schema=schema,
    )


def downgrade():
    raise RuntimeError("Destructive downgrade disabled: preserve rubric factor history")
