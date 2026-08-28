FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Enable bytecode compilation and fast copy mode
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PYTHONUNBUFFERED=1

# Copy dependency files
COPY pyproject.toml requirements.txt ./

# Install dependencies with uv
RUN uv pip install --system -r requirements.txt

# Copy project files
COPY . .

# Expose port (Render overrides PORT at runtime)
EXPOSE 8000

# Start server using uv run uvicorn
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
