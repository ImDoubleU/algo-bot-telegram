from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_error_text(details: str = "") -> str:
    base = "❌ Что-то пошло не так. Попробуйте еще раз."
    if details:
        return f"{base}\n\n{details}"
    return base


def build_error_keyboard(
    retry_callback: str | None = None,
    menu_callback: str = "back_to_main_menu",
) -> InlineKeyboardMarkup:
    buttons = []
    if retry_callback:
        buttons.append([InlineKeyboardButton("🔁 Повторить", callback_data=retry_callback)])
    buttons.append([InlineKeyboardButton("🔄 Главное меню", callback_data=menu_callback)])
    return InlineKeyboardMarkup(buttons)

