# Fixtures

All documents are clearly labelled synthetic and contain no real student data.

- `class_profile.pdf`: overall GPA 3.7, science GPA 3.6 and class size 100, with
  no DAT or shadowing requirements.
- `mission_values.pdf`: mission, community service and values, with no numeric
  admission requirements.
- `scanned_profile.pdf`: image-only page exercising the OCR fallback.

The PDFs were rendered and visually inspected. Rebuild them using
`python app/tests/fixtures/generate_documents.py`. GPT-4o responses are mocked via
the real SDK's HTTP transport in `test_document_extraction.py`; fixtures do not
require live API credentials. DOCX files are generated in memory by tests.

The small taxonomy in extraction/workflow unit tests is **test-only**. Production
extraction uses `client-criteria-v1` from `app/factor_taxonomy.py` (fourteen
categories). Workflow orchestration tests live in `test_document_workflow.py` and
`test_document_api.py` and do not write to the production schema.
