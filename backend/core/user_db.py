"""
NexusVideo Backend - 用户数据存储层
============================================================
架构位置：core/user_db.py
被 core/auth.py 和 routers/auth.py 使用。

存储方案：
  SQLite 数据库（MVP 阶段轻量方案），路径：./data/user.db
  生产环境可迁移至 PostgreSQL / MySQL，仅需修改本模块。

表结构：
  users (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      phone           TEXT UNIQUE NOT NULL,        -- 手机号（唯一标识）
      password_hash   TEXT NOT NULL,               -- bcrypt 哈希
      role            TEXT NOT NULL DEFAULT 'free', -- free | paid
      quota_daily     INTEGER NOT NULL DEFAULT 5,  -- 每日额度上限
      used_today      INTEGER NOT NULL DEFAULT 0,  -- 今日已用次数
      last_reset      TEXT NOT NULL,               -- 上次重置日期 YYYY-MM-DD
      created_at      TEXT NOT NULL,               -- 注册时间 ISO 格式
      updated_at      TEXT NOT NULL                -- 最后更新时间 ISO 格式
  )

设计要点：
  1. 每日额度自动重置：首次查询时检查 last_reset 日期，
     若已过零点则自动重置 used_today = 0
  2. 手机号唯一索引：防止重复注册
  3. 所有操作使用上下文管理器（with 语句）确保事务完整性
"""

import sqlite3
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

from core.auth import (
    hash_password,
    verify_password,
    validate_phone,
    ROLE_FREE,
    ROLE_PAID,
    QUOTA_PER_ROLE,
)


# ================================================================
# 数据库配置
# ================================================================
DB_DIR = Path("./data")
DB_PATH = DB_DIR / "user.db"


def _get_db_path() -> Path:
    """获取数据库文件路径，支持环境变量覆盖。"""
    custom = os.getenv("NEXUS_USER_DB_PATH")
    if custom:
        return Path(custom)
    return DB_PATH


# ================================================================
# 连接管理（线程安全）
# ================================================================
_local = threading.local()


def _get_connection() -> sqlite3.Connection:
    """
    获取当前线程的数据库连接（线程局部存储）。

    使用 sqlite3 的 isolation_level=None 开启自动提交模式，
    简化事务处理。检查点：每次查询前确保数据库文件已创建。
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(_get_db_path()),
            check_same_thread=False,
            timeout=30,
        )
        conn.row_factory = sqlite3.Row  # 返回 dict-like 行
        conn.execute("PRAGMA journal_mode=WAL")  # WAL 模式提升并发性能
        _local.conn = conn
    return conn


# ================================================================
# 数据库初始化
# ================================================================
CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    phone           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'free',
    quota_daily     INTEGER NOT NULL DEFAULT 5,
    used_today      INTEGER NOT NULL DEFAULT 0,
    last_reset      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""

CREATE_QUOTA_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS quota_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    phone       TEXT NOT NULL,
    consumed_at TEXT NOT NULL,
    task_id     TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""


def init_db() -> None:
    """
    初始化数据库：创建表（若不存在）。
    在应用启动时调用一次。

    幂等操作：表已存在时不会报错。
    """
    conn = _get_connection()
    conn.execute(CREATE_USERS_TABLE)
    conn.execute(CREATE_QUOTA_LOG_TABLE)
    conn.commit()
    logger.info(f"用户数据库初始化完成：{DB_PATH}")


# ================================================================
# 用户 CRUD 操作
# ================================================================
def register_user(phone: str, password: str) -> dict:
    """
    注册用户。

    参数：
        phone: 手机号（已校验）
        password: 明文密码

    返回：
        用户字典（含 id、phone、role、quota_daily 等）

    异常：
        NexusError(PHONE_EXISTS) — 手机号已注册
    """
    phone = validate_phone(phone)

    # 检查手机号是否已注册
    conn = _get_connection()
    existing = conn.execute(
        "SELECT id FROM users WHERE phone = ?", (phone,)
    ).fetchone()

    if existing:
        from exceptions import NexusError
        from core.auth import PHONE_EXISTS
        raise NexusError(
            message="该手机号已被注册，请直接登录",
            error_code=PHONE_EXISTS,
            status_code=400,
            detail={"phone": phone},
        )

    # 创建用户
    now = datetime.now().isoformat()
    today_str = date.today().isoformat()
    pwd_hash = hash_password(password)

    cursor = conn.execute(
        """
        INSERT INTO users (phone, password_hash, role, quota_daily, used_today, last_reset, created_at, updated_at)
        VALUES (?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (phone, pwd_hash, ROLE_FREE, QUOTA_PER_ROLE[ROLE_FREE], today_str, now, now),
    )

    conn.commit()
    user_id = cursor.lastrowid

    logger.info(f"新用户注册成功：user_id={user_id}, phone={phone}")

    return {
        "id": user_id,
        "phone": phone,
        "role": ROLE_FREE,
        "quota_daily": QUOTA_PER_ROLE[ROLE_FREE],
        "used_today": 0,
        "last_reset": today_str,
        "created_at": now,
    }


def login_verify(phone: str, password: str) -> tuple[dict, str]:
    """
    验证登录并返回 Token 所需信息。

    参数：
        phone: 手机号（已校验）
        password: 明文密码

    返回：
        (用户字典, "ok")

    异常：
        NexusError(USER_NOT_FOUND) — 手机号未注册
        NexusError(PASSWORD_WRONG) — 密码错误
    """
    from exceptions import NexusError
    from core.auth import USER_NOT_FOUND, PASSWORD_WRONG

    phone = validate_phone(phone)

    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE phone = ?", (phone,)
    ).fetchone()

    if row is None:
        raise NexusError(
            message="该手机号未注册，请先注册",
            error_code=USER_NOT_FOUND,
            status_code=404,
            detail={"phone": phone},
        )

    # 验证密码
    if not verify_password(password, row["password_hash"]):
        logger.warning(f"登录失败：手机号 {phone} 密码错误")
        raise NexusError(
            message="手机号或密码错误",
            error_code=PASSWORD_WRONG,
            status_code=401,
            detail={"phone": phone},
        )

    # 每日额度自动重置检查
    today_str = date.today().isoformat()
    if row["last_reset"] < today_str:
        conn.execute(
            "UPDATE users SET used_today = 0, last_reset = ?, updated_at = ? WHERE id = ?",
            (today_str, datetime.now().isoformat(), row["id"]),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()

    user_dict = _row_to_dict(row)
    logger.info(f"用户登录成功：user_id={user_dict['id']}, role={user_dict['role']}")
    return user_dict, "ok"


def get_user(user_id: int) -> Optional[dict]:
    """根据 user_id 获取用户信息（含最新额度状态）。"""
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    if row is None:
        return None

    # 自动重置检查
    today_str = date.today().isoformat()
    if row["last_reset"] < today_str:
        conn.execute(
            "UPDATE users SET used_today = 0, last_reset = ?, updated_at = ? WHERE id = ?",
            (today_str, datetime.now().isoformat(), user_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    return _row_to_dict(row)


def get_user_by_phone(phone: str) -> Optional[dict]:
    """根据手机号获取用户信息。"""
    phone = validate_phone(phone)
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE phone = ?", (phone,)
    ).fetchone()

    if row is None:
        return None

    return _row_to_dict(row)


def update_quota(user_id: int) -> bool:
    """
    消耗一次用户额度。

    返回：
        True — 成功消耗，剩余额度 >= 0
        False — 额度已用尽

    流程：
        1. 检查用户是否存在
        2. 自动重置（若跨天）
        3. 检查剩余次数
        4. 扣除 1 次
        5. 记录到 quota_log 表
    """
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    if row is None:
        logger.warning(f"尝试消耗额度失败：用户 {user_id} 不存在")
        return False

    # 自动重置
    today_str = date.today().isoformat()
    if row["last_reset"] < today_str:
        conn.execute(
            "UPDATE users SET used_today = 0, last_reset = ?, updated_at = ? WHERE id = ?",
            (today_str, datetime.now().isoformat(), user_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    # 检查额度
    remaining = row["quota_daily"] - row["used_today"]
    if remaining <= 0:
        logger.warning(f"用户 {user_id} ({row['phone']}) 今日额度已用尽")
        return False

    # 消耗额度
    now = datetime.now().isoformat()
    conn.execute(
        """
        UPDATE users
        SET used_today = used_today + 1, updated_at = ?
        WHERE id = ?
        """,
        (now, user_id),
    )

    # 记录消耗日志（便于后续审计）
    conn.execute(
        """
        INSERT INTO quota_log (user_id, phone, consumed_at)
        VALUES (?, ?, ?)
        """,
        (user_id, row["phone"], now),
    )

    conn.commit()
    logger.info(
        f"用户 {user_id} ({row['phone']}) 消耗 1 次额度，"
        f"今日已用 {row['used_today'] + 1}/{row['quota_daily']}"
    )
    return True


def check_quota(user_id: int) -> dict:
    """
    检查用户当前额度状态（不消耗）。

    返回：
        {
            "user_id": int,
            "role": "free" | "paid",
            "quota_daily": int,
            "used_today": int,
            "remaining": int,
            "last_reset": "YYYY-MM-DD",
        }
    """
    user = get_user(user_id)
    if not user:
        return {"user_id": user_id, "remaining": 0}

    remaining = user["quota_daily"] - user["used_today"]
    return {
        "user_id": user["id"],
        "role": user["role"],
        "quota_daily": user["quota_daily"],
        "used_today": user["used_today"],
        "remaining": max(0, remaining),
        "last_reset": user["last_reset"],
    }


def reset_daily_quota(user_id: int) -> None:
    """手动重置用户的每日额度（管理后台用）。"""
    conn = _get_connection()
    today_str = date.today().isoformat()
    conn.execute(
        "UPDATE users SET used_today = 0, last_reset = ?, updated_at = ? WHERE id = ?",
        (today_str, datetime.now().isoformat(), user_id),
    )
    conn.commit()
    logger.info(f"手动重置用户 {user_id} 的每日额度")


def upgrade_user_role(user_id: int, role: str) -> bool:
    """
    升级用户角色（管理后台用）。

    参数：
        user_id: 用户 ID
        role: "paid"

    返回：
        True — 成功升级
        False — 用户不存在或角色无效
    """
    if role not in (ROLE_FREE, ROLE_PAID):
        logger.error(f"无效角色：{role}")
        return False

    conn = _get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return False

    conn.execute(
        """
        UPDATE users
        SET role = ?, quota_daily = ?, updated_at = ?
        WHERE id = ?
        """,
        (role, QUOTA_PER_ROLE[role], datetime.now().isoformat(), user_id),
    )
    conn.commit()
    logger.info(f"用户 {user_id} 角色已更新为 {role}，每日额度={QUOTA_PER_ROLE[role]}")
    return True


# ================================================================
# 辅助函数
# ================================================================
def _row_to_dict(row: sqlite3.Row) -> dict:
    """将 sqlite3.Row 转换为普通字典。"""
    d = dict(row)
    # 确保 quota_daily 和 used_today 为 int
    d["quota_daily"] = int(d.get("quota_daily", 0))
    d["used_today"] = int(d.get("used_today", 0))
    return d


# ================================================================
# 全局初始化钩子
# ================================================================
# 模块导入时自动初始化数据库（惰性初始化）
_db_initialized = False


def ensure_db_ready() -> None:
    """确保数据库已初始化（在 lifespan 中调用）。"""
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True