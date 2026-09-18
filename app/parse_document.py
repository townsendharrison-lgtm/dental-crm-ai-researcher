"""Local, read-only parser inspection: python -m app.parse_document file.pdf."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from app.clients.fetch_client import DocumentError, DocumentParser
from app.config import get_settings


def main():
    parser = argparse.ArgumentParser(description="Inspect PDF/DOCX chunks locally; no DB or model calls")
    parser.add_argument("path", type=Path)
    parser.add_argument("--include-text", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    try:
        with args.path.open("rb") as source:
            data = source.read(settings.document_max_bytes + 1)
        chunks = DocumentParser(settings).parse(data, args.path.name)
        output = []
        for chunk in chunks:
            item = asdict(chunk)
            item["characters"] = len(chunk.text)
            item["content_hash"] = chunk.content_hash
            if not args.include_text:
                item.pop("text")
            output.append(item)
        print(json.dumps({"chunks": output}, indent=2))
    except (OSError, DocumentError) as exc:
        print(json.dumps({"error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
