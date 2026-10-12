import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from elastic_transport import TransportError
from elasticsearch import ApiError, AsyncElasticsearch
from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import StringConstraints
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.models import Base, Document
from app.schemas import DocumentOut, ErrorOut
from app.search import delete_from_index, ensure_index, search_ids

logger = logging.getLogger(__name__)

DESCRIPTION = """
Простой поисковик по текстам документов.

* Документы хранятся в **PostgreSQL**, поисковый индекс — в **Elasticsearch**.
* Поиск идёт по тексту документа с учётом морфологии русского языка.
"""

# ---------- зависимости: всё берём из app.state, созданного в lifespan ----------


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_es(request: Request) -> AsyncElasticsearch:
    return request.app.state.es


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


SessionDep = Annotated[AsyncSession, Depends(get_session)]
EsDep = Annotated[AsyncElasticsearch, Depends(get_es)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]

# ---------- ручки ----------

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get(
    "/search",
    response_model=list[DocumentOut],
    summary="Поиск документов по тексту",
    responses={503: {"model": ErrorOut, "description": "Поисковый индекс недоступен"}},
)
async def search_documents(
    q: Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)],
        Query(description="Произвольный текстовый запрос", examples=["конкурс скин"]),
    ],
    session: SessionDep,
    es: EsDep,
    settings: SettingsDep,
):
    """Ищет документы по тексту в индексе и возвращает **первые 20** со всеми полями из БД.

    * Документ подходит, если в нём есть **все** слова запроса (с учётом словоформ).
    * Результаты упорядочены по дате создания: **сначала самые новые**.
    * Если ничего не найдено — пустой список.
    """
    ids = await search_ids(es, settings.elastic_index, q, settings.search_max_candidates)
    if not ids:
        return []

    # Индекс хранит только id и text, поэтому даты и остальные поля берём из БД
    # и сортируем/ограничиваем уже там.
    stmt = (
        select(Document)
        .where(Document.id.in_(ids))
        .order_by(Document.created_date.desc(), Document.id.desc())
        .limit(settings.search_page_size)
    )
    return (await session.scalars(stmt)).all()


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить документ",
    responses={
        404: {"model": ErrorOut, "description": "Документ не найден"},
        503: {"model": ErrorOut, "description": "Поисковый индекс недоступен"},
    },
)
async def delete_document(
    document_id: Annotated[int, Path(ge=1, le=2**63 - 1, description="id документа")],
    session: SessionDep,
    es: EsDep,
    settings: SettingsDep,
):
    """Удаляет документ из БД и из поискового индекса.

    Операция идемпотентна по частичным сбоям: если в прошлый раз документ успел
    удалиться только из БД (индекс был недоступен), повторный вызов дочистит индекс.
    """
    deleted_id = await session.scalar(
        delete(Document).where(Document.id == document_id).returning(Document.id)
    )
    await session.commit()
    deleted_in_db = deleted_id is not None

    deleted_in_index = await delete_from_index(es, settings.elastic_index, document_id)

    if not (deleted_in_db or deleted_in_index):
        return JSONResponse({"detail": "Документ не найден"}, status_code=404)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- приложение ----------


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        es = AsyncElasticsearch(settings.elastic_url)

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        app.state.es = es

        # Для тестового задания таблицу создаём на старте. В проде — миграции (Alembic).
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await ensure_index(es, settings.elastic_index)

        yield

        await es.close()
        await engine.dispose()

    app = FastAPI(
        title="Document Search Service",
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.exception_handler(TransportError)
    @app.exception_handler(ApiError)
    async def elastic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Elasticsearch error: %s", exc)
        return JSONResponse({"detail": "Поисковый индекс недоступен"}, status_code=503)

    return app


app = create_app()
