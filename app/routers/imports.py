from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.import_parser import parse_statement_text
from app.models import Account, PendingImport
from app.plaid_sync import LAST_SYNC_ERRORS
from app.services import (
    clear_pending_imports,
    create_pending_imports,
    discard_pending_import,
    get_last_import_capture,
    get_pending_imports,
    log_import_capture,
)
from app.templating import templates

router = APIRouter()


def _relative_time(dt: datetime) -> str:
    seconds = (datetime.utcnow() - dt).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(hours // 24)
    return f"{days} day{'s' if days != 1 else ''} ago"


@router.get("/import")
def import_form(request: Request, db: Session = Depends(get_db)):
    accounts = db.scalars(select(Account).order_by(Account.name)).all()
    return templates.TemplateResponse(request, "import_new.html", {"accounts": accounts})


@router.post("/import")
def parse_import(
    request: Request,
    account_id: int = Form(...),
    statement_text: str = Form(...),
    db: Session = Depends(get_db),
):
    accounts = db.scalars(select(Account).order_by(Account.name)).all()

    if not statement_text.strip():
        return templates.TemplateResponse(
            request,
            "import_new.html",
            {"accounts": accounts, "error": "Paste some statement text first."},
        )

    try:
        parsed = parse_statement_text(statement_text)
    except Exception as e:
        # Gemini/network/parsing failures are real and expected (rate
        # limit, bad key, a response that isn't valid JSON) -- surface
        # the error instead of a blank 500, since there's nothing the
        # user can do about a crash but they can retry after seeing why
        # it failed.
        return templates.TemplateResponse(
            request,
            "import_new.html",
            {"accounts": accounts, "error": f"Couldn't parse that: {e}"},
        )

    created = create_pending_imports(db, account_id, parsed["transactions"])
    log_import_capture(db, account_id, len(created))
    return RedirectResponse(url="/import/review", status_code=303)


@router.get("/import/review")
def review_imports(request: Request, db: Session = Depends(get_db)):
    pending = get_pending_imports(db)
    last_capture = get_last_import_capture(db)
    # Auto-sync runs in the background at launch; if any bank failed, say
    # so here too -- otherwise a stale queue just looks like a quiet week.
    sync_errors = [
        # list() snapshots it -- the startup sync thread may be writing to
        # it while this request reads.
        (db.get(Account, account_id), error)
        for account_id, error in list(LAST_SYNC_ERRORS.items())
    ]
    # Includes discarded rows too -- "Clear backlog" wipes both, so it
    # should show up even when the visible queue is empty but discard
    # history is still piled up (silently causing possible_duplicate
    # noise on future captures).
    total_backlog = db.query(PendingImport).count()
    return templates.TemplateResponse(
        request,
        "import_review.html",
        {
            "pending": pending,
            "last_capture": last_capture,
            "last_capture_relative_time": (
                _relative_time(last_capture.created_at) if last_capture else None
            ),
            "total_backlog": total_backlog,
            "sync_errors": [(a, e) for a, e in sync_errors if a is not None],
        },
    )


@router.post("/import/{pending_id}/discard")
def discard_import(pending_id: int, db: Session = Depends(get_db)):
    discard_pending_import(db, pending_id)
    return RedirectResponse(url="/import/review", status_code=303)


@router.post("/import/clear")
def clear_import_backlog(db: Session = Depends(get_db)):
    """Wipes the whole PendingImport table (active and discarded), not
    just the visible queue -- also clears the discard history that
    drives the possible_duplicate flag, so a queue/history that's become
    more noise than signal (e.g. after a run of accidental re-captures)
    can be reset without deleting any real Transaction."""
    clear_pending_imports(db)
    return RedirectResponse(url="/import/review", status_code=303)
