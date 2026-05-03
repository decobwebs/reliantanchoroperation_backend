from typing import Any, Generic, List, Optional, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class StandardResponse(BaseModel, Generic[T]):
    success: bool = True
    data: Optional[T] = None
    message: str = ""
    errors: List[str] = []

    @classmethod
    def ok(cls, data: Any = None, message: str = "") -> "StandardResponse":
        return cls(success=True, data=data, message=message, errors=[])

    @classmethod
    def fail(cls, message: str = "", errors: List[str] = None) -> "StandardResponse":
        return cls(success=False, data=None, message=message, errors=errors or [])


class PaginatedData(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    per_page: int
    pages: int


class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = True
    data: PaginatedData[T]
    message: str = ""
    errors: List[str] = []

    @classmethod
    def ok(cls, items: List[Any], total: int, page: int, per_page: int) -> "PaginatedResponse":
        pages = (total + per_page - 1) // per_page if per_page > 0 else 0
        return cls(
            success=True,
            data=PaginatedData(items=items, total=total, page=page, per_page=per_page, pages=pages),
            message="",
            errors=[],
        )
