from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.import_parser import parse_statement_text
from app.models import Account
from app.services import (
    create_pending_imports,
    discard_pending_import,
    get_pending_imports,
    get_projected_balance,
)
from app.templating import templates

router = APIRouter()


@router.get("/api/accounts")
def list_accounts_json(db: Session = Depends(get_db)):
    """Plain JSON, for the browser extension's popup to populate an
    account picker -- everything else in this app serves HTML."""
    accounts = db.scalars(select(Account).order_by(Account.name)).all()
    return [{"id": a.id, "name": a.name} for a in accounts]


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

    create_pending_imports(db, account_id, parsed["transactions"])
    return RedirectResponse(url="/import/review", status_code=303)


@router.post("/import/capture")
def parse_import_capture(
    account_id: int = Form(...),
    statement_text: str = Form(...),
    db: Session = Depends(get_db),
):
    """Used by the browser extension: the statement page's own rendered
    text (grabbed by a content script), not a screenshot -- reads more
    reliably than an image and reuses the same Gemini text path as the
    manual-paste flow. Returns JSON (the extension's popup reads this
    directly), not a redirect -- there's no page to redirect within a
    popup.

    Also cross-validates: if the page had a stated current balance,
    compare it against the account's confirmed balance plus this batch's
    non-duplicate candidates. A mismatch means something in the batch
    was missed or misread -- worth flagging before the user blindly
    confirms everything."""
    if not statement_text.strip():
        return JSONResponse({"error": "No page text received."}, status_code=400)

    try:
        parsed = parse_statement_text(statement_text)
    except Exception as e:
        return JSONResponse({"error": f"Couldn't parse that: {e}"}, status_code=502)

    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"error": "That account no longer exists."}, status_code=400)

    created = create_pending_imports(db, account_id, parsed["transactions"])

    result = {"count": len(created)}
    bank_balance = parsed.get("account_balance")
    if bank_balance is not None:
        try:
            bank_balance = Decimal(str(bank_balance))
        except InvalidOperation:
            bank_balance = None

    if bank_balance is not None:
        app_balance = get_projected_balance(db, account, created)
        difference = app_balance - bank_balance
        result.update(
            {
                "bank_balance": float(bank_balance),
                "app_balance": float(app_balance),
                # A cent or two of rounding slop shouldn't read as a
                # mismatch -- statements themselves sometimes round.
                "balance_matches": abs(difference) < Decimal("0.01"),
                "difference": float(difference),
            }
        )

    return result


@router.get("/import/review")
def review_imports(request: Request, db: Session = Depends(get_db)):
    pending = get_pending_imports(db)
    return templates.TemplateResponse(request, "import_review.html", {"pending": pending})


@router.post("/import/{pending_id}/discard")
def discard_import(pending_id: int, db: Session = Depends(get_db)):
    discard_pending_import(db, pending_id)
    return RedirectResponse(url="/import/review", status_code=303)
