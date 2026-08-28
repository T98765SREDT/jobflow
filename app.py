#!/usr/bin/env python3
"""Run JobFlow locally with only the Python standard library."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from jobflow.server import build_server


ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="JobFlow remote job application tracker")
    parser.add_argument("--host", default=os.getenv("JOBFLOW_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("JOBFLOW_PORT", "8000")))
    parser.add_argument("--db", default=os.getenv("JOBFLOW_DB", str(ROOT / "data" / "jobflow.db")))
    parser.add_argument(
        "--seed-demo",
        action="store_true",
        help="add six sample applications when creating a brand-new database",
    )
    args = parser.parse_args()

    server = build_server(
        args.host,
        args.port,
        database_path=args.db,
        static_dir=ROOT / "static",
        seed_demo=args.seed_demo,
    )
    print(f"JobFlow is running at http://{args.host}:{server.server_port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping JobFlow...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
