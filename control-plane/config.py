import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    database_url: str = os.environ["DATABASE_URL"]
    razorpay_merchant_token: str = os.environ["RAZORPAY_MERCHANT_TOKEN"]
    razorpay_mcp_url: str = os.getenv("RAZORPAY_MCP_URL", "https://mcp.razorpay.com/mcp")
    env: str = os.getenv("ENV", "development")
    mandate_keyring_dir: str = os.getenv("MANDATE_KEYRING_DIR", "./secrets")


@lru_cache
def get_settings() -> Settings:
    return Settings()
