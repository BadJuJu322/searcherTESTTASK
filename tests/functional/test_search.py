async def test_search_returns_document_with_all_db_fields(client, seed):
    [doc] = await seed(("Конкурс на новый скин для героя", "2019-07-25 12:42:13"))

    response = await client.get("/documents/search", params={"q": "скин"})

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": doc.id,
            "rubrics": ["VK-1", "VK-2"],
            "text": "Конкурс на новый скин для героя",
            "created_date": "2019-07-25T12:42:13",
        }
    ]


async def test_search_ignores_documents_without_match(client, seed):
    await seed(
        ("Конкурс на новый скин", "2019-01-01 10:00:00"),
        ("Совсем другой текст про погоду", "2019-01-02 10:00:00"),
    )

    response = await client.get("/documents/search", params={"q": "скин"})

    assert [d["text"] for d in response.json()] == ["Конкурс на новый скин"]


async def test_search_understands_russian_word_forms(client, seed):
    """Анализатор russian приводит «конкурсы» и «конкурс» к одной основе."""
    await seed(("Новые конкурсы каждую неделю", "2019-01-01 10:00:00"))

    response = await client.get("/documents/search", params={"q": "конкурс"})

    assert len(response.json()) == 1


async def test_search_requires_all_words_of_query(client, seed):
    await seed(
        ("Конкурс на новый скин", "2019-01-01 10:00:00"),
        ("Конкурс репостов", "2019-01-02 10:00:00"),
    )

    response = await client.get("/documents/search", params={"q": "конкурс скин"})

    assert [d["text"] for d in response.json()] == ["Конкурс на новый скин"]


async def test_search_sorts_by_created_date_newest_first(client, seed):
    # порядок вставки намеренно не совпадает с порядком дат
    await seed(
        ("котики 2", "2019-05-01 00:00:00"),
        ("котики 3", "2019-12-31 00:00:00"),
        ("котики 1", "2019-01-01 00:00:00"),
    )

    response = await client.get("/documents/search", params={"q": "котики"})

    dates = [d["created_date"] for d in response.json()]
    assert dates == sorted(dates, reverse=True)
    assert [d["text"] for d in response.json()] == ["котики 3", "котики 2", "котики 1"]


async def test_search_returns_only_20_newest_documents(client, seed):
    # 25 документов, у i-го дата 2019-01-i. Ожидаем 25..6
    await seed(*[(f"котики номер {i}", f"2019-01-{i:02d} 12:00:00") for i in range(1, 26)])

    response = await client.get("/documents/search", params={"q": "котики"})

    body = response.json()
    assert len(body) == 20
    assert body[0]["text"] == "котики номер 25"
    assert body[-1]["text"] == "котики номер 6"


async def test_search_without_matches_returns_empty_list(client, seed):
    await seed(("Конкурс на новый скин", "2019-01-01 10:00:00"))

    response = await client.get("/documents/search", params={"q": "несуществующееслово"})

    assert response.status_code == 200
    assert response.json() == []


async def test_search_on_empty_index_returns_empty_list(client):
    response = await client.get("/documents/search", params={"q": "что угодно"})

    assert response.status_code == 200
    assert response.json() == []


async def test_search_rejects_missing_or_blank_query(client):
    assert (await client.get("/documents/search")).status_code == 422
    assert (await client.get("/documents/search", params={"q": ""})).status_code == 422
    assert (await client.get("/documents/search", params={"q": "   "})).status_code == 422


async def test_search_query_length_limit(client):
    assert (await client.get("/documents/search", params={"q": "а" * 500})).status_code == 200
    assert (await client.get("/documents/search", params={"q": "а" * 501})).status_code == 422


async def test_search_returns_503_when_elasticsearch_is_down(client, es_down):
    async with es_down():
        response = await client.get("/documents/search", params={"q": "скин"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Поисковый индекс недоступен"}
