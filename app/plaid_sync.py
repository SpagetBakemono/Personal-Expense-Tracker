"""
Posts Plaid transactions straight into the ledger -- no review queue.

What stands in for a human reviewer:
- Plaid's transaction id is stored on the row (unique), so a re-sync can
  never post the same bank transaction twice.
- Before creating anything, an entry you already logged by hand (same
  account, amount and direction, within MATCH_WINDOW_DAYS) is *adopted* --
  linked to the Plaid id instead of duplicated.
- On a newly linked account's first sync, anything dated on or before your
  last entry for it is adopt-only: that stretch is already reconciled, so
  unmatched history is skipped rather than risk double-counting.
- A card payment seen by both checking and the card becomes one transfer.
- A pending transaction is updated in place when it posts, so edits made
  to it (category, note) survive.
- After every sync the app's balance is compared with the bank's; a
  mismatch is shown on the Dashboard and Accounts page.
"""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    Account,
    AccountType,
    Category,
    ImportCapture,
    Transaction,
    TransactionType,
)
from app.plaid_client import describe_error, get_balance, sync_transactions
from app.services import get_account_balance, log_import_capture
from app.token_crypto import decrypt_token

# Matching a Plaid transaction to one you logged by hand. Banks post
# *after* you buy -- never before -- and BofA card swipes (e.g. MTA fares)
# were observed posting up to 5 days late, with no purchase date sent to
# Plaid. So look well back, barely forward.
POSTING_LAG_DAYS = 5
POSTING_LEAD_DAYS = 1
PAIR_WINDOW_DAYS = 5
CARD_PAYMENT = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
TRANSFER_PRIMARIES = {"TRANSFER_IN", "TRANSFER_OUT", "LOAN_PAYMENTS"}

# Plaid personal-finance category -> this app's seeded category names.
# Detailed codes take precedence over primary ones.
PLAID_CATEGORY_MAP = {
    "FOOD_AND_DRINK_GROCERIES": "Groceries",
    "FOOD_AND_DRINK": "Food",
    "TRANSPORTATION": "Transport",
    "TRAVEL": "Travel",
    "RENT_AND_UTILITIES": "Rent",
    "MEDICAL": "Health",
    "PERSONAL_CARE": "Health",
    "GENERAL_MERCHANDISE": "Shopping",
    "ENTERTAINMENT": "Entertainment",
    "INCOME_WAGES": "Salary",
    "INCOME_INTEREST_EARNED": "Interest",
}

# account_id -> readable error from its most recent failed sync. Shown on
# the Accounts and Review pages, because an auto-sync that fails silently
# (e.g. Plaid needing a bank re-login) would just quietly stop importing.
# In-memory is enough: every app launch re-syncs and rebuilds it.
LAST_SYNC_ERRORS: dict[int, str] = {}


def _within(days: int, when):
    return (Transaction.date >= when - timedelta(days=days)) & (
        Transaction.date <= when + timedelta(days=days)
    )


def _posted_after(when):
    """A hand-entered row this Plaid transaction could be the posting of."""
    return (Transaction.date >= when - timedelta(days=POSTING_LAG_DAYS)) & (
        Transaction.date <= when + timedelta(days=POSTING_LEAD_DAYS)
    )


def _is_transferish(t: dict) -> bool:
    return t["category_detailed"] == CARD_PAYMENT or t["category_primary"] in TRANSFER_PRIMARIES


def _pick_category(db: Session, t: dict, txn_type: TransactionType) -> int | None:
    # Your own past choice for this merchant wins over Plaid's guess.
    learned = db.scalar(
        select(Transaction.category_id)
        .where(Transaction.note == t["merchant"], Transaction.category_id.isnot(None),
               Transaction.type == txn_type)
        .order_by(Transaction.date.desc())
        .limit(1)
    )
    if learned:
        return learned
    name = PLAID_CATEGORY_MAP.get(t["category_detailed"]) or PLAID_CATEGORY_MAP.get(
        t["category_primary"]
    )
    if txn_type == TransactionType.INCOME and name not in ("Salary", "Interest"):
        name = "Other Income"
    elif txn_type == TransactionType.EXPENSE and name is None:
        name = "Other"
    return db.scalar(select(Category.id).where(Category.name == name)) if name else None


def _find_by_plaid_id(db: Session, plaid_id: str) -> Transaction | None:
    return db.scalar(
        select(Transaction).where(
            or_(
                Transaction.plaid_transaction_id == plaid_id,
                Transaction.plaid_pair_transaction_id == plaid_id,
            )
        )
    )


def _adopt(db: Session, account: Account, t: dict) -> bool:
    """Links t to a hand-entered row it duplicates, if there is one."""
    if t["outflow"]:
        candidates = select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.type.in_([TransactionType.EXPENSE, TransactionType.TRANSFER]),
            Transaction.plaid_transaction_id.is_(None),
        )
    else:
        candidates = select(Transaction).where(
            or_(
                (Transaction.account_id == account.id)
                & (Transaction.type == TransactionType.INCOME)
                & Transaction.plaid_transaction_id.is_(None),
                # The receiving side of a transfer you logged from another
                # account (e.g. a card payment logged on checking).
                (Transaction.to_account_id == account.id)
                & (Transaction.type == TransactionType.TRANSFER)
                & Transaction.plaid_pair_transaction_id.is_(None),
            )
        )
    rows = db.scalars(
        candidates.where(Transaction.amount == t["amount"], _posted_after(t["date"]))
    ).all()
    if not rows:
        return False
    # Oldest candidate, not nearest: banks post in FIFO order, and MTA
    # batches several days of fares into one posting date. Nearest-date
    # lets a batch grab the newest rides and strands the oldest ones
    # outside the next batch's window, so they get posted twice. With
    # oldest-first processing (see sync_plaid_account), oldest-first
    # claiming pairs identical amounts off in order.
    row = min(rows, key=lambda r: (r.date, r.id))
    if row.to_account_id == account.id and row.account_id != account.id:
        row.plaid_pair_transaction_id = t["plaid_id"]
    else:
        row.plaid_transaction_id = t["plaid_id"]
    row.pending = t["pending"]
    return True


def _pair_transfer(db: Session, account: Account, t: dict) -> bool:
    """Merges t with the other side of the same transfer, if that side has
    already been posted from another linked account."""
    if t["outflow"]:
        # The card saw the payment first and it was posted as income.
        row = db.scalar(
            select(Transaction).where(
                Transaction.account_id != account.id,
                Transaction.type == TransactionType.INCOME,
                Transaction.plaid_transaction_id.isnot(None),
                Transaction.plaid_pair_transaction_id.is_(None),
                Transaction.amount == t["amount"],
                _within(PAIR_WINDOW_DAYS, t["date"]),
            )
        )
        if row is None:
            return False
        row.plaid_pair_transaction_id = row.plaid_transaction_id
        row.plaid_transaction_id = t["plaid_id"]
        row.to_account_id, row.account_id = row.account_id, account.id
        row.type, row.category_id = TransactionType.TRANSFER, None
        return True
    # This is the receiving side; the paying side is a transfer still
    # waiting for its destination.
    row = db.scalar(
        select(Transaction).where(
            Transaction.account_id != account.id,
            Transaction.type == TransactionType.TRANSFER,
            Transaction.to_account_id.is_(None),
            Transaction.plaid_transaction_id.isnot(None),
            Transaction.amount == t["amount"],
            _within(PAIR_WINDOW_DAYS, t["date"]),
        )
    )
    if row is None:
        return False
    row.to_account_id = account.id
    row.plaid_pair_transaction_id = t["plaid_id"]
    return True


def _apply_added(db: Session, account: Account, t: dict, adopt_only: bool) -> bool:
    """Returns True if a new ledger row was created."""
    if _find_by_plaid_id(db, t["plaid_id"]):
        return False  # already posted -- re-syncs are harmless

    if t["pending_plaid_id"]:
        row = _find_by_plaid_id(db, t["pending_plaid_id"])
        if row:  # a pending charge just posted: update it in place
            if row.plaid_transaction_id == t["pending_plaid_id"]:
                row.plaid_transaction_id = t["plaid_id"]
            else:
                row.plaid_pair_transaction_id = t["plaid_id"]
            row.amount, row.date, row.pending = t["amount"], t["date"], t["pending"]
            return False

    if _adopt(db, account, t) or adopt_only:
        return False
    if _is_transferish(t) and _pair_transfer(db, account, t):
        return False

    is_card = account.type == AccountType.CREDIT_CARD
    if not t["outflow"]:
        txn_type = TransactionType.INCOME
    elif t["category_detailed"] == CARD_PAYMENT and not is_card:
        # Paying a card: a transfer, not spending. The destination gets
        # filled in when the card's side of the payment syncs.
        txn_type = TransactionType.TRANSFER
    else:
        txn_type = TransactionType.EXPENSE

    db.add(
        Transaction(
            date=t["date"],
            amount=t["amount"],
            type=txn_type,
            account_id=account.id,
            category_id=None if txn_type == TransactionType.TRANSFER else _pick_category(db, t, txn_type),
            note=t["merchant"],
            plaid_transaction_id=t["plaid_id"],
            pending=t["pending"],
        )
    )
    return True


def _apply_modified(db: Session, t: dict) -> None:
    row = db.scalar(select(Transaction).where(Transaction.plaid_transaction_id == t["plaid_id"]))
    if row:
        row.amount, row.date, row.pending = t["amount"], t["date"], t["pending"]


def _posted_balance(db: Session, account: Account) -> Decimal:
    """The app's balance counting only posted transactions -- comparable
    to the bank's posted ("current") balance, which excludes pending."""
    balance = get_account_balance(db, account)
    is_liability = account.type == AccountType.CREDIT_CARD
    pending = db.scalars(
        select(Transaction).where(
            Transaction.pending == True,  # noqa: E712
            or_(Transaction.account_id == account.id, Transaction.to_account_id == account.id),
            Transaction.date >= account.opening_balance_date,
        )
    ).all()
    for t in pending:
        # Undo exactly what get_account_balance applied for this row.
        if t.to_account_id == account.id:
            effect = -t.amount if is_liability else t.amount
        elif t.type == TransactionType.INCOME:
            effect = -t.amount if is_liability else t.amount
        else:  # EXPENSE, or TRANSFER out of this account
            effect = t.amount if is_liability else -t.amount
        balance -= effect
    return balance


def _apply_removed(db: Session, plaid_id: str) -> None:
    row = _find_by_plaid_id(db, plaid_id)
    if row is None:
        return
    if row.plaid_pair_transaction_id is None:
        db.delete(row)
    elif row.plaid_transaction_id == plaid_id:
        # Paying side vanished; keep the receiving side as a credit.
        row.plaid_transaction_id, row.plaid_pair_transaction_id = row.plaid_pair_transaction_id, None
        row.account_id, row.to_account_id = row.to_account_id, None
        row.type = TransactionType.INCOME
    else:
        # Receiving side vanished; the paying side waits for a destination.
        row.plaid_pair_transaction_id, row.to_account_id = None, None


def sync_plaid_account(db: Session, account: Account) -> int:
    """Returns how many new ledger rows were created. Everything -- the
    changes and the advanced cursor -- lands in one commit, so a failure
    part-way leaves nothing half-applied and the next sync retries it.
    Raises on any Plaid/network/decryption failure."""
    token = decrypt_token(account.plaid_access_token)
    first_sync = account.plaid_cursor is None
    result = sync_transactions(token, account.plaid_account_id, account.plaid_cursor)

    backfill_until = None
    if first_sync:
        backfill_until = db.scalar(
            select(func.max(Transaction.date)).where(
                or_(Transaction.account_id == account.id, Transaction.to_account_id == account.id)
            )
        )

    created = 0
    # Posted before pending, then oldest first: anything already posted
    # happened before anything still pending, so it gets first claim on
    # hand-entered rows. Otherwise a pending $3 fare can grab the row an
    # older posted $3 fare belongs to, and that fare gets doubled.
    for t in sorted(result["added"], key=lambda t: (t["pending"], t["date"])):
        # Already baked into the opening-balance snapshot.
        if t["date"] < account.opening_balance_date:
            continue
        adopt_only = backfill_until is not None and t["date"] <= backfill_until
        created += _apply_added(db, account, t, adopt_only)
        db.flush()  # later matches in this batch must see earlier ones
    for t in result["modified"]:
        _apply_modified(db, t)
    for plaid_id in result["removed"]:
        _apply_removed(db, plaid_id)
        db.flush()

    account.plaid_cursor = result["next_cursor"]
    db.commit()

    bank_balance = get_balance(token, account.plaid_account_id)
    app_balance = _posted_balance(db, account)
    matches = None if bank_balance is None else abs(app_balance - bank_balance) < Decimal("0.01")
    log_import_capture(db, account.id, created, bank_balance, app_balance, matches)
    return created


def get_sync_alerts(db: Session) -> list[str]:
    """Everything about bank sync that needs your attention, one line
    each: failed syncs, and linked accounts whose balance disagrees with
    the bank's after their latest sync. With no review queue, the balance
    check is what catches a missed or doubled transaction."""
    alerts = []
    # list() snapshots it -- the startup sync thread may be writing to it.
    for account_id, error in list(LAST_SYNC_ERRORS.items()):
        account = db.get(Account, account_id)
        if account:
            alerts.append(f"{account.name}: sync failed -- {error}")
    for account in db.scalars(select(Account).where(Account.plaid_access_token.isnot(None))):
        if account.id in LAST_SYNC_ERRORS:
            continue
        last = db.scalar(
            select(ImportCapture)
            .where(ImportCapture.account_id == account.id)
            .order_by(ImportCapture.id.desc())
            .limit(1)
        )
        if last and last.balance_matches is False:
            alerts.append(
                f"{account.name}: bank's posted balance is ${last.bank_balance:,.2f} but the "
                f"app's is ${last.app_balance:,.2f} -- a transaction may be missing or doubled."
            )
    return alerts


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
