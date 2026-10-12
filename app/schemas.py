from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentOut(BaseModel):
    """Документ со всеми полями из БД."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Уникальный идентификатор документа", examples=[42])
    rubrics: list[str] = Field(
        description="Массив рубрик", examples=[["VK-1603736028819866", "VK-11879320040"]]
    )
    text: str = Field(description="Текст документа")
    created_date: datetime = Field(
        description="Дата создания документа", examples=["2019-07-25T12:42:13"]
    )


class ErrorOut(BaseModel):
    detail: str = Field(examples=["Документ не найден"])
