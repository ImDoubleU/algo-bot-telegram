from access_manager import AccessManager


class AccessService:
    def __init__(self, manager: AccessManager | None = None):
        self.manager = manager or AccessManager()

    def list_access(self):
        return self.manager.get_access_list()

    def grant_access(self, user_id: int, username: str, first_name: str, last_name: str, granted_by: int):
        return self.manager.grant_access(user_id, username, first_name, last_name, granted_by)

    def revoke_access(self, user_id: int):
        return self.manager.revoke_access(user_id)

    def get_work_file(self, user_id: int) -> str | None:
        return self.manager.get_work_file(user_id)

    def set_work_file(self, user_id: int, work_file_url: str) -> bool:
        return self.manager.set_work_file(user_id, work_file_url)
