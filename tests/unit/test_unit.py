"""Быстрые тесты без внешних сервисов."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.load_data import parse_row, parse_rubrics, read_csv
from app.search import build_text_query
from scripts.export_openapi import OUTPUT, build_openapi


def test_build_text_query_requires_all_words():
    assert build_text_query("конкурс скин") == {
        "match": {"text": {"query": "конкурс скин", "operator": "and"}}
    }


def test_parse_rubrics_reads_python_list_literal():
    assert parse_rubrics("['VK-1', 'VK-2']") == ["VK-1", "VK-2"]


@pytest.mark.parametrize("raw", ["VK-1", "[1, 2]", "{'a': 1}", "__import__('os')"])
def test_parse_rubrics_rejects_garbage(raw):
    with pytest.raises((ValueError, SyntaxError)):
        parse_rubrics(raw)


def test_parse_row():
    row = parse_row(
        {"text": "Привет", "created_date": "2019-07-25 12:42:13", "rubrics": "['VK-1']"}
    )

    assert row.text == "Привет"
    assert row.created_date == datetime(2019, 7, 25, 12, 42, 13)
    assert row.rubrics == ["VK-1"]


def test_read_csv_reports_record_number_on_bad_row(tmp_path: Path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "text,created_date,rubrics\n"
        '"ok","2019-01-01 00:00:00","[\'a\']"\n'
        '"bad","не дата","[\'a\']"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Запись №2"):
        read_csv(bad)


def test_sample_csv_is_valid():
    rows = read_csv(Path(__file__).resolve().parents[2] / "data" / "posts.csv")

    assert len(rows) == 1500
    assert all(r.rubrics and r.text for r in rows)


def test_docs_json_is_up_to_date():
    """Если упало — выполните: python -m scripts.export_openapi"""
    assert json.loads(OUTPUT.read_text(encoding="utf-8")) == build_openapi()
