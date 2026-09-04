import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from access_manager import AccessManager


class AccessInviteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "access.db"
        self.manager = AccessManager(str(self.db_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_invite_grants_access_once_and_stores_real_profile(self):
        token = self.manager.create_invite(created_by=101)

        self.assertLessEqual(len(f"access_{token}"), 64)
        with closing(sqlite3.connect(self.db_path)) as connection:
            token_hash, used_at = connection.execute(
                "select token_hash, used_at from access_invites"
            ).fetchone()
        self.assertNotEqual(token_hash, token)
        self.assertIsNone(used_at)

        redeemed = self.manager.redeem_invite(
            token=token,
            user_id=202,
            username="teacher",
            first_name="Иван",
            last_name="Иванов",
        )

        self.assertTrue(redeemed)
        self.assertTrue(self.manager.has_access(202))
        self.assertFalse(
            self.manager.redeem_invite(
                token=token,
                user_id=303,
                username="other",
                first_name="Пётр",
                last_name="Петров",
            )
        )
        users = self.manager.get_access_list()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["user_id"], 202)
        self.assertEqual(users[0]["username"], "teacher")
        self.assertEqual(users[0]["first_name"], "Иван")
        self.assertEqual(users[0]["last_name"], "Иванов")

    def test_new_invite_reactivates_revoked_user(self):
        first_token = self.manager.create_invite(created_by=101)
        self.assertTrue(
            self.manager.redeem_invite(first_token, 202, "old", "Old", "Name")
        )
        self.assertTrue(self.manager.revoke_access(202))
        self.assertFalse(self.manager.has_access(202))

        second_token = self.manager.create_invite(created_by=404)
        self.assertTrue(
            self.manager.redeem_invite(second_token, 202, "new", "New", "Name")
        )
        self.assertTrue(self.manager.has_access(202))
        user = self.manager.get_access_list()[0]
        self.assertEqual(user["username"], "new")

    def test_invalid_invite_does_not_grant_access(self):
        self.assertFalse(
            self.manager.redeem_invite("not-a-real-token", 202, "user", "A", "B")
        )
        self.assertFalse(self.manager.has_access(202))


if __name__ == "__main__":
    unittest.main()
