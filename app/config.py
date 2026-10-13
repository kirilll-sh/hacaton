from __future__ import annotations

import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
CLASS_PASSWORD_KEY = os.getenv("CLASS_PASSWORD_KEY")

POSTGRES_USER = os.getenv("POSTGRES_USER", "hq_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "change_me")
POSTGRES_DB = os.getenv("POSTGRES_DB", "school_hq_db")
DATABASE_URL = os.getenv("DATABASE_URL") or (
    "postgresql+asyncpg://"
    f"{quote_plus(POSTGRES_USER)}:{quote_plus(POSTGRES_PASSWORD)}"
    f"@localhost:5432/{quote_plus(POSTGRES_DB)}"
)
