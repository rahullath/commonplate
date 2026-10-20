"""Vercel entrypoint. Vercel would otherwise load app/main.py as a top-level
module, which breaks its relative imports; importing the package keeps them."""
from app.main import app  # noqa: F401
