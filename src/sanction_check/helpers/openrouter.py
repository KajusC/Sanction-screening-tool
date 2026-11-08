import openai

from sanction_check.settings import get_settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DENY_DATA_COLLECTION = {"data_collection": "deny"}


def build_openrouter_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url=OPENROUTER_BASE_URL, api_key=get_settings().openrouter_api_key.get_secret_value()
    )
