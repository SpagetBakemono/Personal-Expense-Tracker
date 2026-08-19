"""
Business logic that isn't just CRUD -- balance calculation, monthly
summaries, and the reimbursement views we designed for.

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
    CategoryKind,
    ReimbursementStatus,
    Transaction,
    TransactionType,
)

# Dataviz-skill validated 8-hue categorical palette (light mode), adjacent-pair
# order -- see app/static/style.css's palette note. Used only for the
# multi-category trend chart; the single-category highlight view keeps its
# existing accent/gray 2-color scheme.
CATEGORY_COLOR_SLOTS = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red -- reserved for the "Other" fold-bucket, below
]


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


def get_total_balance(balances: list[tuple[Account, Decimal]]) -> Decimal:
    """Net total across accounts. Credit card balances are what you owe
    (a liability), so they subtract rather than add -- otherwise carrying
    card debt would make your total look bigger, not smaller."""
    total = Decimal(0)
    for account, balance in balances:
        total += -balance if account.type == AccountType.CREDIT_CARD else balance
    return total


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


def get_monthly_category_trend(
    db: Session, category_id: int | None, start: date, months: int
) -> list[dict]:
    """Monthly expense totals for `months` consecutive months starting at
    `start` (the 1st of a month), split into `highlighted` (the given
    category) and `other` (everything else) -- so a lumpy, infrequent cost
    like tuition can be visually called out against regular spending
    instead of just inflating the month's bar."""
    end = start + relativedelta(months=months)
    txns = db.scalars(
        select(Transaction).where(
            Transaction.date >= start,
            Transaction.date < end,
            Transaction.type == TransactionType.EXPENSE,
        )
    ).all()

    buckets = {}
    for i in range(months):
        m = start + relativedelta(months=i)
        buckets[(m.year, m.month)] = {"highlighted": Decimal(0), "other": Decimal(0)}

    for t in txns:
        key = (t.date.year, t.date.month)
        if key not in buckets:
            continue
        if category_id is not None and t.category_id == category_id:
            buckets[key]["highlighted"] += t.amount
        else:
            buckets[key]["other"] += t.amount

    result = []
    for i in range(months):
        m = start + relativedelta(months=i)
        b = buckets[(m.year, m.month)]
        result.append(
            {
                "month_label": m.strftime("%b %Y"),
                "highlighted": b["highlighted"],
                "other": b["other"],
                "total": b["highlighted"] + b["other"],
            }
        )
    return result


def get_category_color_series(db: Session) -> list[dict]:
    """Fixed {label, color, category_ids} assignment for the multi-category
    trend chart: the first 7 expense categories (by id, i.e. creation order)
    get a dedicated hue from the validated 8-hue categorical palette; every
    other expense category folds into a shared "Other" bucket on the 8th
    hue. Fixed by category id, never by how much each spent, so a
    category's color never changes when the visible time range does."""
    categories = db.scalars(
        select(Category).where(Category.kind == CategoryKind.EXPENSE).order_by(Category.id)
    ).all()

    series = [
        {"label": c.name, "color": CATEGORY_COLOR_SLOTS[i], "category_ids": {c.id}}
        for i, c in enumerate(categories[:7])
    ]
    if len(categories) > 7:
        series.append(
            {
                "label": "Other",
                "color": CATEGORY_COLOR_SLOTS[7],
                "category_ids": {c.id for c in categories[7:]},
            }
        )
    return series


def get_monthly_all_categories_trend(db: Session, start: date, months: int) -> list[dict]:
    """Per-month expense totals broken out by category (see
    get_category_color_series), for the "All categories" trend view --
    every dollar is attributed to its actual category's color instead of
    being lumped into one undifferentiated total."""
    end = start + relativedelta(months=months)
    series = get_category_color_series(db)
    series_index_by_category_id = {
        cid: idx for idx, s in enumerate(series) for cid in s["category_ids"]
    }

    txns = db.scalars(
        select(Transaction).where(
            Transaction.date >= start,
            Transaction.date < end,
            Transaction.type == TransactionType.EXPENSE,
        )
    ).all()

    buckets = {}
    for i in range(months):
        m = start + relativedelta(months=i)
        buckets[(m.year, m.month)] = [Decimal(0)] * len(series)

    for t in txns:
        key = (t.date.year, t.date.month)
        if key not in buckets:
            continue
        idx = series_index_by_category_id.get(t.category_id)
        if idx is None:
            continue
        buckets[key][idx] += t.amount

    # Only series with spend somewhere in the range get a segment/legend
    # entry -- keeps an idle category from cluttering the chart -- but
    # which slot/color each one gets is fixed above, not recomputed here.
    active = [i for i in range(len(series)) if any(buckets[k][i] > 0 for k in buckets)]

    result = []
    for i in range(months):
        m = start + relativedelta(months=i)
        b = buckets[(m.year, m.month)]
        top = next((idx for idx in reversed(active) if b[idx] > 0), None)
        segments = [
            {
                "label": series[idx]["label"],
                "color": series[idx]["color"],
                "amount": b[idx],
                "rounded_top": idx == top,
            }
            for idx in active
        ]
        result.append(
            {
                "month_label": m.strftime("%b %Y"),
                "segments": segments,
                "total": sum(b, Decimal(0)),
            }
        )
    return result


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
