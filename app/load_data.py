"""Загрузка документов из CSV в БД и в поисковый индекс.

Запуск:
    python -m app.load_data data/posts.csv            # загрузить, если БД пустая
    python -m app.load_data data/posts.csv --reset    # стереть всё и загрузить заново
"""

import argparse
import asyncio
import ast
import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from elasticsearch import AsyncElasticsearch
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base, Document
from app.search import bulk_index, ensure_index

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class DocumentRow:
    text: str
    created_date: datetime
    rubrics: list[str]


def parse_rubrics(raw: str) -> list[str]:
    """'['a', 'b']' (строка с питоновским списком) -> ['a', 'b'].

    literal_eval, а не eval: он разбирает только литералы и не выполняет код.
    """
    value = ast.literal_eval(raw)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"rubrics должен быть списком строк, получено: {raw!r}")
    return value


def parse_row(row: dict[str, str]) -> DocumentRow:
    return DocumentRow(
        text=row["text"],
        created_date=datetime.strptime(row["created_date"], DATE_FORMAT),
        rubrics=parse_rubrics(row["rubrics"]),
    )


def read_csv(path: Path) -> list[DocumentRow]:
    rows: list[DocumentRow] = []
    with path.open(encoding="utf-8", newline="") as file:
        for number, raw in enumerate(csv.DictReader(file), start=1):
            try:
                rows.append(parse_row(raw))
            except (KeyError, ValueError, SyntaxError) as exc:
                raise ValueError(f"Запись №{number}: {exc}") from exc
    return rows


async def import_rows(
    session_factory: async_sessionmaker,
    es: AsyncElasticsearch,
    index: str,
    rows: list[DocumentRow],
) -> list[Document]:
    """Сохраняет документы в БД и индексирует их. Возвращает созданные документы."""
    async with session_factory() as session:
        docs = [Document(text=r.text, created_date=r.created_date, rubrics=r.rubrics) for r in rows]
        session.add_all(docs)
        await session.flush() 
        await bulk_index(es, index, [(doc.id, doc.text) for doc in docs])

        await session.commit()
    return docs


async def run(csv_path: Path, reset: bool) -> None:
    settings = get_settings()
    rows = read_csv(csv_path)
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    es = AsyncElasticsearch(settings.elastic_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if reset:
                await conn.execute(text(f"TRUNCATE {Document.__tablename__} RESTART IDENTITY"))
        if reset:
            await es.indices.delete(index=settings.elastic_index, ignore_unavailable=True)
        await ensure_index(es, settings.elastic_index)

        async with session_factory() as session:
            existing = await session.scalar(select(func.count()).select_from(Document))
        if existing:
            print(f"В БД уже {existing} документов. Пропускаю (для перезагрузки: --reset).")
            return

        docs = await import_rows(session_factory, es, settings.elastic_index, rows)
        print(f"Загружено документов: {len(docs)}")
    finally:
        await es.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка документов из CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--reset", action="store_true", help="удалить существующие данные")
    args = parser.parse_args()
    asyncio.run(run(args.csv_path, args.reset))


if __name__ == "__main__":
    main()
