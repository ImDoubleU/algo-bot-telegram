import sqlite3
from typing import Any, Iterable


class SQLiteService:
    def __init__(self, db_path: str, timeout: int = 15):
        self.db_path = db_path
        self.timeout = timeout

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=self.timeout)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(sql, tuple(params))
        conn.commit()
        conn.close()

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> Any:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(sql, tuple(params))
        result = cursor.fetchone()
        conn.close()
        return result

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[Any]:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(sql, tuple(params))
        result = cursor.fetchall()
        conn.close()
        return result

    def execute_script(self, statements: list[str]) -> None:
        conn = self._connect()
        cursor = conn.cursor()
        for statement in statements:
            cursor.execute(statement)
        conn.commit()
        conn.close()
