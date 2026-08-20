"""
Business logic that isn't just CRUD -- balance calculation, monthly
summaries, and the reimbursement views we designed for.

Balances are computed on the fly from the transaction log rather than
stored as a running counter on Account. For a personal-scale dataset this
is fast enough, and it avoids an entire class of bugs where a stored
balance drifts out of sync after an edit or delete.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountType,
    Category,
    CategoryKind,
    PendingImport,
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
            # A credit/refund posted directly to a card (cashback, a
            # merchant credit) reduces what you owe, same direction as an
            # incoming transfer below -- it doesn't add to it.
            balance += -t.amount if is_liability else t.amount
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


def get_month_summary(
    db: Session, year: int, month: int, account_id: int | None = None
) -> dict:
    start = date(year, month, 1)
    end = start + relativedelta(months=1)

    query = select(Transaction).where(Transaction.date >= start, Transaction.date < end)
    if account_id is not None:
        # Matches either side so a transfer shows up whichever account
        # you're looking at -- e.g. paying down a card shows as an
        # outflow on checking and an inflow on the card.
        query = query.where(
            or_(Transaction.account_id == account_id, Transaction.to_account_id == account_id)
        )
    txns = db.scalars(query).all()

    income = sum((t.amount for t in txns if t.type == TransactionType.INCOME), Decimal(0))
    expenses = sum((t.amount for t in txns if t.type == TransactionType.EXPENSE), Decimal(0))
    # Same as `expenses` but skips anything flagged exclude_from_living
    # (tuition, a deposit, ...) -- shown alongside the real total, not
    # instead of it, so a big one-off doesn't drown out day-to-day spend
    # without also hiding that it happened.
    living_expenses = sum(
        (
            t.amount
            for t in txns
            if t.type == TransactionType.EXPENSE and not t.exclude_from_living
        ),
        Decimal(0),
    )

    by_category: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for t in txns:
        if t.type == TransactionType.EXPENSE and t.category:
            by_category[t.category.name] += t.amount

    return {
        "start": start,
        "income": income,
        "expenses": expenses,
        "living_expenses": living_expenses,
        "net": income - expenses,
        "living_net": income - living_expenses,
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "transactions": sorted(txns, key=lambda t: t.date, reverse=True),
    }


def get_monthly_single_category_trend(
    db: Session, category_id: int, start: date, months: int
) -> list[dict]:
    """Monthly expense totals for a single category, isolated -- no
    comparison against other spending. For drilling into one category's
    trend on its own (see get_monthly_all_categories_trend for the
    everything-broken-out view instead)."""
    end = start + relativedelta(months=months)
    txns = db.scalars(
        select(Transaction).where(
            Transaction.date >= start,
            Transaction.date < end,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.category_id == category_id,
        )
    ).all()

    buckets = {}
    for i in range(months):
        m = start + relativedelta(months=i)
        buckets[(m.year, m.month)] = Decimal(0)

    for t in txns:
        key = (t.date.year, t.date.month)
        if key in buckets:
            buckets[key] += t.amount

    result = []
    for i in range(months):
        m = start + relativedelta(months=i)
        result.append({"month_label": m.strftime("%b %Y"), "amount": buckets[(m.year, m.month)]})
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


def get_monthly_all_categories_trend(
    db: Session, start: date, months: int
) -> tuple[list[dict], list[dict]]:
    """Per-month expense totals broken out by category (see
    get_category_color_series), for the "All categories" trend view --
    every dollar is attributed to its actual category's color instead of
    being lumped into one undifferentiated total. Returns (data, legend):
    legend is in a fixed category order for consistent labeling; each
    month's own segments are ordered by that month's amounts instead
    (largest at the bottom of the stack), which the legend deliberately
    does not follow -- legend order should stay put while values change."""
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

    # Only series with spend somewhere in the range get a legend entry --
    # keeps an idle category from cluttering the chart -- but which
    # slot/color each one gets is fixed above and stays fixed here; this
    # order is for the *legend* only, not how a given month's bar stacks.
    active = [i for i in range(len(series)) if any(buckets[k][i] > 0 for k in buckets)]
    legend = [{"label": series[i]["label"], "color": series[i]["color"]} for i in active]

    result = []
    for i in range(months):
        m = start + relativedelta(months=i)
        b = buckets[(m.year, m.month)]
        # Largest amount at the bottom of the stack, smallest at the top --
        # per-month, independent of the legend's fixed order, since the
        # point here is "what dominated this month," not identity order.
        month_order = sorted((idx for idx in active if b[idx] > 0), key=lambda idx: -b[idx])
        segments = [
            {
                "label": series[idx]["label"],
                "color": series[idx]["color"],
                "amount": b[idx],
                "rounded_top": idx == month_order[-1] if month_order else False,
            }
            for idx in month_order
        ]
        result.append(
            {
                "month_label": m.strftime("%b %Y"),
                "segments": segments,
                # Stable, legend-order lookup for the table view -- unlike
                # `segments` above, a table's columns can't reorder row to
                # row and still be readable.
                "by_label": {series[idx]["label"]: b[idx] for idx in active},
                "total": sum(b, Decimal(0)),
            }
        )
    return result, legend


def _trailing_average(
    db: Session,
    txn_type: TransactionType,
    months: int,
    account_id: int | None,
    living_only: bool,
) -> tuple[Decimal, int]:
    """Shared windowing logic behind get_trailing_average_expense/income:
    divides by however many months of matching history actually exist
    (capped at `months`), not always by `months` -- otherwise a fresh
    ledger with a few days of data would understate the average by 10-20x
    until a full window of history accumulates. Returns (average, months
    the average is actually based on) so the UI can label it honestly."""
    today = date.today()
    # "Trailing `months`" = this (partial) month plus the (months - 1)
    # months before it, so the window spans exactly `months` calendar-month
    # buckets -- keeps the numerator (summed months) and denominator
    # (months_covered below) counting the same thing.
    window_start = date(today.year, today.month, 1) - relativedelta(months=months - 1)

    def _scope(query):
        query = query.where(Transaction.type == txn_type)
        if account_id is not None:
            query = query.where(Transaction.account_id == account_id)
        if living_only:
            query = query.where(Transaction.exclude_from_living == False)  # noqa: E712
        return query

    earliest = db.scalar(_scope(select(func.min(Transaction.date))))
    if earliest is None:
        return Decimal(0), 0

    start = max(window_start, date(earliest.year, earliest.month, 1))

    txns = db.scalars(_scope(select(Transaction)).where(Transaction.date >= start)).all()
    total = sum((t.amount for t in txns), Decimal(0))

    months_covered = (today.year - start.year) * 12 + (today.month - start.month) + 1
    months_covered = max(1, min(months, months_covered))

    return total / months_covered, months_covered


def get_trailing_average_expense(
    db: Session,
    months: int = 12,
    account_id: int | None = None,
    living_only: bool = False,
) -> tuple[Decimal, int]:
    """Smoothed monthly spend, so a single lumpy cost (tuition, etc.)
    doesn't make one month look catastrophic and the rest artificially
    frugal -- shown alongside the raw monthly total, not instead of it.
    living_only=True excludes anything flagged exclude_from_living, for
    the "typical living expense" figure."""
    return _trailing_average(db, TransactionType.EXPENSE, months, account_id, living_only)


def get_trailing_average_income(
    db: Session, months: int = 12, account_id: int | None = None
) -> tuple[Decimal, int]:
    """Same idea as get_trailing_average_expense but for income -- no
    living_only variant since exclude_from_living only applies to
    expenses."""
    return _trailing_average(db, TransactionType.INCOME, months, account_id, living_only=False)


def get_pending_reimbursements(db: Session, account_id: int | None = None) -> list[Transaction]:
    query = select(Transaction).where(
        Transaction.reimbursable == True,  # noqa: E712
        Transaction.reimbursement_status == ReimbursementStatus.PENDING,
    )
    if account_id is not None:
        query = query.where(Transaction.account_id == account_id)
    return db.scalars(query).all()


DUPLICATE_DATE_TOLERANCE_DAYS = 1


def _looks_like_duplicate(db: Session, account_id: int, txn_date: date, amount: Decimal) -> bool:
    """A pending/posted date can shift by a day between when you logged
    something by hand and when the bank's statement settles it -- exact
    date matching would miss real duplicates, so this allows a 1-day
    window either side."""
    window_start = txn_date - timedelta(days=DUPLICATE_DATE_TOLERANCE_DAYS)
    window_end = txn_date + timedelta(days=DUPLICATE_DATE_TOLERANCE_DAYS)
    existing = db.scalar(
        select(Transaction.id).where(
            Transaction.account_id == account_id,
            Transaction.amount == amount,
            Transaction.date >= window_start,
            Transaction.date <= window_end,
        )
    )
    return existing is not None


def create_pending_imports(
    db: Session, account_id: int, parsed: list[dict]
) -> list[PendingImport]:
    """Turns parsed {date, amount, merchant, type} dicts (see
    app/import_parser.py) into PendingImport rows for the review queue,
    flagging ones that look like they might already be logged by hand.
    Tolerant of a parsed row being malformed (bad date/amount/type) --
    skips just that row rather than failing the whole batch, since one bad
    line out of dozens shouldn't block importing the rest."""
    created = []
    for row in parsed:
        try:
            txn_date = date.fromisoformat(row["date"]) if row.get("date") else date.today()
            amount = Decimal(str(row["amount"]))
            merchant = (row.get("merchant") or "").strip() or "(unknown)"
            suggested_type = TransactionType(row.get("type", "expense"))
        except (KeyError, ValueError, InvalidOperation, TypeError):
            continue

        pending = PendingImport(
            date=txn_date,
            amount=amount,
            merchant=merchant,
            suggested_type=suggested_type,
            account_id=account_id,
            possible_duplicate=_looks_like_duplicate(db, account_id, txn_date, amount),
        )
        db.add(pending)
        created.append(pending)

    db.commit()
    return created


def get_pending_imports(db: Session) -> list[PendingImport]:
    return db.scalars(
        select(PendingImport).order_by(PendingImport.date.desc())
    ).all()


def delete_pending_import(db: Session, pending_id: int) -> None:
    pending = db.get(PendingImport, pending_id)
    if pending:
        db.delete(pending)
        db.commit()


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
    "Travel",
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
