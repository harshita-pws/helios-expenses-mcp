"""Entry point for tools that load a server from a file path.

`mcp dev` / `mcp run` import the given file directly, which breaks the relative
imports inside the package. This shim re-exports the server object so
`uv run mcp dev server_entry.py` works.
"""

from helios_expenses.server import mcp

__all__ = ["mcp"]

if __name__ == "__main__":
    mcp.run()
