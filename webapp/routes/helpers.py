from pathlib import Path

from fastapi.templating import Jinja2Templates


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def ru_plural(value: int, form_one: str, form_few: str, form_many: str) -> str:
    value = abs(int(value))
    last_two = value % 100
    last_one = value % 10

    if 11 <= last_two <= 14:
        return form_many
    if last_one == 1:
        return form_one
    if 2 <= last_one <= 4:
        return form_few
    return form_many


templates.env.filters["ru_plural"] = ru_plural
