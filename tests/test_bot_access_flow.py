import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000000:UNIT_TEST_TOKEN")
os.environ.setdefault("TELEGRAM_ADMIN_IDS", "100000001,100000002")

import bot


def make_user(user_id=202):
    return SimpleNamespace(
        id=user_id,
        username="teacher",
        first_name="Иван",
        last_name="Иванов",
    )


class BotAccessFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_without_access_shows_invitation_requirement(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=make_user(),
            effective_message=message,
            message=message,
            callback_query=None,
        )
        context = SimpleNamespace(args=[], user_data={})
        manager = Mock()
        manager.has_access.return_value = False

        with (
            patch.object(bot, "access_manager", manager),
            patch.object(bot.activity_tracker, "log_action"),
            patch.object(bot, "show_main_menu", new=AsyncMock()) as show_menu,
        ):
            await bot.start(update, context)

        message.reply_text.assert_awaited_once()
        self.assertIn("Доступ к боту закрыт", message.reply_text.await_args.args[0])
        show_menu.assert_not_awaited()

    async def test_start_with_valid_invite_grants_access_and_opens_menu(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        user = make_user()
        update = SimpleNamespace(
            effective_user=user,
            effective_message=message,
            message=message,
            callback_query=None,
        )
        context = SimpleNamespace(args=["access_valid-token"], user_data={})
        manager = Mock()
        manager.has_access.return_value = False
        manager.redeem_invite.return_value = True

        with (
            patch.object(bot, "access_manager", manager),
            patch.object(bot.activity_tracker, "log_action"),
            patch.object(bot, "show_main_menu", new=AsyncMock()) as show_menu,
        ):
            await bot.start(update, context)

        manager.redeem_invite.assert_called_once_with(
            token="valid-token",
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )
        self.assertIn("Доступ открыт", message.reply_text.await_args.args[0])
        show_menu.assert_awaited_once_with(update, context)

    async def test_callback_is_blocked_before_route_resolution(self):
        query_message = SimpleNamespace(edit_text=AsyncMock(), reply_text=AsyncMock())
        query = SimpleNamespace(
            data="mode_feedback",
            answer=AsyncMock(),
            message=query_message,
        )
        update = SimpleNamespace(
            effective_user=make_user(),
            callback_query=query,
        )
        context = SimpleNamespace(user_data={})
        manager = Mock()
        manager.has_access.return_value = False

        with (
            patch.object(bot, "access_manager", manager),
            patch.object(bot, "get_callback_router") as get_router,
        ):
            await bot.handle_callback_query(update, context)

        get_router.assert_not_called()
        query.answer.assert_awaited_once_with("Нет доступа", show_alert=True)
        self.assertIn("Доступ к боту закрыт", query_message.edit_text.await_args.args[0])

    async def test_admin_creates_ready_to_forward_link(self):
        query_message = SimpleNamespace(edit_text=AsyncMock())
        query = SimpleNamespace(answer=AsyncMock(), message=query_message)
        update = SimpleNamespace(
            effective_user=make_user(100000001),
            callback_query=query,
        )
        context = SimpleNamespace(bot=SimpleNamespace(username="AlgoNNBot"))
        manager = Mock()
        manager.create_invite.return_value = "one-time-token"

        with (
            patch.object(bot, "access_manager", manager),
            patch.object(bot.activity_tracker, "log_action"),
        ):
            await bot.create_access_invite(update, context)

        manager.create_invite.assert_called_once_with(100000001)
        sent_text = query_message.edit_text.await_args.args[0]
        self.assertIn(
            "https://t.me/AlgoNNBot?start=access_one-time-token",
            sent_text,
        )


if __name__ == "__main__":
    unittest.main()
