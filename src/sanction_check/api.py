from fastapi import FastAPI
from scalar_fastapi import add_scalar_reference

from sanction_check.schemas.screening_schema import ScreeningInput, ScreeningResult
from sanction_check.screening.supplier_screening import screen_supplier

app = FastAPI(title="SanctionCheck")

add_scalar_reference(app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/screen")
def screen(supplier: ScreeningInput, web_search: bool = False) -> ScreeningResult:
    return screen_supplier(supplier, should_search_web=web_search)
