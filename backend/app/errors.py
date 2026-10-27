"""Errors with a machine-readable `code` (SPEC.md section 6, the error table): main.py renders an AppError as
{"detail": ..., "code": ..., "feature"?: ..., "plan"?: ...}. Clients branch on `code`, never on the text."""

from fastapi import HTTPException


class AppError(HTTPException):
    def __init__(
        self,
        status_code: int,
        detail: str,
        code: str,
        *,
        feature: str | None = None,
        plan: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.feature = feature
        self.plan = plan

    def body(self) -> dict[str, str]:
        extra = {key: value for key, value in (("feature", self.feature), ("plan", self.plan)) if value is not None}
        return {"detail": self.detail, "code": self.code, **extra}
