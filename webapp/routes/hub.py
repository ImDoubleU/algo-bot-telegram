from fastapi import APIRouter, Depends, Query, Request

from webapp.deps import get_current_user, hub_service
from webapp.routes.helpers import templates


router = APIRouter(prefix="/hub")


def _resolve_hub_view(section_id: str) -> tuple[list[dict], dict | None]:
    hub_data = hub_service.load().get("hub_structure", {})
    main_sections = hub_data.get("main_sections", [])
    subsections = hub_data.get("subsections", {})
    main_section_ids = {item.get("id") for item in main_sections}

    def resolve_section(item_id: str) -> dict | None:
        for item in main_sections:
            if item.get("id") == item_id:
                return item
        return subsections.get(item_id)

    def find_parent_section(item_id: str) -> dict | None:
        for section in main_sections:
            for button in section.get("buttons", []):
                if button.get("callback") == item_id:
                    return section
        return None

    def decorate_button(button: dict, current_section: dict) -> dict:
        item = dict(button)
        callback = item.get("callback", "")
        href = item.get("url", "")
        is_external = bool(href)
        target_section = resolve_section(callback) if callback else None

        if not href and callback:
            if target_section:
                href = f"/hub?section={callback}"
            elif callback == "back_to_teacher_hub":
                href = "/hub"
            elif callback == "back_to_main_menu":
                href = "/"

        current_is_main = current_section.get("id") in main_section_ids
        if callback in {"back_to_teacher_hub", "back_to_main_menu"}:
            kind = "nav"
            tag = "Навигация"
            note = "Быстрый возврат"
        elif target_section and not current_is_main and callback in main_section_ids:
            kind = "nav"
            tag = "Раздел"
            note = f"Вернуться в «{target_section.get('title', '')}»"
        elif target_section:
            kind = "section"
            tag = "Подраздел" if current_is_main else "Раздел"
            note = "Открыть внутри хаба"
        elif href:
            kind = "external"
            tag = "Ссылка"
            note = "Откроется в новой вкладке"
        else:
            kind = "static"
            tag = "Недоступно"
            note = "Пока нет в веб-версии"

        item["href"] = href
        item["is_external"] = is_external
        item["kind"] = kind
        item["tag"] = tag
        item["note"] = note
        return item

    def decorate_section(section: dict) -> dict:
        item = dict(section)
        buttons = [decorate_button(button, section) for button in section.get("buttons", [])]
        parent_section = find_parent_section(section.get("id", ""))
        action_buttons = [button for button in buttons if button.get("kind") != "nav"]
        nav_buttons = [button for button in buttons if button.get("kind") == "nav"]
        item["description"] = section.get("description") or section.get("text") or ""
        item["buttons"] = buttons
        item["action_buttons"] = action_buttons
        item["nav_buttons"] = nav_buttons
        item["parent_section"] = (
            {
                "id": parent_section.get("id", ""),
                "title": parent_section.get("title", ""),
                "href": f"/hub?section={parent_section.get('id', '')}",
            }
            if parent_section
            else None
        )
        item["buttons_count"] = len(action_buttons) if action_buttons else len(buttons)
        return item

    sections = [decorate_section(section) for section in main_sections]
    selected_raw = resolve_section(section_id) if section_id else (main_sections[0] if main_sections else None)
    selected = decorate_section(selected_raw) if selected_raw else None
    return sections, selected


@router.get("")
async def hub_page(request: Request, section: str = Query(""), user=Depends(get_current_user)):
    sections, selected = _resolve_hub_view(section)
    return templates.TemplateResponse(
        "hub.html",
        {"request": request, "user": user, "sections": sections, "selected": selected},
    )
