from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import Base, SessionLocal, engine
from app.routers import accounts, dashboard, imports, transactions, trends
from app.services import seed_default_categories

app = FastAPI(title="Expense Tracker")

# The statement-capture browser extension runs from a chrome-extension://
# origin and posts the captured page text straight to this local server --
# there's no real cross-site risk here (this only ever listens on
# 127.0.0.1, never exposed beyond your own machine), so allowing any
# origin is fine.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(dashboard.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(trends.router)
app.include_router(imports.router)


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
