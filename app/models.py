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
  they're just a transaction with a non-monthly `cadence`, which dashboards
  can use to compute smoothed/trailing views instead of raw monthly totals.

- Reimbursements are tracked with a lightweight flag + status on the
  original expense, rather than a full receivables ledger. When money
  actually arrives, it's logged as its own INCOME transaction.
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


class Cadence(str, enum.Enum):
    ONE_TIME = "one_time"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMESTERLY = "semesterly"
    ANNUAL = "annual"


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
    cadence: Mapped[Cadence] = mapped_column(SAEnum(Cadence), default=Cadence.ONE_TIME)

    reimbursable: Mapped[bool] = mapped_column(Boolean, default=False)
    reimbursement_status: Mapped[ReimbursementStatus | None] = mapped_column(
        SAEnum(ReimbursementStatus), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped["Account"] = relationship(
        "Account", foreign_keys=[account_id], back_populates="transactions_from"
    )
    to_account: Mapped["Account | None"] = relationship(
        "Account", foreign_keys=[to_account_id], back_populates="transactions_to"
    )
    category: Mapped["Category | None"] = relationship(back_populates="transactions")
