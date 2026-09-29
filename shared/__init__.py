"""Shared library for TenantForge microservices.

This package holds the small amount of code that every service legitimately
shares: settings loading, the async SQLAlchemy engine factory, JWT / password
security helpers, the event-bus abstraction and common FastAPI dependencies.

Each service still owns its own database schema (its own declarative ``Base``)
and its own domain logic; ``shared`` only provides infrastructure primitives.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
