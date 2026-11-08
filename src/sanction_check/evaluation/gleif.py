import httpx

from sanction_check.helpers.log import log
from sanction_check.schemas.screening_schema import ScreeningInput

GLEIF_BASE_URL = "https://api.gleif.org/api/v1"
GLEIF_TIMEOUT_SECONDS = 10.0
GLEIF_MAX_PAGE_SIZE = 200
GLEIF_ACCEPT_HEADER = "application/vnd.api+json"


def fetch_companies(client: httpx.Client, country: str, page_number: int, page_size: int) -> list[ScreeningInput]:
    response = client.get(
        "/lei-records",
        params={"filter[entity.legalAddress.country]": country, "page[size]": page_size, "page[number]": page_number},
        headers={"Accept": GLEIF_ACCEPT_HEADER},
    )
    response.raise_for_status()

    entities = [record["attributes"]["entity"] for record in response.json()["data"]]

    return [
        ScreeningInput(
            name=entity["legalName"]["name"], country=entity["legalAddress"]["country"], reg_no=entity["registeredAs"]
        )
        for entity in entities
    ]


def list_companies(country: str, count: int) -> list[ScreeningInput]:
    page_size = min(count, GLEIF_MAX_PAGE_SIZE)
    companies: list[ScreeningInput] = []
    page_number = 1

    with httpx.Client(base_url=GLEIF_BASE_URL, timeout=GLEIF_TIMEOUT_SECONDS) as client:
        while len(companies) < count:
            page = fetch_companies(client, country, page_number, page_size)
            log.info("fetched %d companies for %s, page %d", len(page), country, page_number)
            if not page:
                break

            companies.extend(page)
            page_number += 1

    return companies[:count]
