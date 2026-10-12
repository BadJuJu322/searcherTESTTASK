"""Работа с поисковым индексом Elasticsearch."""

from collections.abc import Iterable

from elasticsearch import AsyncElasticsearch, BadRequestError, NotFoundError
from elasticsearch.helpers import async_bulk

# Структура индекса по ТЗ: id и text.
# Анализатор "russian" встроен в Elasticsearch: приводит слова к основе
# («конкурсы» -> «конкурс») и отбрасывает стоп-слова («и», «в», «на»).
INDEX_SETTINGS = {"number_of_shards": 1, "number_of_replicas": 0}
INDEX_MAPPINGS = {
    "properties": {
        "id": {"type": "long"},
        "text": {"type": "text", "analyzer": "russian"},
    }
}


def build_text_query(text: str) -> dict:
    """Запрос к индексу по произвольному тексту.

    operator=and: документ должен содержать ВСЕ слова запроса. Результаты потом
    сортируются по дате, а не по релевантности, поэтому «хоть одно слово» (or)
    давало бы много случайных совпадений.
    """
    return {"match": {"text": {"query": text, "operator": "and"}}}


async def ensure_index(es: AsyncElasticsearch, index: str) -> None:
    if await es.indices.exists(index=index):
        return
    try:
        await es.indices.create(index=index, settings=INDEX_SETTINGS, mappings=INDEX_MAPPINGS)
    except BadRequestError as exc:
        # другой процесс (например, загрузчик) успел создать индекс раньше нас
        if exc.error != "resource_already_exists_exception":
            raise


async def bulk_index(es: AsyncElasticsearch, index: str, docs: Iterable[tuple[int, str]]) -> None:
    """Индексирует пары (id, text) одним bulk-запросом."""
    actions = (
        {"_index": index, "_id": str(doc_id), "_source": {"id": doc_id, "text": text}}
        for doc_id, text in docs
    )
    await async_bulk(es, actions)
    await es.indices.refresh(index=index)


async def search_ids(es: AsyncElasticsearch, index: str, text: str, limit: int) -> list[int]:
    """Возвращает id документов, подходящих под запрос (по убыванию релевантности)."""
    response = await es.search(
        index=index,
        query=build_text_query(text),
        size=limit,
        source_includes=["id"],
        track_total_hits=False,
    )
    return [hit["_source"]["id"] for hit in response["hits"]["hits"]]


async def delete_from_index(es: AsyncElasticsearch, index: str, doc_id: int) -> bool:
    """Удаляет документ из индекса. False, если его там не было."""
    try:
        await es.delete(index=index, id=str(doc_id))
    except NotFoundError:
        return False
    return True
