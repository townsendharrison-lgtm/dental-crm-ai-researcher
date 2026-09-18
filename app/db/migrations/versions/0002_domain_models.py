"""Phase 1 domain tables, constraints and immutable override history.

This revision is a frozen snapshot; it does not import current ORM models.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_domain_models"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    schema = op.get_context().version_table_schema
    op.execute('CREATE SCHEMA IF NOT EXISTS extensions')
    op.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions')
    op.create_table('cross_school_stats',
    sa.Column('factor_key', sa.Text(), nullable=False),
    sa.Column('unit', sa.Text(), server_default='', nullable=False),
    sa.Column('sample_size', sa.Integer(), nullable=False),
    sa.Column('min_value', sa.Numeric(precision=24, scale=10), nullable=False),
    sa.Column('max_value', sa.Numeric(precision=24, scale=10), nullable=False),
    sa.Column('mean_value', sa.Numeric(precision=24, scale=10), nullable=False),
    sa.Column('stddev_value', sa.Numeric(precision=24, scale=10), nullable=False),
    sa.Column('percentiles', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('method', sa.Text(), nullable=False),
    sa.Column('provenance', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("btrim(factor_key) <> ''", name='stats_factor_nonempty'),
    sa.CheckConstraint("btrim(method) <> '' AND jsonb_typeof(provenance) = 'object' AND provenance <> '{}'::jsonb", name='stats_provenance'),
    sa.CheckConstraint("jsonb_typeof(percentiles) = 'object'", name='stats_percentiles_object'),
    sa.CheckConstraint('min_value <= mean_value AND mean_value <= max_value', name='stats_value_order'),
    sa.CheckConstraint('sample_size > 0', name='stats_sample_size'),
    sa.CheckConstraint('stddev_value >= 0', name='stats_stddev'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('factor_key', 'unit', name='stats_factor_unit_unique'),
    schema=schema
    )
    op.create_table('schools',
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('official_url', sa.Text(), nullable=False),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('ingestion_status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('ingestion_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ingestion_completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("btrim(name) <> ''", name='schools_name_nonempty'),
    sa.CheckConstraint("ingestion_status IN ('pending','processing','complete','failed')", name='schools_status'),
    sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name='schools_metadata_object'),
    sa.CheckConstraint("official_url ~ '^https?://[^[:space:]]+$'", name='schools_url'),
    sa.PrimaryKeyConstraint('id'),
    schema=schema
    )
    op.create_table('jobs',
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('school_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('result', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('error', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("btrim(type) <> ''", name='jobs_type_nonempty'),
    sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name='jobs_payload_object'),
    sa.CheckConstraint("status <> 'failed' OR error IS NOT NULL", name='jobs_failure_error'),
    sa.CheckConstraint("status IN ('pending','running','succeeded','failed','cancelled')", name='jobs_status'),
    sa.CheckConstraint('attempts >= 0', name='jobs_attempts'),
    sa.ForeignKeyConstraint(['school_id'], [schema + '.schools.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    schema=schema
    )
    op.create_index('ix_jobs_status_created', 'jobs', ['status', 'created_at'], unique=False, schema=schema)
    op.create_index('ix_jobs_school', 'jobs', ['school_id'], unique=False, schema=schema)
    op.create_table('rubric_factors',
    sa.Column('school_id', sa.Uuid(), nullable=False),
    sa.Column('factor_key', sa.Text(), nullable=False),
    sa.Column('value', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('weight', sa.Numeric(precision=14, scale=8), nullable=True),
    sa.Column('weight_source', sa.String(length=32), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('reasoning', sa.Text(), nullable=False),
    sa.Column('source_urls', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("array_position(source_urls, NULL) IS NULL AND array_position(source_urls, '') IS NULL AND array_to_string(source_urls, '') !~ '[[:space:]]'", name='rubric_sources_valid'),
    sa.CheckConstraint("btrim(factor_key) <> ''", name='rubric_factor_nonempty'),
    sa.CheckConstraint("btrim(reasoning) <> ''", name='rubric_reasoning_required'),
    sa.CheckConstraint("value IS DISTINCT FROM 'null'::jsonb", name='rubric_no_json_null'),
    sa.CheckConstraint("weight_source = 'manual_override' OR cardinality(source_urls) > 0", name='rubric_grounding_required'),
    sa.CheckConstraint("weight_source IN ('stated','cross_school_inferred','qualitative_inferred','manual_override')", name='rubric_weight_source'),
    sa.CheckConstraint('(value IS NOT NULL OR weight IS NOT NULL) OR confidence = 0', name='rubric_unknown_confidence'),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name='rubric_confidence'),
    sa.CheckConstraint("weight IS NULL OR (weight >= 0 AND weight < 'Infinity'::numeric)", name='rubric_weight_nonnegative'),
    sa.ForeignKeyConstraint(['school_id'], [schema + '.schools.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('school_id', 'factor_key', name='rubric_school_factor_unique'),
    schema=schema
    )
    op.create_table('school_documents',
    sa.Column('school_id', sa.Uuid(), nullable=False),
    sa.Column('filename', sa.Text(), nullable=False),
    sa.Column('media_type', sa.Text(), nullable=False),
    sa.Column('storage_bucket', sa.Text(), nullable=False),
    sa.Column('storage_key', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=True),
    sa.Column('source_type', sa.String(length=20), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('parsed_status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('parse_error', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("btrim(storage_bucket) <> '' AND btrim(storage_key) <> ''", name='documents_storage_nonempty'),
    sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name='documents_sha256'),
    sa.CheckConstraint("parsed_status IN ('pending','processing','complete','failed')", name='documents_status'),
    sa.CheckConstraint("source_type <> 'web' OR source_url IS NOT NULL", name='documents_web_source'),
    sa.CheckConstraint("source_type IN ('upload','web')", name='documents_source_type'),
    sa.CheckConstraint("source_url IS NULL OR source_url ~ '^https?://[^[:space:]]+$'", name='documents_url'),
    sa.CheckConstraint('byte_size IS NULL OR byte_size >= 0', name='documents_size'),
    sa.ForeignKeyConstraint(['school_id'], [schema + '.schools.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('school_id', 'content_hash', name='documents_school_hash_unique'),
    sa.UniqueConstraint('school_id', 'id', name='documents_school_id_unique'),
    schema=schema
    )
    op.create_table('scoring_runs',
    sa.Column('school_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Text(), nullable=False),
    sa.Column('score', sa.Numeric(precision=24, scale=10), nullable=False),
    sa.Column('per_factor_breakdown', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('rubric_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reasoning', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("btrim(reasoning) <> ''", name='scoring_reasoning_required'),
    sa.CheckConstraint("btrim(student_id) <> ''", name='scoring_student_nonempty'),
    sa.CheckConstraint("jsonb_typeof(per_factor_breakdown) = 'object'", name='scoring_breakdown_object'),
    sa.CheckConstraint("jsonb_typeof(rubric_snapshot) = 'object'", name='scoring_snapshot_object'),
    sa.ForeignKeyConstraint(['school_id'], [schema + '.schools.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    schema=schema
    )
    op.create_index('ix_scoring_school_created', 'scoring_runs', ['school_id', 'created_at'], unique=False, schema=schema)
    op.create_index('ix_scoring_student_created', 'scoring_runs', ['student_id', 'created_at'], unique=False, schema=schema)
    op.create_table('rubric_overrides',
    sa.Column('school_id', sa.Uuid(), nullable=False),
    sa.Column('factor_key', sa.Text(), nullable=False),
    sa.Column('old_value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('new_value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('editor', sa.Text(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("btrim(editor) <> '' AND btrim(reason) <> ''", name='overrides_attribution'),
    sa.CheckConstraint("jsonb_typeof(old_value) = 'object' AND jsonb_typeof(new_value) = 'object'", name='overrides_snapshots_objects'),
    sa.ForeignKeyConstraint(['school_id', 'factor_key'], [schema + '.rubric_factors.school_id', schema + '.rubric_factors.factor_key'], name='overrides_rubric_factor', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    schema=schema
    )
    op.create_index('ix_overrides_school_factor', 'rubric_overrides', ['school_id', 'factor_key'], unique=False, schema=schema)
    op.create_table('school_raw_facts',
    sa.Column('school_id', sa.Uuid(), nullable=False),
    sa.Column('factor_key', sa.Text(), nullable=False),
    sa.Column('value', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('unit', sa.Text(), nullable=True),
    sa.Column('source_type', sa.String(length=20), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('document_id', sa.Uuid(), nullable=True),
    sa.Column('raw_text_snippet', sa.Text(), nullable=True),
    sa.Column('page_number', sa.Integer(), nullable=True),
    sa.Column('section', sa.Text(), nullable=True),
    sa.Column('extracted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(source_type = 'document' AND document_id IS NOT NULL) OR (source_type = 'web' AND source_url IS NOT NULL)", name='facts_source_required'),
    sa.CheckConstraint("btrim(factor_key) <> ''", name='facts_factor_nonempty'),
    sa.CheckConstraint("source_type IN ('document','web')", name='facts_source_type'),
    sa.CheckConstraint("source_url IS NULL OR source_url ~ '^https?://[^[:space:]]+$'", name='facts_url'),
    sa.CheckConstraint("value IS DISTINCT FROM 'null'::jsonb", name='facts_no_json_null'),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name='facts_confidence'),
    sa.CheckConstraint('page_number IS NULL OR page_number > 0', name='facts_page'),
    sa.CheckConstraint('value IS NOT NULL OR confidence = 0', name='facts_unknown_confidence'),
    sa.ForeignKeyConstraint(['school_id', 'document_id'], [schema + '.school_documents.school_id', schema + '.school_documents.id'], name='facts_document_same_school', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['school_id'], [schema + '.schools.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    schema=schema
    )
    op.create_index('ix_facts_school_factor', 'school_raw_facts', ['school_id', 'factor_key'], unique=False, schema=schema)

    # These functions and triggers affect only the newly created service tables.
    op.execute(f'''CREATE FUNCTION "{schema}".touch_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END; $$''')
    for table in ("schools", "school_documents", "rubric_factors", "cross_school_stats", "jobs"):
        op.execute(f'''CREATE TRIGGER touch_updated_at BEFORE UPDATE ON "{schema}"."{table}"
            FOR EACH ROW EXECUTE FUNCTION "{schema}".touch_updated_at()''')
    op.execute(f'''CREATE FUNCTION "{schema}".reject_override_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Rubric override history is append-only' USING ERRCODE = '23514';
        END; $$''')
    op.execute(f'''CREATE TRIGGER protect_override_rows BEFORE UPDATE OR DELETE
        ON "{schema}".rubric_overrides FOR EACH ROW
        EXECUTE FUNCTION "{schema}".reject_override_mutation()''')
    op.execute(f'''CREATE TRIGGER protect_override_truncate BEFORE TRUNCATE
        ON "{schema}".rubric_overrides FOR EACH STATEMENT
        EXECUTE FUNCTION "{schema}".reject_override_mutation()''')


def downgrade():
    # The user requires preservation of all existing data, including audit history.
    raise RuntimeError("Destructive downgrade disabled: preserve Phase 1 data and use a forward migration")
