"""Shared Jinja2Templates instance for all routers.

Registers static_version() as a template global so <link>/<script> tags can
cache-bust with the file's actual mtime (see base.html) -- without it,
browsers keep serving a stale cached style.css after every edit, since
nothing in the URL ever changes to tell them the file did.

Also registers pending_import_count() so base.html's nav can show a
"Review (N)" badge on every page -- the review queue used to be an
orphan page with no persistent link to it anywhere in the nav, reachable
only right after a capture's redirect or by knowing the URL.
"""
import os

from fastapi.templating import Jinja2Templates

from app.database import SessionLocal
from app.models import PendingImport

templates = Jinja2Templates(directory="app/templates")


def static_version(filename: str) -> str:
    path = os.path.join("app/static", filename)
    return str(int(os.path.getmtime(path)))


def pending_import_count() -> int:
    db = SessionLocal()
    try:
        return db.query(PendingImport).filter(PendingImport.discarded == False).count()  # noqa: E712
    finally:
        db.close()


templates.env.globals["static_version"] = static_version
templates.env.globals["pending_import_count"] = pending_import_count
