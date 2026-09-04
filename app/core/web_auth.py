import hashlib
import hmac
import os
import sqlite3

from core.sqlite_service import SQLiteService

from app.settings import settings


class WebAuthService:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or settings.db_path)
        self.db = SQLiteService(self.db_path)
        self._init_table()
        self._ensure_linked_teacher_column()
        self._ensure_password_plain_column()
        self.ensure_default_admin()

    def _init_table(self):
        self.db.execute_script([
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            """
            CREATE TABLE IF NOT EXISTS web_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                linked_teacher_id INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ])

    def _ensure_linked_teacher_column(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(web_users)")
        columns = {row[1] for row in cur.fetchall()}
        if "linked_teacher_id" not in columns:
            try:
                cur.execute("ALTER TABLE web_users ADD COLUMN linked_teacher_id INTEGER")
                conn.commit()
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        conn.close()

    def _ensure_password_plain_column(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(web_users)")
        columns = {row[1] for row in cur.fetchall()}
        if "password_plain" not in columns:
            try:
                cur.execute("ALTER TABLE web_users ADD COLUMN password_plain TEXT")
                conn.commit()
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        conn.close()

    def ensure_default_admin(self):
        existing = self.db.fetchone(
            "SELECT id, password_plain FROM web_users WHERE username = ?",
            (settings.admin_username,),
        )
        if existing:
            if not existing[1]:
                self.db.execute(
                    """
                    UPDATE web_users
                    SET password_plain = ?
                    WHERE username = ?
                    """,
                    (settings.admin_password, settings.admin_username),
                )
            return
        self.create_user(settings.admin_username, settings.admin_password, settings.admin_display_name, "admin")

    def create_user(
        self,
        username: str,
        password: str,
        display_name: str,
        role: str = "teacher",
        linked_teacher_id: int | None = None,
    ) -> bool:
        existing = self.get_user_by_username(username)
        if existing:
            return False
        password_hash = self._hash_password(password)
        self.db.execute(
            """
            INSERT INTO web_users (username, password_hash, password_plain, display_name, role, linked_teacher_id, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (username, password_hash, password, display_name, role, linked_teacher_id),
        )
        return True

    def get_user_by_username(self, username: str):
        return self.db.fetchone(
            """
            SELECT id, username, display_name, role, linked_teacher_id, is_active, created_at
            FROM web_users
            WHERE username = ?
            """,
            (username,),
        )

    def update_user_password(self, username: str, password: str):
        self.db.execute(
            """
            UPDATE web_users
            SET password_hash = ?, password_plain = ?
            WHERE username = ?
            """,
            (self._hash_password(password), password, username),
        )

    def delete_user(self, username: str) -> bool:
        if username == settings.admin_username:
            return False
        self.db.execute("DELETE FROM web_users WHERE username = ?", (username,))
        return True

    def update_user_profile(
        self,
        username: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        linked_teacher_id: int | None = None,
        is_active: int | None = None,
    ):
        updates = []
        params = []
        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name)
        if role is not None:
            updates.append("role = ?")
            params.append(role)
        if linked_teacher_id is not None:
            updates.append("linked_teacher_id = ?")
            params.append(linked_teacher_id)
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(is_active)
        if not updates:
            return
        params.append(username)
        self.db.execute(
            f"""
            UPDATE web_users
            SET {", ".join(updates)}
            WHERE username = ?
            """,
            tuple(params),
        )

    def authenticate(self, username: str, password: str):
        row = self.db.fetchone(
            """
            SELECT id, username, password_hash, display_name, role, linked_teacher_id, is_active
            FROM web_users
            WHERE username = ?
            """,
            (username,),
        )
        if not row or row[6] != 1:
            return None
        if not self._verify_password(password, row[2]):
            return None
        return {
            "id": row[0],
            "username": row[1],
            "display_name": row[3],
            "role": row[4],
            "linked_teacher_id": row[5],
        }

    def list_users(self):
        return self.db.fetchall(
            """
            SELECT id, username, display_name, password_plain
            FROM web_users
            ORDER BY username ASC
            """
        )

    def _get_first_teacher_id(self) -> int | None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT teacher_id FROM teacher_groups ORDER BY teacher_id ASC LIMIT 1")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None

    def _hash_password(self, password: str) -> str:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return f"{salt.hex()}:{digest.hex()}"

    def _verify_password(self, password: str, stored: str) -> bool:
        salt_hex, digest_hex = stored.split(":", 1)
        computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 100_000)
        return hmac.compare_digest(computed.hex(), digest_hex)
