"""Генерирует docs.json (OpenAPI) из текущего кода приложения.

Запуск из корня проекта:
    python -m scripts.export_openapi
"""

import json
from pathlib import Path

from app.main import create_app

OUTPUT = Path(__file__).resolve().parent.parent / "docs.json"


def build_openapi() -> dict:
    return create_app().openapi()


if __name__ == "__main__":
    OUTPUT.write_text(
        json.dumps(build_openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Записан {OUTPUT}")
