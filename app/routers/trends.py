from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category, CategoryKind, Transaction, TransactionType
from app.services import (
    get_balance_history,
    get_category_color_series,
    get_monthly_all_categories_trend,
    get_monthly_single_category_trend,
)
from app.templating import templates

router = APIRouter()

MAX_MONTHS = 36  # keeps the chart from rendering hundreds of unreadable bars

BALANCE_CHART_W = 640
BALANCE_CHART_H = 160
GRANULARITIES = [("day", "Daily"), ("week", "Weekly"), ("month", "Monthly")]


def _parse_month(value: str | None) -> date | None:
    if not value:
        return None
    try:
        year, month = value.split("-")
        return date(int(year), int(month), 1)
    except (ValueError, TypeError):
        return None


def _category_trend_view(
    db: Session,
    selected_category_id: int | None,
    selected_category: Category | None,
    start_month: date,
    months: int,
    living_only: bool,
) -> dict:
    """Shared by the "All spending" and "Living expenses" toggle panels --
    same {data, legend, max_total} shape either way, just filtered
    differently, so the template renders both with one macro-like block."""
    if selected_category_id is not None:
        # Isolated: just this category's own trend, no comparison against
        # everything else -- shares the same {month_label, segments,
        # total} shape as the all-categories view below so the template
        # doesn't need two different chart-rendering branches.
        raw = get_monthly_single_category_trend(
            db, selected_category_id, start_month, months, living_only=living_only
        )
        color_series = get_category_color_series(db)
        color = next(
            (s["color"] for s in color_series if selected_category_id in s["category_ids"]),
            "#256abf",
        )
        data = [
            {
                "month_label": d["month_label"],
                "segments": [
                    {
                        "label": selected_category.name,
                        "color": color,
                        "amount": d["amount"],
                        "rounded_top": True,
                    }
                ],
                "by_label": {selected_category.name: d["amount"]},
                "total": d["amount"],
            }
            for d in raw
        ]
        legend = [{"label": selected_category.name, "color": color}] if data else []
    else:
        data, legend = get_monthly_all_categories_trend(
            db, start_month, months, living_only=living_only
        )
    max_total = max((d["total"] for d in data), default=Decimal(0))
    return {"data": data, "legend": legend, "max_total": max_total}


def _balance_chart_view(db: Session, start: date, end: date, granularity: str) -> dict:
    """Line-chart geometry for one granularity, pre-computed in Python so
    the template just draws points -- an SVG viewBox of fixed
    BALANCE_CHART_W x BALANCE_CHART_H, coordinates scaled to the actual
    min/max balance in range."""
    points = get_balance_history(db, start, end, granularity)
    if not points:
        return {
            "granularity": granularity,
            "coords": [],
            "line_path": "",
            "area_path": "",
            "latest": None,
            "x_labels": [],
        }

    values = [p["balance"] for p in points]
    min_v, max_v = min(values), max(values)
    n = len(points)
    flat = max_v == min_v

    coords = []
    for i, p in enumerate(points):
        x = (i / (n - 1) * BALANCE_CHART_W) if n > 1 else BALANCE_CHART_W / 2
        if flat:
            y = BALANCE_CHART_H / 2
        else:
            y = BALANCE_CHART_H - float((p["balance"] - min_v) / (max_v - min_v)) * BALANCE_CHART_H
        coords.append(
            {
                "x": round(x, 1),
                "y": round(y, 1),
                "tooltip": f"{p['date'].strftime('%b %d, %Y')}: ${p['balance']:.2f}",
            }
        )

    line_path = "M " + " L ".join(f"{c['x']},{c['y']}" for c in coords)
    area_path = (
        line_path + f" L {coords[-1]['x']},{BALANCE_CHART_H} L {coords[0]['x']},{BALANCE_CHART_H} Z"
    )

    # At most 6 x-axis labels, evenly spaced by index -- always including
    # the first and last point, however many total points there are.
    label_count = min(6, n)
    label_indices = (
        sorted({round(i * (n - 1) / (label_count - 1)) for i in range(label_count)})
        if label_count > 1
        else [0]
    )
    date_fmt = "%b %Y" if granularity == "month" else "%b %d"
    x_labels = [
        {"x": coords[i]["x"], "text": points[i]["date"].strftime(date_fmt)} for i in label_indices
    ]

    return {
        "granularity": granularity,
        "coords": coords,
        "line_path": line_path,
        "area_path": area_path,
        "latest": points[-1]["balance"],
        "x_labels": x_labels,
    }


@router.get("/trends")
def trends(
    request: Request,
    category_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
):
    categories = db.scalars(
        select(Category).where(Category.kind == CategoryKind.EXPENSE).order_by(Category.name)
    ).all()

    # No category_id (missing, or the "All categories" option's empty
    # value) means don't split anything out -- every expense counts as
    # one series, not a category-vs-rest breakdown.
    selected_category_id = int(category_id) if category_id else None
    selected_category = next((c for c in categories if c.id == selected_category_id), None)

    today = date.today()
    this_month = date(today.year, today.month, 1)

    end_month = _parse_month(end) or this_month

    start_month = _parse_month(start)
    if start_month is None:
        earliest_expense = db.scalar(
            select(func.min(Transaction.date)).where(Transaction.type == TransactionType.EXPENSE)
        )
        start_month = (
            date(earliest_expense.year, earliest_expense.month, 1)
            if earliest_expense
            else end_month - relativedelta(months=5)
        )

    # A picker that's backwards or absurdly wide would otherwise render a
    # chart nobody can read -- swap and cap instead of erroring.
    if start_month > end_month:
        start_month, end_month = end_month, start_month
    months = (end_month.year - start_month.year) * 12 + (end_month.month - start_month.month) + 1
    if months > MAX_MONTHS:
        start_month = end_month - relativedelta(months=MAX_MONTHS - 1)
        months = MAX_MONTHS

    view_all = _category_trend_view(
        db, selected_category_id, selected_category, start_month, months, living_only=False
    )
    view_living = _category_trend_view(
        db, selected_category_id, selected_category, start_month, months, living_only=True
    )

    balance_views = {
        g: _balance_chart_view(db, start_month, end_month, g) for g, _ in GRANULARITIES
    }

    return templates.TemplateResponse(
        request,
        "trends.html",
        {
            "categories": categories,
            "selected_category_id": selected_category_id,
            "selected_category": selected_category,
            "start_value": start_month.strftime("%Y-%m"),
            "end_value": end_month.strftime("%Y-%m"),
            "view_all": view_all,
            "view_living": view_living,
            "balance_views": balance_views,
            "granularities": GRANULARITIES,
            "balance_chart_w": BALANCE_CHART_W,
            "balance_chart_h": BALANCE_CHART_H,
        },
    )
