"""Top-level entry point for the AURA Dashboard server and React frontend."""

import argparse
from aura.dashboard.server import run_server
from aura.dashboard.ui import main as run_streamlit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run AURA Dashboard")
    parser.add_argument(
        "--legacy-streamlit",
        action="store_true",
        help="Run legacy Streamlit UI instead of the modern React dashboard server",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    args = parser.parse_args()

    if args.legacy_streamlit:
        run_streamlit()
    else:
        print(f"🚀 Starting AURA React Dashboard server on http://localhost:{args.port} ...")
        run_server(host=args.host, port=args.port)
