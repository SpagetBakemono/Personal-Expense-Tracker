"""
Data model.

This encodes the design decisions from planning:

- Credit cards are modeled as accounts with type CREDIT_CARD. A purchase on
  a card is an EXPENSE transaction against that account (increases what you
  owe). Paying the card bill is a TRANSFER from a checking/cash account into
  the card account (decreases what you owe). This is what avoids double
  counting and solves the "card charges settle after the month" problem --
  see get_account_balance() in services.py for how balances are derived.

- Lumpy, foreseeable costs (like semester tuition) aren't a special case --
  they're logged as a normal expense on the date paid. The 12-month
  trailing average in get_trailing_average_expense() is what keeps a
  single lumpy cost from making one month look catastrophic; the raw
  monthly total is deliberately left alone since it's accurate.

- Reimbursements are tracked with a lightweight flag + status on the
  original expense, rather than a full receivables ledger. When money
  actually arrives, it's logged as its own INCOME transaction.

- Statement-import candidates (PendingImport) are a completely separate
  table from Transaction, not a status flag on it -- every balance/summary
  query already assumes every Transaction row is real money that moved;
  keeping unconfirmed candidates out of that table entirely means there's
  no risk of a query forgetting to filter them out.
"""
import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AccountType(str, enum.Enum):
    CASH = "cash"
    CHECKING = "checking"
    CREDIT_CARD = "credit_card"


class TransactionType(str, enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"


class ReimbursementStatus(str, enum.Enum):
    PENDING = "pending"
    RECEIVED = "received"


class CategoryKind(str, enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[AccountType] = mapped_column(SAEnum(AccountType))
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    opening_balance_date: Mapped[date] = mapped_column(Date, default=date.today)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    transactions_from: Mapped[list["Transaction"]] = relationship(
        "Transaction", foreign_keys="Transaction.account_id", back_populates="account"
    )
    transactions_to: Mapped[list["Transaction"]] = relationship(
        "Transaction", foreign_keys="Transaction.to_account_id", back_populates="to_account"
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[CategoryKind] = mapped_column(SAEnum(CategoryKind))

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="category")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    type: Mapped[TransactionType] = mapped_column(SAEnum(TransactionType))

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    # Only set for transfers (e.g. paying down a credit card from checking).
    to_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    # Nullable because transfers don't need a spending category.
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    reimbursable: Mapped[bool] = mapped_column(Boolean, default=False)
    reimbursement_status: Mapped[ReimbursementStatus | None] = mapped_column(
        SAEnum(ReimbursementStatus), nullable=True
    )

    # A big one-off cost (tuition, a deposit) that shouldn't count toward
    # "living expenses" -- lets the dashboard show spend with and without
    # it, instead of one number that either hides or overstates it.
    exclude_from_living: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped["Account"] = relationship(
        "Account", foreign_keys=[account_id], back_populates="transactions_from"
    )
    to_account: Mapped["Account | None"] = relationship(
        "Account", foreign_keys=[to_account_id], back_populates="transactions_to"
    )
    category: Mapped["Category | None"] = relationship(back_populates="transactions")


class PendingImport(Base):
    """A transaction candidate parsed from pasted statement text, sitting
    in a review queue -- never counted in any balance/summary/trend
    calculation (those all query Transaction, not this table). Only
    becomes a real Transaction if the user confirms it; deleted either way
    once resolved (confirmed or discarded), never left lingering."""

    __tablename__ = "pending_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    merchant: Mapped[str] = mapped_column(Text)
    suggested_type: Mapped[TransactionType] = mapped_column(SAEnum(TransactionType))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    # Computed at import time by checking for an existing Transaction with
    # the same account/amount and a nearby date -- surfaced in the review
    # list so an already-hand-logged transaction doesn't get double-counted.
    possible_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped["Account"] = relationship("Account", foreign_keys=[account_id])
