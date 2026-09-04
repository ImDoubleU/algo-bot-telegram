from datetime import datetime


class ValidationError(ValueError):
    pass


def parse_int(value: str, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} должно быть целым числом.") from exc


def parse_date(value: str, fmt: str, field_name: str) -> datetime:
    try:
        return datetime.strptime(value, fmt)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Неверный формат поля «{field_name}».") from exc


def parse_time(value: str, field_name: str) -> None:
    parse_date(value, "%H:%M", field_name)

