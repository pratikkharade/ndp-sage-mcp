"""Shared pytest configuration.

We skip the plugin-registry network refresh at import time so tests don't
depend on ECR availability. Individual tests can still exercise the refresh
by calling ``PluginRegistry(auto_refresh=True)`` with a mocked ``requests``.
"""

import os
import sys

os.environ.setdefault("SAGE_MCP_SKIP_REGISTRY_REFRESH", "1")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")

# ensure repo root on sys.path so `import sage_mcp` works
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
