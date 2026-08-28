# Deploying the AI Server to Render using `uv`

This guide explains how to deploy the **Dental CRM AI Server (FastAPI + LangGraph)** to [Render.com](https://render.com) using Astral's **`uv`** package manager for fast installs and execution.

---

## Method 1: Web Service on Render (Recommended Native `uv`)

1. Go to your [Render Dashboard](https://dashboard.render.com/) and click **New +** &rarr; **Web Service**.
2. Connect your Git repository.
3. Configure the service settings:
   - **Name**: `dental-crm-ai-server` (or your preferred name)
   - **Root Directory**: `ai-server`
   - **Environment / Runtime**: `Python`
   - **Build Command**:
     ```bash
     curl -LsSf https://astral.sh/uv/install.sh | sh && $HOME/.local/bin/uv pip install --system -r requirements.txt
     ```
   - **Start Command**:
     ```bash
     $HOME/.local/bin/uv run uvicorn main:app --host 0.0.0.0 --port $PORT
     ```

4. **Environment Variables**:
   Under **Advanced** &rarr; **Environment Variables**, add:
   | Key | Value / Description |
   |---|---|
   | `PYTHON_VERSION` | `3.12.0` |
   | `ENVIRONMENT` | `production` |
   | `OPENAI_API_KEY` | `sk-...` (Your OpenAI API key) |
   | `SUPABASE_URL` | `https://your-project.supabase.co` |
   | `SUPABASE_ANON_KEY` | `eyJ...` (Your Supabase Anon Key) |
   | `SUPABASE_SERVICE_ROLE_KEY` | `eyJ...` (Your Supabase Service Role Key) |
   | `CORS_ORIGINS` | `["*"]` (or your frontend domain, e.g. `https://your-crm.vercel.app`) |

5. Click **Create Web Service**.

---

## Method 2: Docker on Render (Zero-Setup `uv` Container)

The repository includes a ready-to-deploy [`Dockerfile`](file:///c:/Users/hashi/Documents/Fiverr%20Projects/ORDER0063%20-%20DENTAL-CRM/ai-server/Dockerfile) powered by `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`.

1. On Render, select **Docker** as the runtime.
2. Set **Root Directory** to `ai-server`.
3. Add the same Environment Variables listed above (`OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`).
4. Click **Deploy**.

---

## Method 3: 1-Click Blueprint (`render.yaml`)

1. In Render, click **New +** &rarr; **Blueprint**.
2. Select your repository.
3. Render will read the [`render.yaml`](file:///c:/Users/hashi/Documents/Fiverr%20Projects/ORDER0063%20-%20DENTAL-CRM/render.yaml) file at the repository root and configure the AI Server and Backend services automatically.

---

## Connecting the Frontend

Once the AI Server is deployed, Render will give you a public URL (e.g. `https://dental-crm-ai-server.onrender.com`).

In your frontend environment (`.env.production` on Vercel/Render):
```env
NEXT_PUBLIC_AI_SERVER_URL=https://dental-crm-ai-server.onrender.com
```
