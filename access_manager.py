import logging
import hashlib
import secrets
import sqlite3
from core.sqlite_service import SQLiteService

logger = logging.getLogger(__name__)


class AccessManager:
    def __init__(self, db_path='logs/bot_analytics.db'):
        self.db_path = db_path
        self.db = SQLiteService(db_path)
        self.init_access_table()
        self.init_invites_table()
        self.init_work_files_table()

    def init_work_files_table(self):
        """Initialize table for storing per-user work file links."""
        self.db.execute(
            '''
            CREATE TABLE IF NOT EXISTS user_work_files (
                user_id INTEGER PRIMARY KEY,
                work_file_url TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )
        logger.info("Work files table initialized")

    def get_work_file(self, user_id: int) -> str:
        """Return user's work file URL."""
        try:
            result = self.db.fetchone(
                '''
                SELECT work_file_url FROM user_work_files
                WHERE user_id = ?
                ''',
                (user_id,),
            )
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Failed to get work file for user {user_id}: {e}")
            return None

    def set_work_file(self, user_id: int, work_file_url: str) -> bool:
        """Set or update user's work file URL."""
        try:
            self.db.execute(
                '''
                INSERT OR REPLACE INTO user_work_files
                (user_id, work_file_url, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ''',
                (user_id, work_file_url),
            )
            logger.info(f"Work file updated for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to set work file for user {user_id}: {e}")
            return False

    def delete_work_file(self, user_id: int) -> bool:
        """Delete user's work file URL."""
        try:
            self.db.execute(
                '''
                DELETE FROM user_work_files
                WHERE user_id = ?
                ''',
                (user_id,),
            )
            logger.info(f"Work file deleted for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete work file for user {user_id}: {e}")
            return False

    def init_access_table(self):
        """Initialize teacher hub access table."""
        self.db.execute_script([
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            '''
            CREATE TABLE IF NOT EXISTS teacher_hub_access (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                granted_by INTEGER,
                granted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE
            )
            ''',
        ])
        logger.info("Hub access table initialized")

    def init_invites_table(self):
        """Initialize one-time access invitations."""
        self.db.execute(
            '''
            CREATE TABLE IF NOT EXISTS access_invites (
                token_hash TEXT PRIMARY KEY,
                created_by INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                used_by INTEGER,
                used_at DATETIME
            )
            '''
        )
        logger.info("Access invites table initialized")

    @staticmethod
    def _hash_invite_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_invite(self, created_by: int) -> str:
        """Create and persist a cryptographically random one-time invite token."""
        for _ in range(3):
            token = secrets.token_urlsafe(24)
            try:
                self.db.execute(
                    '''
                    INSERT INTO access_invites (token_hash, created_by)
                    VALUES (?, ?)
                    ''',
                    (self._hash_invite_token(token), created_by),
                )
                logger.info("One-time access invite created by user %s", created_by)
                return token
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Failed to generate a unique access invite")

    def redeem_invite(
        self,
        token: str,
        user_id: int,
        username: str,
        first_name: str,
        last_name: str,
    ) -> bool:
        """Atomically consume an invite and grant access to the current Telegram user."""
        if not token:
            return False

        connection = self.db._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            token_hash = self._hash_invite_token(token)
            invite = connection.execute(
                '''
                SELECT 1 FROM access_invites
                WHERE token_hash = ? AND used_at IS NULL
                ''',
                (token_hash,),
            ).fetchone()
            if invite is None:
                connection.rollback()
                return False

            connection.execute(
                '''
                INSERT INTO teacher_hub_access
                    (user_id, username, first_name, last_name, granted_by, granted_at, is_active)
                SELECT ?, ?, ?, ?, created_by, CURRENT_TIMESTAMP, TRUE
                FROM access_invites
                WHERE token_hash = ?
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    granted_by = excluded.granted_by,
                    granted_at = CURRENT_TIMESTAMP,
                    is_active = TRUE
                ''',
                (user_id, username, first_name, last_name, token_hash),
            )
            updated = connection.execute(
                '''
                UPDATE access_invites
                SET used_by = ?, used_at = CURRENT_TIMESTAMP
                WHERE token_hash = ? AND used_at IS NULL
                ''',
                (user_id, token_hash),
            ).rowcount
            if updated != 1:
                connection.rollback()
                return False

            connection.commit()
            logger.info("Access invite redeemed by user %s", user_id)
            return True
        except Exception:
            connection.rollback()
            logger.exception("Failed to redeem access invite for user %s", user_id)
            return False
        finally:
            connection.close()

    def has_access(self, user_id: int) -> bool:
        try:
            result = self.db.fetchone(
                '''
                SELECT 1 FROM teacher_hub_access
                WHERE user_id = ? AND is_active = TRUE
                ''',
                (user_id,),
            )
            return result is not None
        except Exception as e:
            logger.error(f"Failed to check access for user {user_id}: {e}")
            return False

    def grant_access(
        self,
        user_id: int,
        username: str,
        first_name: str,
        last_name: str,
        granted_by: int,
    ) -> bool:
        """Grant access for a user."""
        try:
            self.db.execute(
                '''
                INSERT OR REPLACE INTO teacher_hub_access
                (user_id, username, first_name, last_name, granted_by, is_active)
                VALUES (?, ?, ?, ?, ?, TRUE)
                ''',
                (user_id, username, first_name, last_name, granted_by),
            )
            logger.info(f"Access granted to user {user_id} (@{username})")
            return True
        except Exception as e:
            logger.error(f"Failed to grant access for user {user_id}: {e}")
            return False

    def revoke_access(self, user_id: int) -> bool:
        """Revoke access for a user."""
        try:
            self.db.execute(
                '''
                UPDATE teacher_hub_access
                SET is_active = FALSE
                WHERE user_id = ?
                ''',
                (user_id,),
            )
            logger.info(f"Access revoked for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to revoke access for user {user_id}: {e}")
            return False

    def get_access_list(self):
        try:
            all_users = []
            db_users = self.db.fetchall(
                '''
                SELECT user_id, username, first_name, last_name, granted_at
                FROM teacher_hub_access
                WHERE is_active = TRUE
                ORDER BY granted_at DESC
                '''
            )

            for user_id, username, first_name, last_name, granted_at in db_users:
                all_users.append(
                    {
                        'user_id': user_id,
                        'username': username or 'unknown',
                        'first_name': first_name or '',
                        'last_name': last_name or '',
                        'granted_at': granted_at,
                        'source': 'database',
                    }
                )

            return all_users
        except Exception as e:
            logger.error(f"Failed to fetch access list: {e}")
            return []


access_manager = AccessManager()
