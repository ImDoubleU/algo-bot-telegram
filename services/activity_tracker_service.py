import logging
import sqlite3
from datetime import datetime
from typing import Any

try:
    from telegram import Update
except ImportError:  # pragma: no cover - compatible with web-only runtime
    Update = Any

logger = logging.getLogger(__name__)

class UserActivityTracker:
    def __init__(self, db_path='logs/bot_analytics.db'):
        self.db_path = db_path
        self.init_database()
        self.init_user_states_table()
        self.init_lesson_offsets_table()
        self.init_course_lessons_table()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def init_course_lessons_table(self):
        """Инициализация таблицы для сохранения выбранных уроков по курсам"""
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_course_lessons (
                user_id INTEGER,
                course_name TEXT,
                lesson_index INTEGER DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, course_name)
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("Таблица состояний уроков по курсам инициализирована")

    def save_course_lesson_state(self, user_id: int, course_name: str, lesson_index: int):
        """Сохраняет выбранный урок для конкретного курса"""
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute('''
                INSERT OR REPLACE INTO user_course_lessons 
                (user_id, course_name, lesson_index, updated_at) 
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (user_id, course_name, lesson_index))

            conn.commit()
            conn.close()
            logger.info(
                f"Сохранен урок {lesson_index + 1} для пользователя {user_id}, курс {course_name}")

        except Exception as e:
            logger.error(f"Ошибка сохранения состояния урока: {e}")

    def get_course_lesson_state(self, user_id: int, course_name: str):
        """Получает сохраненный урок для конкретного курса"""
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT lesson_index 
                FROM user_course_lessons 
                WHERE user_id = ? AND course_name = ?
            ''', (user_id, course_name))

            result = cursor.fetchone()
            conn.close()

            if result:
                lesson_index = result[0]
                logger.info(
                    f"Загружен урок {lesson_index + 1} для пользователя {user_id}, курс {course_name}")
                return lesson_index
            else:
                return 0

        except Exception as e:
            logger.error(f"Ошибка получения состояния урока: {e}")
            return 0

    def clear_course_lesson_state(self, user_id: int, course_name: str):
        """Очищает сохраненный урок для курса"""
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute('''
                DELETE FROM user_course_lessons 
                WHERE user_id = ? AND course_name = ?
            ''', (user_id, course_name))

            conn.commit()
            conn.close()
            logger.info(
                f"Очищен урок для пользователя {user_id}, курс {course_name}")

        except Exception as e:
            logger.error(f"Ошибка очистки состояния урока: {e}")

    def init_database(self):
        """Инициализация базы данных"""
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                action_type TEXT,
                action_details TEXT,
                timestamp DATETIME,
                course_name TEXT,
                lesson_number INTEGER,
                lesson_date TEXT
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("База данных аналитики инициализирована")

    def init_user_states_table(self):
        """Инициализация таблицы состояний пользователей"""
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_states (
                user_id INTEGER PRIMARY KEY,
                last_course TEXT,
                last_lesson_index INTEGER,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("Таблица состояний пользователей инициализирована")

    def save_user_state(self, user_id: int, course_name: str, lesson_index: int):
        """Сохраняет состояние пользователя"""
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute('''
                INSERT OR REPLACE INTO user_states 
                (user_id, last_course, last_lesson_index, updated_at) 
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (user_id, course_name, lesson_index))

            conn.commit()
            conn.close()
            logger.info(
                f"Сохранено состояние пользователя {user_id}: курс {course_name}, урок {lesson_index + 1}")

        except Exception as e:
            logger.error(f"Ошибка сохранения состояния пользователя: {e}")

    def get_user_state(self, user_id: int):
        """Получает состояние пользователя"""
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT last_course, last_lesson_index 
                FROM user_states 
                WHERE user_id = ?
            ''', (user_id,))

            result = cursor.fetchone()
            conn.close()

            if result:
                course_name, lesson_index = result
                logger.info(
                    f"Загружено состояние пользователя {user_id}: курс {course_name}, урок {lesson_index + 1}")
                return course_name, lesson_index
            else:
                return None, None

        except Exception as e:
            logger.error(f"Ошибка получения состояния пользователя: {e}")
            return None, None

    def clear_user_state(self, user_id: int):
        """Очищает состояние пользователя"""
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                'DELETE FROM user_states WHERE user_id = ?', (user_id,))

            conn.commit()
            conn.close()
            logger.info(f"Очищено состояние пользователя {user_id}")

        except Exception as e:
            logger.error(f"Ошибка очистки состояния пользователя: {e}")

    def log_action(self, update: Update, action_type: str, action_details: str = "",
                   course_name: str = "", lesson_number: int = None, lesson_date: str = ""):
        """Логирование действия пользователя"""
        try:
            user = update.effective_user
            self.log_action_for_actor(
                actor_id=user.id,
                actor_username=user.username or 'unknown',
                actor_display_name=" ".join(
                    part for part in [(user.first_name or "").strip(), (user.last_name or "").strip()] if part
                ),
                action_type=action_type,
                action_details=action_details,
                course_name=course_name,
                lesson_number=lesson_number,
                lesson_date=lesson_date,
            )
        except Exception as e:
            logger.error(f"Ошибка логирования: {e}")

    def log_action_for_actor(
        self,
        actor_id: int,
        actor_username: str,
        actor_display_name: str,
        action_type: str,
        action_details: str = "",
        course_name: str = "",
        lesson_number: int = None,
        lesson_date: str = "",
    ):
        """Логирование действия без Telegram Update."""
        try:
            first_name = ""
            last_name = ""
            display_name = (actor_display_name or "").strip()
            if display_name:
                parts = display_name.split(maxsplit=1)
                first_name = parts[0]
                last_name = parts[1] if len(parts) > 1 else ""

            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO user_actions
                (user_id, username, first_name, last_name, action_type, action_details,
                 timestamp, course_name, lesson_number, lesson_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                actor_id,
                actor_username or 'unknown',
                first_name,
                last_name,
                action_type,
                action_details,
                datetime.now(),
                course_name,
                lesson_number,
                lesson_date
            ))

            conn.commit()
            conn.close()

            log_message = (f"User {actor_id} (@{actor_username}) - {action_type}: "
                           f"{action_details} | Course: {course_name} | "
                           f"Lesson: {lesson_number} | Date: {lesson_date}")
            logger.info(log_message)

            with open('logs/user_analytics.csv', 'a', encoding='utf-8') as f:
                csv_line = (
                    f'"{datetime.now()}",'
                    f'"{actor_id}",'
                    f'"{actor_username or "unknown"}",'
                    f'"{first_name}",'
                    f'"{last_name}",'
                    f'"{action_type}",'
                    f'"{action_details}",'
                    f'"{course_name}",'
                    f'"{lesson_number if lesson_number is not None else ""}",'
                    f'"{lesson_date}"\n'
                )
                f.write(csv_line)
        except Exception as e:
            logger.error(f"Ошибка логирования: {e}")

    def init_lesson_offsets_table(self):
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_lesson_offsets (
                user_id INTEGER,
                course_name TEXT,
                offset_value INTEGER DEFAULT 0,
                repetition_count INTEGER DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, course_name)
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("Таблица смещений уроков инициализирована")

    def get_user_offset(self, user_id: int, course_name: str):
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT offset_value, repetition_count 
                FROM user_lesson_offsets 
                WHERE user_id = ? AND course_name = ?
            ''', (user_id, course_name))

            result = cursor.fetchone()
            conn.close()

            if result:
                return result[0], result[1]
            return 0, 0

        except Exception as e:
            logger.error(f"Ошибка получения смещения: {e}")
            return 0, 0

    def set_user_offset(self, user_id: int, course_name: str, offset: int, repetition_count: int = None):
        try:
            conn = self._connect()
            cursor = conn.cursor()

            if repetition_count is not None:
                cursor.execute('''
                    INSERT OR REPLACE INTO user_lesson_offsets 
                    (user_id, course_name, offset_value, repetition_count, updated_at) 
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (user_id, course_name, offset, repetition_count))
            else:
                cursor.execute('''
                    INSERT OR REPLACE INTO user_lesson_offsets 
                    (user_id, course_name, offset_value, updated_at) 
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ''', (user_id, course_name, offset))

            conn.commit()
            conn.close()
            logger.info(
                f"Установлено смещение {offset} для пользователя {user_id}, курс {course_name}")

        except Exception as e:
            logger.error(f"Ошибка установки смещения: {e}")

    def get_daily_stats(self, date=None):
        """Получение дневной статистики"""
        conn = self._connect()
        cursor = conn.cursor()

        if date:
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_actions,
                    COUNT(DISTINCT user_id) as unique_users,
                    COUNT(DISTINCT course_name) as active_courses
                FROM user_actions 
                WHERE DATE(timestamp) = ?
            ''', (date,))
        else:
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_actions,
                    COUNT(DISTINCT user_id) as unique_users,
                    COUNT(DISTINCT course_name) as active_courses
                FROM user_actions 
                WHERE DATE(timestamp) = DATE('now')
            ''')

        stats = cursor.fetchone()

        if date:
            cursor.execute('''
                SELECT action_type, COUNT(*) as count 
                FROM user_actions 
                WHERE DATE(timestamp) = ?
                GROUP BY action_type 
                ORDER BY count DESC 
                LIMIT 5
            ''', (date,))
        else:
            cursor.execute('''
                SELECT action_type, COUNT(*) as count 
                FROM user_actions 
                WHERE DATE(timestamp) = DATE('now')
                GROUP BY action_type 
                ORDER BY count DESC 
                LIMIT 5
            ''')

        top_actions = cursor.fetchall()

        conn.close()

        return {
            'total_actions': stats[0],
            'unique_users': stats[1],
            'active_courses': stats[2],
            'top_actions': top_actions
        }

    def get_user_stats(self, username=None, user_id=None, date=None):
        """Получение статистики пользователя"""
        conn = self._connect()
        cursor = conn.cursor()

        query = '''
            SELECT 
                user_id,
                username,
                first_name,
                last_name,
                COUNT(*) as total_actions,
                COUNT(DISTINCT course_name) as courses_accessed,
                MIN(timestamp) as first_seen,
                MAX(timestamp) as last_seen
            FROM user_actions 
            WHERE 1=1
        '''
        params = []

        if username and username != 'unknown':
            query += ' AND username = ?'
            params.append(username)
        elif user_id:
            query += ' AND user_id = ?'
            params.append(user_id)

        if date:
            query += ' AND DATE(timestamp) = ?'
            params.append(date)

        query += ' GROUP BY user_id'

        cursor.execute(query, params)
        user_stats = cursor.fetchone()

        if not user_stats:
            conn.close()
            return None

        course_query = '''
            SELECT course_name, COUNT(*) as lesson_views
            FROM user_actions 
            WHERE user_id = ? AND course_name != ""
        '''
        course_params = [user_stats[0]]

        if date:
            course_query += ' AND DATE(timestamp) = ?'
            course_params.append(date)

        course_query += ' GROUP BY course_name ORDER BY lesson_views DESC'

        cursor.execute(course_query, course_params)
        course_stats = cursor.fetchall()

        action_query = '''
            SELECT action_type, action_details, timestamp, course_name
            FROM user_actions 
            WHERE user_id = ?
        '''
        action_params = [user_stats[0]]

        if date:
            action_query += ' AND DATE(timestamp) = ?'
            action_params.append(date)

        action_query += ' ORDER BY timestamp DESC LIMIT 10'

        cursor.execute(action_query, action_params)
        recent_actions = cursor.fetchall()

        conn.close()

        return {
            'user_info': {
                'user_id': user_stats[0],
                'username': user_stats[1],
                'first_name': user_stats[2],
                'last_name': user_stats[3],
                'first_seen': user_stats[6],
                'last_seen': user_stats[7]
            },
            'stats': {
                'total_actions': user_stats[4],
                'courses_accessed': user_stats[5]
            },
            'course_stats': course_stats,
            'recent_actions': recent_actions
        }

    def search_users(self, search_term):
        """Поиск пользователей по username или имени"""
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT DISTINCT user_id, username, first_name, last_name
            FROM user_actions 
            WHERE username LIKE ? OR first_name LIKE ? OR last_name LIKE ?
            ORDER BY username
            LIMIT 20
        ''', (f'%{search_term}%', f'%{search_term}%', f'%{search_term}%'))

        users = cursor.fetchall()
        conn.close()

        return users

    def get_latest_user_identity(self, user_id: int):
        """Возвращает последние известные username и полное имя пользователя."""
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT username, first_name, last_name
                FROM user_actions
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                ''',
                (user_id,)
            )
            row = cursor.fetchone()
            conn.close()

            if not row:
                return "", ""

            username = (row[0] or "").strip()
            if username.lower() == "unknown":
                username = ""
            first_name = (row[1] or "").strip()
            last_name = (row[2] or "").strip()
            full_name = " ".join(part for part in [first_name, last_name] if part)
            return username, full_name
        except Exception as e:
            logger.error(f"Ошибка получения данных пользователя {user_id}: {e}")
            return "", ""


