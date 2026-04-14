"""Vercel serverless entry point — exposes the Flask app as a WSGI handler."""

import sys
import os

# Ensure project root and flight/ subpackage are importable
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, "flight"))

from app import app  # noqa: E402 — path setup must happen first
