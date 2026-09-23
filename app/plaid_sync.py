"""
Pulls new transactions for one Plaid-linked account into the review queue.

Glue between app/plaid_client.py (Plaid calls, no DB) and the existing
import pipeline in app/services.py -- Plaid is just another source feeding
create_pending_imports, same as a pasted statement, so duplicate flagging,
the balance cross-check and Confirm/Discard all work unchanged.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, PendingImport
from app.plaid_client import get_balance, sync_transactions
from app.services import create_pending_imports, get_projected_balance, log_import_capture
from app.token_crypto import decrypt_token


def sync_plaid_account(db: Session, account: Account) -> int:
    """Returns how many new candidates landed in the review queue.
    Raises on any Plaid/network/decryption failure -- callers decide
    whether that's fatal (the Sync button) or just logged (startup)."""
    token = decrypt_token(account.plaid_access_token)
    result = sync_transactions(token, account.plaid_account_id, account.plaid_cursor)

    # A fresh link returns the account's whole history. Anything before
    # the opening-balance snapshot is already baked into that number, so
    # it'd only ever be noise in the queue (and has zero balance effect if
    # confirmed -- see get_account_balance).
    fresh = [
        t for t in result["transactions"]
        if date.fromisoformat(t["date"]) >= account.opening_balance_date
    ]
    created = create_pending_imports(db, account.id, fresh)

    # Advance the bookmark only after the candidates are safely queued --
    # if anything below fails, the worst case is the next sync flags these
    # same transactions as possible duplicates, never that they're skipped.
    account.plaid_cursor = result["next_cursor"]
    db.commit()

    bank_balance = get_balance(token, account.plaid_account_id)
    app_balance = balance_matches = None
    if bank_balance is not None:
        # Project against *everything* still waiting in the queue, not
        # just this batch -- otherwise a sync that finds nothing new
        # ignores items queued by earlier syncs and reports a false
        # mismatch on nearly every app launch.
        queued = db.scalars(
            select(PendingImport).where(
                PendingImport.account_id == account.id,
                PendingImport.discarded == False,  # noqa: E712
            )
        ).all()
        app_balance = get_projected_balance(db, account, queued)
        balance_matches = abs(app_balance - bank_balance) < Decimal("0.01")
    log_import_capture(db, account.id, len(created), bank_balance, app_balance, balance_matches)
    return len(created)
