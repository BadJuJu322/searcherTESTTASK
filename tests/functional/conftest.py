"""Фикстуры функциональных тестов.

Тесты ходят в настоящие PostgreSQL и Elasticsearch (см. docker-compose.yml),
но в ОТДЕЛЬНУЮ базу и ОТДЕЛЬНЫЙ индекс с суффиксом _test — рабочие данные не трогаем.
"""

from contextlib import asynccontextmanager
from datetime import datetime

import pytest_asyncio
from asgi_lifespan import LifespanManager
from elasticsearch import AsyncElasticsearch
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.load_data import DocumentRow, import_rows
from app.main import create_app
from app.models import Document
from app.search import ensure_index


@pytest_asyncio.fixture(scope="session")
async def settings() -> Settings:
    base = Settings()
    return base.model_copy(
        update={
            "postgres_db": f"{base.postgres_db}_test",
            "elastic_index": f"{base.elastic_index}_test",
        }
    )


async def _create_database_if_missing(settings: Settings) -> None:
    """CREATE DATABASE нельзя выполнять внутри транзакции, поэтому AUTOCOMMIT."""
    admin_url = settings.database_url.set(database="postgres")
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": settings.postgres_db},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{settings.postgres_db}"'))
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def app(settings):
    await _create_database_if_missing(settings)
    application = create_app(settings)
    async with LifespanManager(application):  # выполняет lifespan: таблица + индекс
        yield application


@pytest_asyncio.fixture(autouse=True)
async def clean_state(app, settings):
    """Перед каждым тестом: пустая таблица и пустой индекс."""
    async with app.state.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {Document.__tablename__} RESTART IDENTITY"))
    await app.state.es.indices.delete(index=settings.elastic_index, ignore_unavailable=True)
    await ensure_index(app.state.es, settings.elastic_index)


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seed(app, settings):
    """Кладёт документы в БД и индекс тем же кодом, что и загрузчик CSV."""

    async def _seed(*items: tuple[str, str]) -> list[Document]:
        """items: (text, 'YYYY-MM-DD HH:MM:SS')"""
        rows = [
            DocumentRow(
                text=text_,
                created_date=datetime.strptime(date, "%Y-%m-%d %H:%M:%S"),
                rubrics=["VK-1", "VK-2"],
            )
            for text_, date in items
        ]
        return await import_rows(
            app.state.session_factory, app.state.es, settings.elastic_index, rows
        )

    return _seed


@pytest_asyncio.fixture
async def es_down(app):
    """Контекст, внутри которого приложение «видит» недоступный Elasticsearch."""

    @asynccontextmanager
    async def _down():
        real_es = app.state.es
        app.state.es = AsyncElasticsearch("http://127.0.0.1:1", max_retries=0)  # порт закрыт
        try:
            yield
        finally:
            await app.state.es.close()
            app.state.es = real_es

    return _down
