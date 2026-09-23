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

from app.database import SessionLocal
from app.models import Account, PendingImport
from app.plaid_client import describe_error, get_balance, sync_transactions
from app.services import create_pending_imports, get_projected_balance, log_import_capture
from app.token_crypto import decrypt_token

# account_id -> readable error from its most recent failed sync. Shown on
# the Accounts and Review pages, because an auto-sync that fails silently
# (e.g. Plaid needing a bank re-login) would just quietly stop importing.
# In-memory is enough: every app launch re-syncs and rebuilds it.
LAST_SYNC_ERRORS: dict[int, str] = {}


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


def sync_account_recording_errors(db: Session, account: Account) -> str | None:
    """sync_plaid_account, but a failure is recorded (and returned)
    instead of raised -- one bank's problem shouldn't stop the others."""
    try:
        sync_plaid_account(db, account)
    except Exception as e:  # SDK, network, or a token that won't decrypt
        db.rollback()
        LAST_SYNC_ERRORS[account.id] = describe_error(e)
        return LAST_SYNC_ERRORS[account.id]
    LAST_SYNC_ERRORS.pop(account.id, None)
    return None


def sync_all_linked_accounts() -> None:
    """Run at app startup, in a background thread (so the page opens
    immediately rather than waiting on Plaid). Uses its own session --
    request sessions belong to request threads."""
    db = SessionLocal()
    try:
        accounts = db.scalars(
            select(Account).where(Account.plaid_access_token.isnot(None))
        ).all()
        for account in accounts:
            error = sync_account_recording_errors(db, account)
            print(f"[plaid] {account.name}: {'FAILED -- ' + error if error else 'synced'}", flush=True)
    finally:
        db.close()
