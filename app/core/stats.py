from services.activity_tracker_service import UserActivityTracker


class StatsService:
    def __init__(self, tracker: UserActivityTracker | None = None):
        self.tracker = tracker or UserActivityTracker()

    def get_daily_stats(self, date: str | None = None):
        return self.tracker.get_daily_stats(date=date)

    def search_users(self, search_term: str):
        return self.tracker.search_users(search_term)

    def get_user_stats(self, username: str | None = None, user_id: int | None = None, date: str | None = None):
        return self.tracker.get_user_stats(username=username, user_id=user_id, date=date)
