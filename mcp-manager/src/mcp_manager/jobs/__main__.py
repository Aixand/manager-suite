"""Preserve python -m mcp_manager.jobs for existing callers."""
from .executor import main

raise SystemExit(main())
