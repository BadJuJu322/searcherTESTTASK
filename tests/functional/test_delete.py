from sqlalchemy import func, select

from app.models import Document


async def _count_in_db(app) -> int:
    async with app.state.session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Document))


async def test_delete_removes_document_from_db_and_index(client, app, settings, seed):
    keep, remove = await seed(
        ("котики остаются", "2019-01-01 10:00:00"),
        ("котики удаляются", "2019-01-02 10:00:00"),
    )

    response = await client.delete(f"/documents/{remove.id}")

    assert response.status_code == 204
    assert response.content == b""
    assert await _count_in_db(app) == 1
    assert not await app.state.es.exists(index=settings.elastic_index, id=str(remove.id))
    assert await app.state.es.exists(index=settings.elastic_index, id=str(keep.id))


async def test_deleted_document_is_not_found_by_search(client, seed):
    [doc] = await seed(("котики удаляются", "2019-01-02 10:00:00"))
    assert len((await client.get("/documents/search", params={"q": "котики"})).json()) == 1

    await client.delete(f"/documents/{doc.id}")

    assert (await client.get("/documents/search", params={"q": "котики"})).json() == []


async def test_delete_unknown_document_returns_404(client):
    response = await client.delete("/documents/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Документ не найден"}


async def test_delete_twice_returns_404_second_time(client, seed):
    [doc] = await seed(("котики", "2019-01-01 10:00:00"))

    assert (await client.delete(f"/documents/{doc.id}")).status_code == 204
    assert (await client.delete(f"/documents/{doc.id}")).status_code == 404


async def test_delete_cleans_index_if_document_already_gone_from_db(client, app, seed):
    """Повтор после частичного сбоя: в БД записи уже нет, а в индексе она осталась."""
    [doc] = await seed(("котики", "2019-01-01 10:00:00"))
    async with app.state.session_factory() as session:
        await session.delete(await session.get(Document, doc.id))
        await session.commit()

    response = await client.delete(f"/documents/{doc.id}")

    assert response.status_code == 204


async def test_delete_works_if_document_missing_from_index(client, app, settings, seed):
    [doc] = await seed(("котики", "2019-01-01 10:00:00"))
    await app.state.es.delete(index=settings.elastic_index, id=str(doc.id))

    response = await client.delete(f"/documents/{doc.id}")

    assert response.status_code == 204
    assert await _count_in_db(app) == 0


async def test_delete_rejects_invalid_id(client):
    assert (await client.delete("/documents/abc")).status_code == 422
    assert (await client.delete("/documents/0")).status_code == 422
    assert (await client.delete(f"/documents/{10**30}")).status_code == 422


async def test_delete_with_index_down_returns_503_and_retry_cleans_index(
    client, app, settings, seed, es_down
):
    """Главный сценарий идемпотентности: БД почистилась, индекс упал, повтор дочищает."""
    [doc] = await seed(("котики", "2019-01-01 10:00:00"))

    async with es_down():
        first = await client.delete(f"/documents/{doc.id}")

    assert first.status_code == 503
    assert await _count_in_db(app) == 0  # из БД запись уже удалена
    assert await app.state.es.exists(index=settings.elastic_index, id=str(doc.id))  # в индексе осталась

    second = await client.delete(f"/documents/{doc.id}")  # индекс снова доступен

    assert second.status_code == 204
    assert not await app.state.es.exists(index=settings.elastic_index, id=str(doc.id))
