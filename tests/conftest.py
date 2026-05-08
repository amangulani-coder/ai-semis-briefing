"""Shared test setup for update_research.

The production script imports `feedparser` and constructs an Anthropic client
at module import time. We stub the former and provide a dummy API key so
tests can run hermetically without network access.
"""
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

if "feedparser" not in sys.modules:
    stub = types.ModuleType("feedparser")
    stub.parse = lambda url: types.SimpleNamespace(entries=[])
    sys.modules["feedparser"] = stub

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
