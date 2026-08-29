# Dental School Intelligence & Predictive Admission AI Server

A high-performance Python FastAPI and LangGraph multi-agent server that powers:
1. **Admissions Criteria Extraction & Evidence Verification**: Ingests dental school bulletins, website URLs, and ADEA official guide documents to extract verified prerequisite requirements, GPA/DAT averages, and Dean information with verbatim text citations.
2. **Student Profile vs. School Comparison**: Evaluates CRM student profiles (including automatic parsing of 36-page complete application PDFs and transcripts from Supabase Storage) against dental school criteria to produce calibrated 4-outcome admission probabilities (*Interview %*, *Acceptance %*, *Waitlist %*, *Rejection %*), prerequisite audit checklists, and admissions committee diagnostics.

---

## Tech Stack

- **Framework**: FastAPI (Python 3.12+)
- **Multi-Agent Orchestration**: LangGraph, LangChain Core
- **LLM Engine**: OpenAI GPT-4o / GPT-4o-mini
- **Document Processing**: `pypdf`, `BeautifulSoup4`, `httpx`
- **Database & Storage**: Supabase (PostgreSQL + Supabase Storage)
- **Package Manager & Execution**: Astral `uv`

---

## Quick Start (Local Development with `uv`)

### 1. Install Astral `uv`
```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Install Dependencies
```bash
uv pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your keys:
```env
PORT=8000
HOST=0.0.0.0
ENVIRONMENT=development
OPENAI_API_KEY=your_openai_api_key_here
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
CORS_ORIGINS=["http://localhost:3000"]
```

### 4. Start Server
```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive API documentation will be available at [http://localhost:8000/docs](http://localhost:8000/docs).

---

## Deploying to Render via GitHub

### 1. Create a New GitHub Repository
From inside the `ai-server` folder:
```bash
git init
git add .
git commit -m "Initial commit: Dental CRM AI Server"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/dental-crm-ai-server.git
git push -u origin main
```

### 2. Create Web Service on Render
1. Go to [Render Dashboard](https://dashboard.render.com/) &rarr; **New +** &rarr; **Web Service**.
2. Connect your new `dental-crm-ai-server` GitHub repository.
3. Configure:
   - **Runtime**: `Python`
   - **Build Command**:
     ```bash
     pip install uv && python -m uv pip install -r requirements.txt
     ```
   - **Start Command**:
     ```bash
     python -m uvicorn main:app --host 0.0.0.0 --port $PORT
     ```
4. Add your Environment Variables:
   - `OPENAI_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `PYTHON_VERSION`: `3.12.0`
   - `ENVIRONMENT`: `production`
5. Click **Create Web Service**.

---

## Running Automated Tests
```bash
uv run python tests/test_agents.py
```
