import asyncpg
import os

DATABASE_URL = os.environ.get("DATABASE_URL")

async def get_pool():
    return await asyncpg.create_pool(DATABASE_URL)
