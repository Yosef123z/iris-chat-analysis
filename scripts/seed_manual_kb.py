"""Post a manual-testing KB payload to the public sync endpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    url = f"{args.base_url.rstrip('/')}/api/v1/business/knowledge-base/sync"
    response = httpx.post(url, json=payload, timeout=10)
    print(json.dumps(response.json(), indent=2))
    response.raise_for_status()


if __name__ == "__main__":
    main()
