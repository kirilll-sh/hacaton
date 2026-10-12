import asyncio
import logging
import os
from dotenv import load_dotenv
from maxapi import Bot, Dispatcher

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message_created()
async def handle_message_created(event) -> None:
    try:
        # 1. Забираем ID чата строго по его пути в объекте MAX API
        chat_id = event.chat.chat_id
        
        # 2. Забираем текст строго по его пути
        text = event.message.body.text

        # Игнорируем пустые сообщения или стикеры без текста
        if not text:
            return

        # 3. Формируем ответ
        if text.strip() == "/start":
            reply = "👋 Привет! «Школьный штаб» на связи. Напиши мне что-нибудь, и я повторю."
        else:
            reply = f"📢 Эхо: {text}"

        # 4. Отправляем сообщение обратно в чат
        await bot.send_message(chat_id=chat_id, text=reply)

    except AttributeError as e:
        logger.error(f"Не удалось извлечь данные из сообщения: {e}")
    except Exception as e:
        logger.error(f"Ошибка при отправке ответа: {e}")


async def main() -> None:
    logger.info("🚀 Бот «Школьный штаб» запущен и ждет сообщений...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())