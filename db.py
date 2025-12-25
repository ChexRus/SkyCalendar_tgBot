import asyncpg
from config import DATABASE_URL

async def create_pool():
    return await asyncpg.create_pool(DATABASE_URL)

async def add_user(pool, user_id, username):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users(user_id, username) VALUES($1, $2) ON CONFLICT (user_id) DO NOTHING",
            user_id, username
        )

async def get_users(pool):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM users")
