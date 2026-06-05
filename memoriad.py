"""
[DEPRECATED] memoriad — use memoriad_global.py instead.

The per-project background daemon has been replaced by the REST server
(memoriad_global.py) which runs once on the mini server (port 19998)
and serves all projects. It includes the same session extraction and
FTS5 indexing logic.

Migration:
- The REST server runs on the mini server at 100.121.245.69:19998
- All commands go through memoria.py (HTTP client) targeting MEMORIA_SERVER
- No per-project daemon needed — the server handles everything globally
- Context state, compression, topics, proposals all available via REST

This file kept for reference only — will be removed in v3.
"""

import sys


def main():
    print(
        "[DEPRECATED] memoriad.py is replaced by memoriad_global.py (REST server).",
        file=sys.stderr,
    )
    print("Run the server on the mini server:", file=sys.stderr)
    print("  uvicorn memoriad_global:app --host 0.0.0.0 --port 19998", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
