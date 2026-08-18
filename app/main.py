from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import Base, SessionLocal, engine
from app.routers import accounts, dashboard, transactions
from app.services import seed_default_categories

app = FastAPI(title="Expense Tracker")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(dashboard.router)
app.include_router(accounts.router)
app.include_router(transactions.router)


@app.on_event("startup")
def on_startup():
    # Dev-friendly: creates tables if they don't exist yet (SQLite by
    # default). For Prod we'll switch to real migrations (Alembic) once
    # the schema stabilizes -- fine to auto-create for now.
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_default_categories(db)
    finally:
        db.close()
