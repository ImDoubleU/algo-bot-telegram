import os
import sqlite3
import shutil
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta
import zipfile
import pandas as pd
from contextlib import closing
from config import BACKUP_CONFIG

os.makedirs('logs', exist_ok=True)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        TimedRotatingFileHandler(
            'logs/bot_activity.log',
            when='D',
            interval=1,
            backupCount=20000,
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class BackupManager:
    def __init__(self):
        self.db_path = BACKUP_CONFIG['db_path']
        self.backup_dir = BACKUP_CONFIG['backup_dir']
        self.export_dir = BACKUP_CONFIG['export_dir']
        self.keep_months = BACKUP_CONFIG['keep_months']
        self.keep_backups = BACKUP_CONFIG['keep_backups']
        self.ensure_directories()

    def ensure_directories(self):
        """Создает необходимые директории"""
        os.makedirs(self.backup_dir, exist_ok=True)
        os.makedirs(self.export_dir, exist_ok=True)
        os.makedirs('logs', exist_ok=True)
        logger.info("Директории проверены/созданы")

    def create_backup(self, include_excel=True):
        """Создает резервную копию базы данных (полную копию БД)"""
        try:
            logger.info("Начало создания бэкапа...")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Создаем временную папку для бэкапа
            temp_dir = os.path.join(self.backup_dir, f".temp_backup_{timestamp}")
            os.makedirs(temp_dir, exist_ok=True)
            logger.info(f"Создана временная папка: {temp_dir}")

            # 1. Создаем согласованный SQLite-снимок, включая данные из WAL.
            db_backup_path = os.path.join(
                temp_dir, f"bot_analytics_{timestamp}.db")
            with closing(sqlite3.connect(self.db_path, timeout=30)) as source_db:
                with closing(sqlite3.connect(db_backup_path)) as backup_db:
                    source_db.backup(backup_db)
            logger.info(f"Согласованный снимок базы создан: {db_backup_path}")

            # 2. Создаем Excel отчет за текущий месяц если нужно
            excel_path = None
            if include_excel:
                excel_path = self.export_current_month_to_excel(
                    timestamp, temp_dir)
                if excel_path:
                    logger.info(
                        f"Excel отчет за текущий месяц создан: {excel_path}")
                else:
                    logger.warning("Не удалось создать Excel отчет")

            # 3. Создаем ZIP архив
            zip_filename = f"bot_backup_{timestamp}.zip"
            zip_path = os.path.join(self.backup_dir, zip_filename)

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Добавляем базу данных
                zipf.write(db_backup_path, os.path.basename(db_backup_path))
                logger.info(
                    f"Добавлена БД в архив: {os.path.basename(db_backup_path)}")

                # Добавляем Excel файлы если есть
                if excel_path and os.path.exists(excel_path):
                    zipf.write(excel_path, os.path.basename(excel_path))
                    logger.info(
                        f"Добавлен Excel в архив: {os.path.basename(excel_path)}")

            # Очищаем временную папку
            shutil.rmtree(temp_dir)
            logger.info(f"Временная папка удалена: {temp_dir}")

            logger.info(f"✅ Резервная копия создана: {zip_path}")
            return zip_path

        except Exception as e:
            logger.error(f"❌ Ошибка создания резервной копии: {e}")
            # Пытаемся очистить временную папку
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            return None

    def get_backup_info(self):
        """Возвращает информацию о резервных копиях"""
        try:
            backups = []
            if not os.path.exists(self.backup_dir):
                logger.warning(
                    f"Директория бэкапов не существует: {self.backup_dir}")
                return backups

            for filename in os.listdir(self.backup_dir):
                if filename.endswith('.zip'):
                    file_path = os.path.join(self.backup_dir, filename)
                    file_size = os.path.getsize(
                        file_path) / (1024 * 1024)  # в МБ
                    file_time = datetime.fromtimestamp(
                        os.path.getctime(file_path))

                    # Проверяем содержимое архива
                    try:
                        with zipfile.ZipFile(file_path, 'r') as zipf:
                            file_list = zipf.namelist()

                        has_excel = any(fname.endswith('.xlsx')
                                        for fname in file_list)

                        backups.append({
                            'filename': filename,
                            'size_mb': round(file_size, 2),
                            'created': file_time.strftime('%d.%m.%Y %H:%M'),
                            'path': file_path,
                            'has_excel': has_excel,
                            'files': file_list
                        })
                    except Exception as e:
                        logger.error(f"Ошибка чтения архива {filename}: {e}")
                        # Добавляем бэкап даже если не удалось прочитать содержимое
                        backups.append({
                            'filename': filename,
                            'size_mb': round(file_size, 2),
                            'created': file_time.strftime('%d.%m.%Y %H:%M'),
                            'path': file_path,
                            'has_excel': False,
                            'files': []
                        })

            # Сортируем по дате создания (новые сначала)
            backups.sort(key=lambda x: x['created'], reverse=True)
            logger.info(f"Найдено {len(backups)} бэкапов")
            return backups

        except Exception as e:
            logger.error(f"Ошибка получения информации о бэкапах: {e}")
            return []

    def cleanup_old_backups(self):
        """Удаляет старые резервные копии, оставляя только указанное количество"""
        try:
            backups = self.get_backup_info()

            if len(backups) > self.keep_backups:
                backups_to_delete = backups[self.keep_backups:]

                for backup in backups_to_delete:
                    try:
                        os.remove(backup['path'])
                        logger.info(
                            f"Удалена старая резервная копия: {backup['filename']}")
                    except Exception as e:
                        logger.error(
                            f"Ошибка удаления бэкапа {backup['filename']}: {e}")

                return len(backups_to_delete)
            else:
                logger.info("Нет старых резервных копий для удаления")
                return 0

        except Exception as e:
            logger.error(f"Ошибка удаления старых бэкапов: {e}")
            return None

    def _create_deletion_report(self, cutoff_date):
        """Создает отчет по удаляемым данным"""
        try:
            conn = sqlite3.connect(self.db_path)

            # Получаем данные которые будут удалены
            df = pd.read_sql_query(
                'SELECT * FROM user_actions WHERE timestamp < ? ORDER BY timestamp DESC',
                conn,
                params=(cutoff_date,)
            )

            if not df.empty:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                excel_filename = f"deleted_data_{timestamp}.xlsx"
                excel_path = os.path.join(self.export_dir, excel_filename)

                with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                    df.to_excel(
                        writer, sheet_name='Удаленные данные', index=False)

                    # Сводка по удаляемым данным
                    stats_data = {
                        'Метрика': ['Всего удалено записей', 'Период удаленных данных', 'Уникальных пользователей'],
                        'Значение': [len(df), f"до {cutoff_date}", df['user_id'].nunique()]
                    }
                    pd.DataFrame(stats_data).to_excel(
                        writer, sheet_name='Сводка удаления', index=False)

                logger.info(f"Создан отчет по удаляемым данным: {excel_path}")

            conn.close()

        except Exception as e:
            logger.error(f"Ошибка создания отчета по удалению: {e}")

    def get_storage_info(self):
        """Возвращает информацию о занимаемом месте"""
        try:
            info = {}

            # Размер основной БД
            if os.path.exists(self.db_path):
                info['db_size_mb'] = round(
                    os.path.getsize(self.db_path) / (1024 * 1024), 2)
            else:
                info['db_size_mb'] = 0

            # Информация о бэкапах
            backups = self.get_backup_info()
            info['backups_count'] = len(backups)
            info['backups_total_size_mb'] = round(
                sum(b['size_mb'] for b in backups), 2)

            # Информация об экспортах
            exports = []
            if os.path.exists(self.export_dir):
                for filename in os.listdir(self.export_dir):
                    if filename.endswith('.xlsx'):
                        file_path = os.path.join(self.export_dir, filename)
                        file_size = os.path.getsize(file_path) / (1024 * 1024)
                        exports.append({
                            'filename': filename,
                            'size_mb': round(file_size, 2)
                        })

            info['exports_count'] = len(exports)
            info['exports_total_size_mb'] = round(
                sum(e['size_mb'] for e in exports), 2)

            # Общий размер
            info['total_size_mb'] = round(
                info['db_size_mb'] + info['backups_total_size_mb'] +
                info['exports_total_size_mb'],
                2
            )

            return info

        except Exception as e:
            logger.error(f"Ошибка получения информации о хранилище: {e}")
            return {}

    def export_current_month_to_excel(self, timestamp=None, export_dir=None):
        """Экспортирует в Excel только данные за текущий месяц"""
        try:
            logger.info("Начало экспорта данных за текущий месяц...")

            if timestamp is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            if export_dir is None:
                export_dir = self.export_dir

            conn = sqlite3.connect(self.db_path)
            logger.info("Подключение к БД установлено")

            # Определяем период текущего месяца
            current_month_start = datetime.now().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
            next_month_start = (current_month_start +
                                timedelta(days=32)).replace(day=1)

            current_month_start_str = current_month_start.strftime(
                '%Y-%m-%d %H:%M:%S')
            next_month_start_str = next_month_start.strftime(
                '%Y-%m-%d %H:%M:%S')

            logger.info(
                f"Экспорт данных с {current_month_start_str} по {next_month_start_str}")

            # Получаем данные только за текущий месяц
            query = """
            SELECT * FROM user_actions 
            WHERE timestamp >= ? AND timestamp < ?
            ORDER BY timestamp DESC
            """

            df = pd.read_sql_query(query, conn, params=(
                current_month_start_str, next_month_start_str))
            logger.info(f"Прочитано {len(df)} записей за текущий месяц")

            if df.empty:
                logger.warning("Нет данных за текущий месяц для экспорта")
                conn.close()
                return None

            # Создаем имя файла с указанием месяца
            month_name = current_month_start.strftime("%Y-%m")
            excel_filename = f"user_analytics_{month_name}_{timestamp}.xlsx"
            excel_path = os.path.join(export_dir, excel_filename)
            logger.info(f"Создание Excel файла: {excel_path}")

            # Создаем Excel файл с несколькими листами
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                logger.info("Создание листа 'Все действия'...")
                # 1. Основные данные
                df.to_excel(writer, sheet_name='Все действия', index=False)

                logger.info("Создание листа 'Сводка'...")
                # 2. Сводная статистика
                stats_df = self._create_summary_stats(df, f"за {month_name}")
                stats_df.to_excel(writer, sheet_name='Сводка', index=False)

                logger.info("Создание листа 'Пользователи'...")
                # 3. Статистика по пользователям
                users_df = self._create_users_stats(df)
                if not users_df.empty:
                    users_df.to_excel(
                        writer, sheet_name='Пользователи', index=False)

                logger.info("Создание листа 'Курсы'...")
                # 4. Статистика по курсам
                courses_df = self._create_courses_stats(df)
                if not courses_df.empty:
                    courses_df.to_excel(
                        writer, sheet_name='Курсы', index=False)

                logger.info("Создание листа 'Действия'...")
                # 5. Статистика по действиям
                actions_df = self._create_actions_stats(df)
                if not actions_df.empty:
                    actions_df.to_excel(
                        writer, sheet_name='Действия', index=False)

                logger.info("Создание листа 'Ежедневная активность'...")
                # 6. Ежедневная активность
                daily_df = self._create_daily_stats(df)
                if not daily_df.empty:
                    daily_df.to_excel(
                        writer, sheet_name='Ежедневная активность', index=False)

            conn.close()
            logger.info(
                f"✅ Данные за {month_name} экспортированы в Excel: {excel_path}")
            return excel_path

        except Exception as e:
            logger.error(f"❌ Ошибка экспорта данных за текущий месяц: {e}")
            import traceback
            logger.error(f"Трассировка ошибки: {traceback.format_exc()}")
            return None

    def create_standalone_excel_report(self, period_days=30):
        """Создает отдельный Excel отчет за указанный период"""
        try:
            logger.info(
                f"Создание отдельного Excel отчета за последние {period_days} дней...")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            conn = sqlite3.connect(self.db_path)

            # Определяем период
            period_start = datetime.now() - timedelta(days=period_days)
            period_start_str = period_start.strftime('%Y-%m-%d %H:%M:%S')

            logger.info(f"Экспорт данных с {period_start_str}")

            # Получаем данные за указанный период
            query = """
            SELECT * FROM user_actions 
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            """

            df = pd.read_sql_query(query, conn, params=(period_start_str,))
            logger.info(
                f"Прочитано {len(df)} записей за последние {period_days} дней")

            if df.empty:
                logger.warning("Нет данных для экспорта")
                conn.close()
                return None

            # Создаем имя файла
            excel_filename = f"user_analytics_last_{period_days}days_{timestamp}.xlsx"
            excel_path = os.path.join(self.export_dir, excel_filename)

            # Создаем Excel файл с несколькими листами
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                # Основные данные
                df.to_excel(writer, sheet_name='Все действия', index=False)

                # Сводная статистика
                stats_df = self._create_summary_stats(
                    df, f"за последние {period_days} дней")
                stats_df.to_excel(writer, sheet_name='Сводка', index=False)

                # Остальные листы...
                users_df = self._create_users_stats(df)
                if not users_df.empty:
                    users_df.to_excel(
                        writer, sheet_name='Пользователи', index=False)

                courses_df = self._create_courses_stats(df)
                if not courses_df.empty:
                    courses_df.to_excel(
                        writer, sheet_name='Курсы', index=False)

                actions_df = self._create_actions_stats(df)
                if not actions_df.empty:
                    actions_df.to_excel(
                        writer, sheet_name='Действия', index=False)

                daily_df = self._create_daily_stats(df)
                if not daily_df.empty:
                    daily_df.to_excel(
                        writer, sheet_name='Ежедневная активность', index=False)

            conn.close()
            logger.info(f"✅ Отдельный Excel отчет создан: {excel_path}")
            return excel_path

        except Exception as e:
            logger.error(f"❌ Ошибка создания отдельного Excel отчета: {e}")
            return None

    def _create_summary_stats(self, df, period_text=""):
        """Создает сводную статистику с указанием периода"""
        try:
            if df.empty:
                return pd.DataFrame()

            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df['date'] = df['timestamp'].dt.date

            # Самый популярный курс
            course_counts = df[df['course_name'] !=
                               '']['course_name'].value_counts()
            most_popular_course = course_counts.index[0] if not course_counts.empty else 'Нет данных'

            # Самый активный день
            date_counts = df['date'].value_counts()
            most_active_day = date_counts.index[0].strftime(
                '%d.%m.%Y') if not date_counts.empty else 'Нет данных'

            period_info = f" {period_text}" if period_text else ""

            stats_data = {
                'Метрика': [
                    f'Период анализа{period_info}',
                    'Начало периода',
                    'Конец периода',
                    'Всего записей',
                    'Уникальных пользователей',
                    'Уникальных курсов',
                    'Уникальных действий',
                    'Среднее действий на пользователя',
                    'Самый активный день',
                    'Самый популярный курс'
                ],
                'Значение': [
                    f"{(df['timestamp'].max() - df['timestamp'].min()).days} дней",
                    df['timestamp'].min().strftime('%d.%m.%Y %H:%M'),
                    df['timestamp'].max().strftime('%d.%m.%Y %H:%M'),
                    len(df),
                    df['user_id'].nunique(),
                    df[df['course_name'] != '']['course_name'].nunique(),
                    df['action_type'].nunique(),
                    round(len(df) / df['user_id'].nunique(),
                          1) if df['user_id'].nunique() > 0 else 0,
                    most_active_day,
                    most_popular_course
                ]
            }

            return pd.DataFrame(stats_data)
        except Exception as e:
            logger.error(f"Ошибка создания сводной статистики: {e}")
            return pd.DataFrame()

    def _create_users_stats(self, df):
        """Статистика по пользователям"""
        try:
            if df.empty:
                return pd.DataFrame()

            user_stats = df.groupby(['user_id', 'username', 'first_name', 'last_name']).agg({
                'timestamp': ['count', 'min', 'max'],
                'course_name': 'nunique',
                'action_type': 'nunique'
            }).reset_index()

            user_stats.columns = ['ID', 'Username', 'Имя', 'Фамилия', 'Всего действий',
                                  'Первое действие', 'Последнее действие', 'Курсов', 'Типов действий']

            # Сортируем по активности
            user_stats = user_stats.sort_values(
                'Всего действий', ascending=False)

            return user_stats
        except Exception as e:
            logger.error(f"Ошибка создания статистики пользователей: {e}")
            return pd.DataFrame()

    def _create_courses_stats(self, df):
        """Статистика по курсам"""
        try:
            if df.empty:
                return pd.DataFrame()

            # Фильтруем пустые названия курсов
            course_df = df[df['course_name'] != '']
            if course_df.empty:
                return pd.DataFrame()

            course_stats = course_df.groupby('course_name').agg({
                'user_id': 'nunique',
                'timestamp': 'count'
            }).reset_index()

            course_stats.columns = ['Название курса',
                                    'Уникальных пользователей', 'Всего действий']
            course_stats = course_stats.sort_values(
                'Всего действий', ascending=False)

            return course_stats
        except Exception as e:
            logger.error(f"Ошибка создания статистики курсов: {e}")
            return pd.DataFrame()

    def _create_actions_stats(self, df):
        """Статистика по действиям"""
        try:
            if df.empty:
                return pd.DataFrame()

            # Русские названия действий
            action_translations = {
                "START": "Начало работы",
                "MESSAGE": "Сообщение",
                "COURSE_SELECTED": "Выбор курса",
                "LESSON_SELECTED": "Выбор урока",
                "DATE_SET": "Установка даты",
                "LESSON_VIEWED": "Просмотр урока",
                "NAVIGATION": "Навигация",
                "СТАРТ": "Начало работы",
                "СООБЩЕНИЕ": "Сообщение",
                "ВЫБОР_КУРСА": "Выбор курса",
                "ВЫБОР_УРОКА": "Выбор урока",
                "УСТАНОВКА_ДАТЫ": "Установка даты",
                "ПРОСМОТР_УРОКА": "Просмотр урока",
                "НАВИГАЦИЯ": "Навигация"
            }

            action_stats = df['action_type'].value_counts().reset_index()
            action_stats.columns = ['Тип действия', 'Количество']

            # Переводим английские названия
            action_stats['Тип действия'] = action_stats['Тип действия'].apply(
                lambda x: action_translations.get(x, x)
            )

            return action_stats
        except Exception as e:
            logger.error(f"Ошибка создания статистики действий: {e}")
            return pd.DataFrame()

    def _create_daily_stats(self, df):
        """Ежедневная статистика активности"""
        try:
            if df.empty:
                return pd.DataFrame()

            df['date'] = pd.to_datetime(df['timestamp']).dt.date
            daily_stats = df.groupby('date').agg({
                'user_id': 'nunique',
                'timestamp': 'count',
                'course_name': 'nunique'
            }).reset_index()

            daily_stats.columns = [
                'Дата', 'Уникальных пользователей', 'Всего действий', 'Активных курсов']
            daily_stats = daily_stats.sort_values('Дата', ascending=False)

            return daily_stats
        except Exception as e:
            logger.error(f"Ошибка создания ежедневной статистики: {e}")
            return pd.DataFrame()

    def cleanup_old_data(self):
        """Очищает старые данные и создает полный отчет"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Определяем дату, до которой удаляем данные
            cutoff_date = datetime.now() - timedelta(days=30 * self.keep_months)
            cutoff_date_str = cutoff_date.strftime('%Y-%m-%d %H:%M:%S')

            # Получаем количество записей для удаления
            cursor.execute(
                "SELECT COUNT(*) FROM user_actions WHERE timestamp < ?",
                (cutoff_date_str,)
            )
            records_to_delete = cursor.fetchone()[0]

            if records_to_delete > 0:
                # 1. Создаем полную резервную копию с Excel
                backup_path = self.create_backup(include_excel=True)

                # 2. Создаем отдельный Excel отчет для удаляемых данных
                self._create_deletion_report(cutoff_date_str)

                # 3. Удаляем старые данные
                cursor.execute(
                    "DELETE FROM user_actions WHERE timestamp < ?",
                    (cutoff_date_str,)
                )

                # 4. Выполняем VACUUM для оптимизации базы данных
                conn.execute("VACUUM")

                conn.commit()

                logger.info(
                    f"Удалено {records_to_delete} записей старше {cutoff_date_str}")
                return records_to_delete
            else:
                logger.info("Нет данных для удаления")
                return 0

        except Exception as e:
            logger.error(f"Ошибка очистки данных: {e}")
            return None
        finally:
            if 'conn' in locals():
                conn.close()

    def _create_deletion_report(self, cutoff_date):
        """Создает отчет по удаляемым данным"""
        try:
            conn = sqlite3.connect(self.db_path)

            # Получаем данные которые будут удалены
            df = pd.read_sql_query(
                'SELECT * FROM user_actions WHERE timestamp < ? ORDER BY timestamp DESC',
                conn,
                params=(cutoff_date,)
            )

            if not df.empty:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                excel_filename = f"deleted_data_{timestamp}.xlsx"
                excel_path = os.path.join(self.export_dir, excel_filename)

                with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                    df.to_excel(
                        writer, sheet_name='Удаленные данные', index=False)

                    # Сводка по удаляемым данным
                    stats_data = {
                        'Метрика': ['Всего удалено записей', 'Период удаленных данных', 'Уникальных пользователей'],
                        'Значение': [len(df), f"до {cutoff_date}", df['user_id'].nunique()]
                    }
                    pd.DataFrame(stats_data).to_excel(
                        writer, sheet_name='Сводка удаления', index=False)

                logger.info(f"Создан отчет по удаляемым данным: {excel_path}")

            conn.close()

        except Exception as e:
            logger.error(f"Ошибка создания отчета по удалению: {e}")

    def cleanup_old_backups(self):
        """Удаляет старые резервные копии, оставляя только указанное количество"""
        try:
            backups = self.get_backup_info()

            if len(backups) > self.keep_backups:
                backups_to_delete = backups[self.keep_backups:]

                for backup in backups_to_delete:
                    os.remove(backup['path'])
                    logger.info(
                        f"Удалена старая резервная копия: {backup['filename']}")

                return len(backups_to_delete)
            else:
                logger.info("Нет старых резервных копий для удаления")
                return 0

        except Exception as e:
            logger.error(f"Ошибка удаления старых бэкапов: {e}")
            return None

    def get_storage_info(self):
        """Возвращает информацию о занимаемом месте"""
        try:
            info = {}

            # Размер основной БД
            if os.path.exists(self.db_path):
                info['db_size_mb'] = round(
                    os.path.getsize(self.db_path) / (1024 * 1024), 2)
            else:
                info['db_size_mb'] = 0

            # Информация о бэкапах
            backups = self.get_backup_info()
            info['backups_count'] = len(backups)
            info['backups_total_size_mb'] = round(
                sum(b['size_mb'] for b in backups), 2)

            # Информация об экспортах
            exports = []
            if os.path.exists(self.export_dir):
                for filename in os.listdir(self.export_dir):
                    if filename.endswith('.xlsx'):
                        file_path = os.path.join(self.export_dir, filename)
                        file_size = os.path.getsize(file_path) / (1024 * 1024)
                        exports.append({
                            'filename': filename,
                            'size_mb': round(file_size, 2)
                        })

            info['exports_count'] = len(exports)
            info['exports_total_size_mb'] = round(
                sum(e['size_mb'] for e in exports), 2)

            # Общий размер
            info['total_size_mb'] = round(
                info['db_size_mb'] + info['backups_total_size_mb'] +
                info['exports_total_size_mb'],
                2
            )

            return info

        except Exception as e:
            logger.error(f"Ошибка получения информации о хранилище: {e}")
            return {}


def monthly_maintenance():
    """Выполняет ежемесячное обслуживание"""
    manager = BackupManager()

    logger.info("=== НАЧАЛО ЕЖЕМЕСЯЧНОГО ОБСЛУЖИВАНИЯ ===")

    # 1. Создаем резервную копию с Excel отчетом за текущий месяц
    backup_path = manager.create_backup(include_excel=True)

    # 2. Создаем отдельный Excel отчет за последние 30 дней
    excel_path = manager.create_standalone_excel_report(period_days=30)

    # 3. Очищаем старые данные (старше keep_months)
    deleted_count = manager.cleanup_old_data()

    # 4. Очищаем старые резервные копии
    old_backups_deleted = manager.cleanup_old_backups()

    logger.info("=== ЗАВЕРШЕНИЕ ЕЖЕМЕСЯЧНОГО ОБСЛУЖИВАНИЯ ===")

    return {
        'backup_created': backup_path is not None,
        'excel_created': excel_path is not None,  # Важно: используем 'excel_created'
        'records_deleted': deleted_count or 0,
        'old_backups_deleted': old_backups_deleted or 0
    }
