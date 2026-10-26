from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

OPEN_APP_TEXT = "Открыть калькулятор"

START_TEXT = (
    "Привет! Это калькулятор рисков и P&L концерта.\n\n"
    "Введи вместимость, цены билетов и расходы — посчитаю прибыль по сценариям, "
    "точку безубыточности и запас прочности."
)

HELP_TEXT = (
    "Калькулятор открывается кнопкой под сообщением или кнопкой меню слева от поля ввода.\n"
    "/start — показать кнопку ещё раз"
)


def webapp_keyboard(webapp_url: str) -> InlineKeyboardMarkup:
    # Именно inline-кнопка: при запуске с reply-кнопки initData приходит пустой
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=OPEN_APP_TEXT, web_app=WebAppInfo(url=webapp_url))]
        ]
    )


async def start(message: Message, webapp_url: str) -> None:
    await message.answer(START_TEXT, reply_markup=webapp_keyboard(webapp_url))


async def help_(message: Message) -> None:
    await message.answer(HELP_TEXT)


def create_router() -> Router:
    """Новый роутер на каждый диспетчер: aiogram не даёт подключить один роутер дважды."""
    router = Router()
    # Кнопки web_app работают только в личных чатах
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(start, CommandStart())
    router.message.register(help_, Command("help"))
    return router
