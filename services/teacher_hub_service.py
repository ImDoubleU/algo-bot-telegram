import json
import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

class TeacherHubManager:
    def __init__(self):
        self.hub_data = self.load_hub_data()

    def load_hub_data(self):
        """Загружает данные хаба из JSON файла"""
        try:
            with open('data/hub_content.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Ошибка загрузки hub_content.json: {e}")
            return {"hub_structure": {"main_sections": [], "subsections": {}}}

    def get_main_sections(self):
        """Возвращает список основных разделов"""
        return self.hub_data["hub_structure"]["main_sections"]

    def get_section_by_id(self, section_id):
        """Находит раздел по ID"""
        for section in self.hub_data["hub_structure"]["main_sections"]:
            if section["id"] == section_id:
                return section

        return self.hub_data["hub_structure"]["subsections"].get(section_id)

    def create_keyboard(self, buttons, include_back_to_hub=True, include_main_menu=False):
        keyboard = []

        for button in buttons:
            if button.get('url'):
                keyboard.append([InlineKeyboardButton(
                    button["text"], url=button["url"])])
            else:
                keyboard.append([InlineKeyboardButton(
                    button["text"], callback_data=button["callback"])])

        if include_back_to_hub and not any(btn.get("callback") == "back_to_teacher_hub" for btn in buttons):
            keyboard.append([InlineKeyboardButton(
                "◀️ Назад к хабу", callback_data="back_to_teacher_hub")])

        if include_main_menu:
            keyboard.append([InlineKeyboardButton(
                "🔄 Главное меню", callback_data="back_to_main_menu")])

        return InlineKeyboardMarkup(keyboard)


