import json

from app.settings import settings


class HubService:
    def __init__(self, hub_path=None):
        self.hub_path = hub_path or settings.hub_content_path

    def load(self) -> dict:
        with open(self.hub_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_main_sections(self) -> list[dict]:
        return self.load().get("hub_structure", {}).get("main_sections", [])

    def get_section(self, section_id: str) -> dict | None:
        data = self.load().get("hub_structure", {})
        for section in data.get("main_sections", []):
            if section.get("id") == section_id:
                return section
        return data.get("subsections", {}).get(section_id)
