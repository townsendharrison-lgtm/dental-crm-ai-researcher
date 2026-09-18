# Phase 8 — Node ↔ Python Integration

## Auth choice

Shared secret header **`X-School-AI-Key`**.

| Side | Env var |
| --- | --- |
| Python | `INTERNAL_API_SECRET` |
| Node | `SCHOOL_AI_INTERNAL_KEY` |
| Base URL (Node) | `SCHOOL_AI_SERVICE_URL` (fallback: `AI_SERVER_URL`) |

- `/health`, `/docs`, `/openapi.json` stay public.
- If `INTERNAL_API_SECRET` is empty, auth is disabled (local/offline only). **Set it in any shared environment.**

## Job completion choice: polling

Workers already write durable `jobs` rows. Node should **poll** `GET /jobs/{id}`
(via `schoolAiClient.waitForJob`) until `succeeded` / `failed` / `cancelled`.
Webhooks were deferred: polling needs no extra ingress or retry surface.

## Node client

`backend/src/services/schoolAiClient.ts`

```ts
import { createSchoolAiClient } from './schoolAiClient.js';
const ai = createSchoolAiClient();
const { school_id } = await ai.createSchool('Example Dental', 'https://example.edu');
const upload = await ai.uploadDocument(school_id, fileBlob, 'profile.pdf');
await ai.waitForJob(upload.job_id);
await ai.generateRubric(school_id);
await ai.approveRubric(school_id, 'ops@crm');
const result = await ai.scoreStudent(school_id, 'crm-student-1', { avg_gpa: 3.7 });
```

## Curl walkthrough

```powershell
$env:KEY="your-shared-secret"
$H=@{"X-School-AI-Key"=$env:KEY; "Content-Type"="application/json"}

# 1) Create school
curl.exe -s -X POST http://127.0.0.1:8000/schools -H "X-School-AI-Key: $env:KEY" -H "Content-Type: application/json" -d "{\"name\":\"Example Dental\",\"official_url\":\"https://example.edu/admissions\"}"

# 2) Upload document → poll job
curl.exe -s -X POST "http://127.0.0.1:8000/schools/<school_id>/documents" -H "X-School-AI-Key: $env:KEY" -F "file=@class_profile.pdf"
curl.exe -s http://127.0.0.1:8000/jobs/<job_id> -H "X-School-AI-Key: $env:KEY"

# 3) Optional research + normalization
curl.exe -s -X POST http://127.0.0.1:8000/schools/<school_id>/research -H "X-School-AI-Key: $env:KEY"
curl.exe -s -X POST http://127.0.0.1:8000/normalization/recompute -H "X-School-AI-Key: $env:KEY"

# 4) Rubric → approve → score
curl.exe -s -X POST http://127.0.0.1:8000/schools/<school_id>/rubric/generate -H "X-School-AI-Key: $env:KEY"
curl.exe -s -X POST http://127.0.0.1:8000/schools/<school_id>/rubric/approve -H "X-School-AI-Key: $env:KEY" -H "Content-Type: application/json" -d "{\"editor\":\"admin\"}"
curl.exe -s -X POST http://127.0.0.1:8000/schools/<school_id>/score -H "X-School-AI-Key: $env:KEY" -H "Content-Type: application/json" -d "{\"student_id\":\"crm-1\",\"attributes\":{\"avg_gpa\":3.7,\"avg_dat_aa\":20}}"
```

Workers required for document/research jobs:

```powershell
uv run python -m app.document_cli work
uv run python -m app.research_cli work
```

## Offline proof script

```powershell
uv run python -m scripts.phase8_roundtrip
```

## Frontend binding (CRM admin)

Browser never calls Python. Flow:

```text
Admin UI (Admission Research sidebar)
  → Node /api/school-ai/*  (Firebase JWT)
    → schoolAiClient + X-School-AI-Key
      → school-ai-service
```

### Node env (backend `.env`)

```env
SCHOOL_AI_SERVICE_URL=http://127.0.0.1:8000
SCHOOL_AI_INTERNAL_KEY=<same value as Python INTERNAL_API_SECRET>
```

### CRM migration

Apply `backend/migrations/059_schools_ai_school_id.sql` so CRM `schools.ai_school_id`
can store the Python school UUID.

### Admin UI

**Admission Research** (`/admin/admission-research`): link CRM schools, upload docs,
enqueue research, generate/approve rubrics, score students, list failed jobs.

School Selection remains manual optimization plans only.
