"""Ma'lumotlar bazasi: PostgreSQL ulab, so'rovlarni qabul qiluvchi sinf."""

import asyncpg


def _as_dict(rec):
    if rec is None:
        return None
    return {k: rec[k] for k in rec.keys()}


class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: "asyncpg.Pool | None" = None

    async def connect(self) -> None:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(self.dsn)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def create_tables(self) -> None:
        async with self.pool.acquire() as conn:
            for stmt in SCHEMA:
                await conn.execute(stmt)

    # ---------------- users ----------------
    async def get_user_by_tg_id(self, telegram_id: int):
        async with self.pool.acquire() as conn:
            return _as_dict(
                await conn.fetchrow(
                    f"SELECT {USER_COLS} FROM users WHERE telegram_id = $1", telegram_id
                )
            )

    async def create_user(self, username: str, login: str, password: str,
                          is_admin: bool = False, telegram_id: int | None = None):
        async with self.pool.acquire() as conn:
            rec = await conn.fetchrow(
                f"""INSERT INTO users (username, login, password, is_admin, telegram_id)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING {USER_COLS}""",
                username or "", login, password or "", is_admin, telegram_id,
            )
            return _as_dict(rec)

    async def get_user(self, user_id: int):
        async with self.pool.acquire() as conn:
            rec = await conn.fetchrow(f"SELECT {USER_COLS} FROM users WHERE id = $1", user_id)
            return _as_dict(rec)

    async def update_user(self, user_id: int, username=None, login=None, password=None):
        assignments, params = [], []
        if username is not None:
            params.append(username)
            assignments.append(f"username = ${len(params)}")
        if login is not None:
            params.append(login)
            assignments.append(f"login = ${len(params)}")
        if password is not None:
            params.append(password)
            assignments.append(f"password = ${len(params)}")
        params.append(user_id)
        if not assignments:
            return None
        sql = (
            f"UPDATE users SET {', '.join(assignments)} "
            f"WHERE id = ${len(params)} RETURNING {USER_COLS}"
        )
        async with self.pool.acquire() as conn:
            return _as_dict(await conn.fetchrow(sql, *params))

    async def delete_user(self, user_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM users WHERE id = $1", user_id)

    async def list_users(self):
        async with self.pool.acquire() as conn:
            recs = await conn.fetch(f"SELECT {USER_COLS} FROM users ORDER BY id")
            return [_as_dict(r) for r in recs]

    # ---------------- arenas ----------------
    async def add_arena(self, name: str):
        async with self.pool.acquire() as conn:
            rec = await conn.fetchrow(
                "INSERT INTO arenas (name, is_active) VALUES ($1, FALSE) "
                "RETURNING id, name, is_active, created_at",
                name,
            )
            return _as_dict(rec)

    async def get_arena(self, arena_id: int):
        async with self.pool.acquire() as conn:
            rec = await conn.fetchrow(
                "SELECT id, name, is_active, created_at FROM arenas WHERE id = $1", arena_id
            )
            return _as_dict(rec)

    async def list_arenas(self, active_only: bool = False):
        async with self.pool.acquire() as conn:
            sql = "SELECT id, name, is_active, created_at FROM arenas"
            if active_only:
                sql += " WHERE is_active = TRUE"
            sql += " ORDER BY id"
            recs = await conn.fetch(sql)
            return [_as_dict(r) for r in recs]

    async def set_arena_active(self, arena_id: int, is_active: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE arenas SET is_active = $1 WHERE id = $2", is_active, arena_id)

    async def delete_arena(self, arena_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM arenas WHERE id = $1", arena_id)

    # ---------------- questions ----------------
    async def add_question(self, arena_id: int, question: str, options, correct: int):
        options = list(options)[:4] + ["", "", "", ""]
        position = len(await self.list_questions(arena_id))
        async with self.pool.acquire() as conn:
            rec = await conn.fetchrow(
                f"""INSERT INTO questions
                    (arena_id, question, option_a, option_b, option_c, option_d,
                     correct_option, position)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING {QUESTION_COLS}""",
                arena_id, question,
                options[0] or "", options[1] or "", options[2] or "", options[3] or "",
                int(correct), position,
            )
            return _as_dict(rec)

    async def get_question(self, question_id: int):
        async with self.pool.acquire() as conn:
            rec = await conn.fetchrow(f"SELECT {QUESTION_COLS} FROM questions WHERE id = $1", question_id)
            return _as_dict(rec)

    async def list_questions(self, arena_id: int):
        async with self.pool.acquire() as conn:
            recs = await conn.fetch(
                f"SELECT {QUESTION_COLS} FROM questions WHERE arena_id = $1 ORDER BY position, id",
                arena_id,
            )
            return [_as_dict(r) for r in recs]

    async def delete_question(self, question_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM questions WHERE id = $1", question_id)


USER_COLS = "id, telegram_id, username, login, password, is_admin, created_at"
QUESTION_COLS = "id, arena_id, question, option_a, option_b, option_c, option_d, correct_option"

# Ma'lumotlar bazasi sxemasi (bot ishga tushganda, jadval yo'q bo'lsa yaratiladi)
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        telegram_id BIGINT,
        username TEXT NOT NULL DEFAULT '',
        login TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL DEFAULT '',
        is_admin BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS arenas (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS questions (
        id SERIAL PRIMARY KEY,
        arena_id INTEGER NOT NULL REFERENCES arenas(id) ON DELETE CASCADE,
        question TEXT NOT NULL,
        option_a TEXT NOT NULL DEFAULT '',
        option_b TEXT NOT NULL DEFAULT '',
        option_c TEXT NOT NULL DEFAULT '',
        option_d TEXT NOT NULL DEFAULT '',
        correct_option SMALLINT NOT NULL DEFAULT 1,
        position INTEGER NOT NULL DEFAULT 0
    )
    """,
]
