from fastapi import APIRouter, Depends, HTTPException, status

from concert_pnl.api.schemas import CalculateRequest, ReportResponse
from concert_pnl.api.security import get_current_user
from concert_pnl.core.calculator import calculate
from concert_pnl.core.models import InputError

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/calculate", dependencies=[Depends(get_current_user)])
def calculate_pnl(request: CalculateRequest) -> ReportResponse:
    try:
        event = request.to_core()
    except InputError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    return ReportResponse.model_validate(calculate(event))
