"""FastAPI web API on top of pyquotex.

Single-tenant: one shared :class:`Quotex` client lives for the lifetime
of the FastAPI app and serves every request. Authentication is a single
bearer token configured via ``PYQUOTEX_API_KEY``.

Run locally::

    pip install 'pyquotex[webapi]'
    export PYQUOTEX_EMAIL=… PYQUOTEX_PASSWORD=… PYQUOTEX_API_KEY=…
    python -m pyquotex.webapi

Or via Docker::

    docker compose up
"""
from .app import create_app

__all__ = ["create_app"]
