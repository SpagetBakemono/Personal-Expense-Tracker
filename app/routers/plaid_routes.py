from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account
from app.plaid_client import (
    create_link_token,
    describe_error,
    exchange_public_token,
    get_accounts,
)
from app.plaid_sync import sync_plaid_account
from app.token_crypto import encrypt_token

router = APIRouter()


def _plaid_error(e: Exception) -> JSONResponse:
    # Surface the real reason instead of a bare 500, so a bad key or a
    # sandbox token used against production is diagnosable from the UI.
    return JSONResponse({"error": f"Plaid error: {describe_error(e)}"}, status_code=502)


@router.post("/plaid/create-link-token")
def plaid_create_link_token():
    try:
        return {"link_token": create_link_token()}
    except Exception as e:  # SDK, network (TLS/DNS/timeout), or missing config
        return _plaid_error(e)


class ExchangeRequest(BaseModel):
    public_token: str
    account_id: int
    # From Link's onSuccess metadata -- which account the user picked
    # inside Link. Optional because some institutions don't surface a
    # selection step, in which case the Item's only account is used.
    plaid_account_id: str | None = None


@router.post("/plaid/exchange")
def plaid_exchange(body: ExchangeRequest, db: Session = Depends(get_db)):
    account = db.get(Account, body.account_id)
    if account is None:
        return JSONResponse({"error": "That account no longer exists."}, status_code=400)

    try:
        access_token = exchange_public_token(body.public_token)
        plaid_accounts = get_accounts(access_token)
    except Exception as e:  # SDK, network (TLS/DNS/timeout), or missing config
        return _plaid_error(e)

    if body.plaid_account_id:
        match = next(
            (a for a in plaid_accounts if a["plaid_account_id"] == body.plaid_account_id), None
        )
    elif len(plaid_accounts) == 1:
        match = plaid_accounts[0]
    else:
        match = None

    if match is None:
        return JSONResponse(
            {"error": "Couldn't tell which account to link -- pick exactly one account in Plaid."},
            status_code=400,
        )

    try:
        account.plaid_access_token = encrypt_token(access_token)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    account.plaid_account_id = match["plaid_account_id"]
    # Fresh link, fresh history -- the first sync starts from the
    # beginning of what Plaid has for this account.
    account.plaid_cursor = None
    db.commit()
    return {"ok": True, "linked": match["name"]}


@router.post("/accounts/{account_id}/plaid/sync")
def plaid_sync(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if account is None or not account.plaid_access_token:
        return RedirectResponse(url="/accounts", status_code=303)
    try:
        sync_plaid_account(db, account)
    except Exception as e:  # SDK, network, or a token that won't decrypt
        return RedirectResponse(
            url=f"/accounts?sync_error={quote(f'{account.name}: {describe_error(e)}')}",
            status_code=303,
        )
    # The review page's "Last capture" banner shows what this sync found
    # and whether the balance matched.
    return RedirectResponse(url="/import/review", status_code=303)


@router.post("/accounts/{account_id}/plaid/disconnect")
def plaid_disconnect(account_id: int, db: Session = Depends(get_db)):
    """Only forgets the link locally -- sandbox tokens stop working the
    moment PLAID_ENV flips to production, so clearing them is how you
    relink the same account against real data."""
    account = db.get(Account, account_id)
    if account:
        account.plaid_access_token = None
        account.plaid_account_id = None
        account.plaid_cursor = None
        db.commit()
    return RedirectResponse(url="/accounts", status_code=303)
