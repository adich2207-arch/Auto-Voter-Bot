"""
database.py — Persistent SQLite storage.

Tables survive restarts and redeployments.
Schema migrations use ALTER TABLE so existing data is never wiped.

Roles
─────
  owner  : set via OWNER_ID in .env — full control
  admin  : promoted by owner — can approve/revoke users
  user   : approved by owner or admin — can use bot features
  (none) : unknown/blocked — sees only a "waiting for access" message
"""

import aiosqlite
import json

DB_PATH = "bot_data.db"   # file persists across restarts


# ══════════════════════════════════════════════════════════════════════
#  INIT & MIGRATIONS
# ══════════════════════════════════════════════════════════════════════

async def init_db():
    """Create tables if missing. Safe to call on every startup — never drops data."""
    async with aiosqlite.connect(DB_PATH) as db:

        # ── users table ────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id       INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                role        TEXT    NOT NULL DEFAULT 'pending',
                approved_by INTEGER,
                joined_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes       TEXT
            )
        """)

        # ── accounts table ─────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id    INTEGER NOT NULL,
                phone       TEXT    NOT NULL,
                session_str TEXT    NOT NULL,
                username    TEXT,
                first_name  TEXT,
                is_active   INTEGER DEFAULT 1,
                added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(owner_id, phone)
            )
        """)

        # ── campaigns table ────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id    INTEGER NOT NULL,
                name        TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                target      TEXT    NOT NULL,
                extra       TEXT    DEFAULT '{}',
                total       INTEGER DEFAULT 0,
                success     INTEGER DEFAULT 0,
                fail        INTEGER DEFAULT 0,
                speed       INTEGER DEFAULT 3,
                status      TEXT    DEFAULT 'pending',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP
            )
        """)

        await db.commit()


# ══════════════════════════════════════════════════════════════════════
#  USER / ROLE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════

async def upsert_user(tg_id: int, username: str = None, first_name: str = None):
    """Insert user on first contact. Never overwrites role of existing user."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users (tg_id, username, first_name)
               VALUES (?, ?, ?)
               ON CONFLICT(tg_id) DO UPDATE SET
                 username   = excluded.username,
                 first_name = excluded.first_name""",
            (tg_id, username, first_name)
        )
        await db.commit()


async def get_user(tg_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def set_role(tg_id: int, role: str, approved_by: int = None):
    """Set role for a user. role ∈ {'owner','admin','user','pending','banned'}"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET role=?, approved_by=? WHERE tg_id=?",
            (role, approved_by, tg_id)
        )
        await db.commit()


async def get_role(tg_id: int, owner_id: int) -> str:
    """Return effective role. Owner is always 'owner' regardless of DB."""
    if tg_id == owner_id:
        return "owner"
    user = await get_user(tg_id)
    return user["role"] if user else "pending"


async def list_users(role_filter: str = None) -> list[dict]:
    """List all users, optionally filtered by role."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if role_filter:
            cur = await db.execute(
                "SELECT * FROM users WHERE role=? ORDER BY joined_at DESC", (role_filter,)
            )
        else:
            cur = await db.execute("SELECT * FROM users ORDER BY joined_at DESC")
        return [dict(r) for r in await cur.fetchall()]


async def list_pending() -> list[dict]:
    return await list_users("pending")


# ══════════════════════════════════════════════════════════════════════
#  ACCOUNTS
# ══════════════════════════════════════════════════════════════════════

async def save_account(owner_id: int, phone: str, session_str: str,
                       username: str = None, first_name: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO accounts (owner_id, phone, session_str, username, first_name)
               VALUES (?,?,?,?,?)
               ON CONFLICT(owner_id, phone) DO UPDATE SET
                 session_str = excluded.session_str,
                 username    = excluded.username,
                 first_name  = excluded.first_name,
                 is_active   = 1""",
            (owner_id, phone, session_str, username, first_name)
        )
        await db.commit()


async def get_accounts(owner_id: int, active_only: bool = True) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM accounts WHERE owner_id=?"
        if active_only:
            q += " AND is_active=1"
        cur = await db.execute(q, (owner_id,))
        return [dict(r) for r in await cur.fetchall()]


async def remove_account(owner_id: int, phone: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM accounts WHERE owner_id=? AND phone=?", (owner_id, phone)
        )
        await db.commit()


async def count_active(owner_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM accounts WHERE owner_id=? AND is_active=1", (owner_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else 0


# ══════════════════════════════════════════════════════════════════════
#  CAMPAIGNS
# ══════════════════════════════════════════════════════════════════════

async def create_campaign(owner_id: int, name: str, action: str,
                          target: str, extra: dict, total: int, speed: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO campaigns (owner_id, name, action, target, extra, total, speed)
               VALUES (?,?,?,?,?,?,?)""",
            (owner_id, name, action, target, json.dumps(extra), total, speed)
        )
        await db.commit()
        return cur.lastrowid


async def finish_campaign(cid: int, success: int, fail: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE campaigns SET status='completed', success=?, fail=?,
               finished_at=CURRENT_TIMESTAMP WHERE id=?""",
            (success, fail, cid)
        )
        await db.commit()


async def get_campaigns(owner_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM campaigns WHERE owner_id=? ORDER BY created_at DESC LIMIT 20",
            (owner_id,)
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_stats(owner_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT action, SUM(success) as s, SUM(fail) as f, COUNT(*) as runs
               FROM campaigns WHERE owner_id=? GROUP BY action""",
            (owner_id,)
        )
        rows = await cur.fetchall()
        return {r[0]: {"success": r[1] or 0, "fail": r[2] or 0, "runs": r[3]}
                for r in rows}


# ── Global stats (owner only) ──────────────────────────────────────────────────

async def global_stats() -> dict:
    """Total accounts, campaigns, users across the whole bot."""
    async with aiosqlite.connect(DB_PATH) as db:
        acc = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE is_active=1")).fetchone())[0]
        camps = (await (await db.execute("SELECT COUNT(*) FROM campaigns")).fetchone())[0]
        users = (await (await db.execute("SELECT COUNT(*) FROM users WHERE role NOT IN ('pending','banned')")).fetchone())[0]
        pending = (await (await db.execute("SELECT COUNT(*) FROM users WHERE role='pending'")).fetchone())[0]
        return {"accounts": acc, "campaigns": camps, "active_users": users, "pending": pending}
