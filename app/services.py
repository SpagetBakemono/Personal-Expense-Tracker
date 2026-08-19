"""
Business logic that isn't just CRUD -- balance calculation, monthly
summaries, and the reimbursement/cadence views we designed for.

Balances are computed on the fly from the transaction log rather than
stored as a running counter on Account. For a personal-scale dataset this
is fast enough, and it avoids an entire class of bugs where a stored
balance drifts out of sync after an edit or delete.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountType,
    Category,
    Cadence,
    ReimbursementStatus,
    Transaction,
    TransactionType,
)


def get_account_balance(db: Session, account: Account) -> Decimal:
    balance = account.opening_balance

    outgoing = db.scalars(
        select(Transaction).where(Transaction.account_id == account.id)
    ).all()
    incoming = db.scalars(
        select(Transaction).where(Transaction.to_account_id == account.id)
    ).all()

    is_liability = account.type == AccountType.CREDIT_CARD

    for t in outgoing:
        if t.type == TransactionType.INCOME:
            balance += t.amount
        elif t.type == TransactionType.EXPENSE:
            # On a credit card, spending increases what you owe.
            # On cash/checking, spending decreases what you have.
            balance += t.amount if is_liability else -t.amount
        elif t.type == TransactionType.TRANSFER:
            # Money leaving this account.
            balance += t.amount if is_liability else -t.amount

    for t in incoming:
        # Only TRANSFER transactions have a to_account.
        # Paying down a credit card reduces what's owed; landing in an
        # asset account increases what you have.
        balance += -t.amount if is_liability else t.amount

    return balance


def get_all_balances(db: Session) -> list[tuple[Account, Decimal]]:
    accounts = db.scalars(select(Account).order_by(Account.id)).all()
    return [(a, get_account_balance(db, a)) for a in accounts]


def get_month_summary(db: Session, year: int, month: int) -> dict:
    start = date(year, month, 1)
    end = start + relativedelta(months=1)

    txns = db.scalars(
        select(Transaction).where(Transaction.date >= start, Transaction.date < end)
    ).all()

    income = sum((t.amount for t in txns if t.type == TransactionType.INCOME), Decimal(0))
    expenses = sum((t.amount for t in txns if t.type == TransactionType.EXPENSE), Decimal(0))

    by_category: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for t in txns:
        if t.type == TransactionType.EXPENSE and t.category:
            by_category[t.category.name] += t.amount

    return {
        "start": start,
        "income": income,
        "expenses": expenses,
        "net": income - expenses,
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "transactions": sorted(txns, key=lambda t: t.date, reverse=True),
    }


def get_trailing_average_expense(db: Session, months: int = 12) -> tuple[Decimal, int]:
    """Smoothed monthly spend, so a single lumpy cost (tuition, etc.)
    doesn't make one month look catastrophic and the rest artificially
    frugal -- shown alongside the raw monthly total, not instead of it.

    Divides by however many months of expense history actually exist
    (capped at `months`), not always by `months` -- otherwise a fresh
    ledger with a few days of data would understate the average by 10-20x
    until a full window of history accumulates. Returns (average, months
    the average is actually based on) so the UI can label it honestly.
    """
    today = date.today()
    # "Trailing `months`" = this (partial) month plus the (months - 1)
    # months before it, so the window spans exactly `months` calendar-month
    # buckets -- keeps the numerator (summed months) and denominator
    # (months_covered below) counting the same thing.
    window_start = date(today.year, today.month, 1) - relativedelta(months=months - 1)

    earliest = db.scalar(
        select(func.min(Transaction.date)).where(Transaction.type == TransactionType.EXPENSE)
    )
    if earliest is None:
        return Decimal(0), 0

    start = max(window_start, date(earliest.year, earliest.month, 1))

    txns = db.scalars(
        select(Transaction).where(
            Transaction.date >= start,
            Transaction.type == TransactionType.EXPENSE,
        )
    ).all()
    total = sum((t.amount for t in txns), Decimal(0))

    months_covered = (today.year - start.year) * 12 + (today.month - start.month) + 1
    months_covered = max(1, min(months, months_covered))

    return total / months_covered, months_covered


def get_pending_reimbursements(db: Session) -> list[Transaction]:
    return db.scalars(
        select(Transaction).where(
            Transaction.reimbursable == True,  # noqa: E712
            Transaction.reimbursement_status == ReimbursementStatus.PENDING,
        )
    ).all()


DEFAULT_EXPENSE_CATEGORIES = [
    "Food",
    "Groceries",
    "Rent",
    "Transport",
    "Subscriptions",
    "Education",
    "Health",
    "Shopping",
    "Entertainment",
    "Other",
]
DEFAULT_INCOME_CATEGORIES = [
    "Salary",
    "Freelance",
    "Reimbursement",
    "Interest",
    "Gift",
    "Other Income",
]


def seed_default_categories(db: Session) -> None:
    existing = {c.name for c in db.scalars(select(Category)).all()}
    for name in DEFAULT_EXPENSE_CATEGORIES:
        if name not in existing:
            db.add(Category(name=name, kind="expense"))
    for name in DEFAULT_INCOME_CATEGORIES:
        if name not in existing:
            db.add(Category(name=name, kind="income"))
    db.commit()
