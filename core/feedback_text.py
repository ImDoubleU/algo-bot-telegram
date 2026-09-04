MAX_BOT_URL = "https://max.ru/id525601030904_3_bot"
MAX_BOT_LINK_LABEL = "Открыть бот Algo MAX"


def build_astrocoins_block(lesson_number: int, formatted_date: str) -> str:
    return (
        f"Начислены астрокоины за урок №{lesson_number:02d} от {formatted_date}\n"
        "Количество астрокоинов, а также куда их потратить, можно посмотреть в боте Max\n"
        f"🔗 {MAX_BOT_LINK_LABEL}"
    )
