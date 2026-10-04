"""Read-only HTTP API over the MFDataIndia PostgreSQL store."""

from mfdataindia.api.app import create_app

__all__ = ["create_app"]
