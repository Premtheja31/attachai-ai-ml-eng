import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://kindred:kindred@localhost:5432/kindred_test"
)

import pytest

from app.db import SessionLocal
from app.models import Base
from scripts.init_db import init_db


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    init_db()
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    yield session
    session.close()
