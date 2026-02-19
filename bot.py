"""
CrossyPosty — Telegram bot for cross-posting videos to YouTube, TikTok, Instagram, VK
"""
import os
import sys
import json
import asyncio
import logging
import uuid
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    FSInputFile, BotCommand
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
TOKENS_FILE = DATA_DIR / "tokens.json"

# --- Token storage ---
def load_tokens():
    if TOKENS_FILE.exists():
        return json.loads(TOKENS_FILE.read_text())
    return {}

def save_tokens(data):
    TOKENS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def get_user_tokens(user_id):
    data = load_tokens()
    return data.get(str(user_id), {})

def set_user_token(user_id, platform, token_data):
    data = load_tokens()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {}
    data[uid][platform] = token_data
    save_tokens(data)

def remove_user_token(user_id, platform):
    data = load_tokens()
    uid = str(user_id)
    if uid in data and platform in data[uid]:
        del data[uid][platform]
        save_tokens(data)

# --- Platform imports ---
from platforms.youtube_uploader import YouTubeUploader
from platforms.vk_uploader import VKUploader
from platforms.instagram_uploader import InstagramUploader
from platforms.tiktok_uploader import TikTokUploader

youtube = YouTubeUploader()
vk = VKUploader()
instagram = InstagramUploader()
tiktok = TikTokUploader()

PLATFORMS = {
    "youtube": {"name": "YouTube Shorts", "emoji": "▶️", "uploader": youtube},
    "vk": {"name": "VK Clips", "emoji": "📱", "uploader": vk},
    "instagram": {"name": "Instagram Reels", "emoji": "📸", "uploader": instagram},
    "tiktok": {"name": "TikTok", "emoji": "🎵", "uploader": tiktok},
}

# --- FSM ---
class UploadFlow(StatesGroup):
    waiting_video = State()
    waiting_title = State()
    waiting_description = State()
    choosing_platforms = State()
    uploading = State()

# --- Router ---
router = Router()

# --- /start ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    tokens = get_user_tokens(message.from_user.id)
    connected = []
    for p_id, p_info in PLATFORMS.items():
        if p_id in tokens:
            connected.append(f"  {p_info['emoji']} {p_info['name']} ✅")
        else:
            connected.append(f"  {p_info['emoji']} {p_info['name']} ❌")

    text = (
        "🚀 <b>CrossyPosty</b> — кросспостинг видео\n\n"
        "Отправь мне видео — я опубликую его на выбранных платформах.\n\n"
        "<b>Подключённые аккаунты:</b>\n"
        + "\n".join(connected) + "\n\n"
        "<b>Команды:</b>\n"
        "  /post — отправить видео\n"
        "  /connect — подключить платформу\n"
        "  /disconnect — отключить платформу\n"
        "  /status — статус аккаунтов\n"
        "  /help — помощь"
    )
    await message.answer(text, parse_mode="HTML")

# --- /status ---
@router.message(Command("status"))
async def cmd_status(message: Message):
    tokens = get_user_tokens(message.from_user.id)
    lines = ["<b>📊 Статус аккаунтов:</b>\n"]
    for p_id, p_info in PLATFORMS.items():
        if p_id in tokens:
            lines.append(f"{p_info['emoji']} {p_info['name']} — ✅ подключён")
        else:
            lines.append(f"{p_info['emoji']} {p_info['name']} — ❌ не подключён")
    await message.answer("\n".join(lines), parse_mode="HTML")

# --- /connect ---
@router.message(Command("connect"))
async def cmd_connect(message: Message):
    tokens = get_user_tokens(message.from_user.id)
    buttons = []
    for p_id, p_info in PLATFORMS.items():
        status = "✅" if p_id in tokens else "❌"
        buttons.append([InlineKeyboardButton(
            text=f"{p_info['emoji']} {p_info['name']} {status}",
            callback_data=f"connect_{p_id}"
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выбери платформу для подключения:", reply_markup=kb)

# --- Connect handlers ---
@router.callback_query(F.data.startswith("connect_"))
async def connect_platform(callback: CallbackQuery, state: FSMContext):
    platform = callback.data.replace("connect_", "")
    user_id = callback.from_user.id

    if platform == "youtube":
        url = youtube.get_auth_url()
        if url:
            await callback.message.answer(
                f"▶️ <b>Подключение YouTube</b>\n\n"
                f"1. Перейди по ссылке:\n{url}\n\n"
                f"2. Разреши доступ\n"
                f"3. Скопируй код и отправь мне",
                parse_mode="HTML"
            )
            await state.set_state(UploadFlow.waiting_video)
            await state.update_data(connecting="youtube")
        else:
            await callback.message.answer(
                "❌ YouTube не настроен. Добавь client_secret.json от Google OAuth."
            )

    elif platform == "vk":
        vk_app_id = os.getenv("VK_APP_ID", "")
        if vk_app_id:
            url = (
                f"https://oauth.vk.com/authorize?client_id={vk_app_id}"
                f"&scope=video,wall,offline&response_type=token&v=5.199"
            )
            await callback.message.answer(
                f"📱 <b>Подключение VK</b>\n\n"
                f"1. Перейди по ссылке:\n{url}\n\n"
                f"2. Разреши доступ\n"
                f"3. Скопируй ВСЮ ссылку из адресной строки и отправь мне",
                parse_mode="HTML"
            )
            await state.set_state(UploadFlow.waiting_video)
            await state.update_data(connecting="vk")
        else:
            await callback.message.answer("❌ VK_APP_ID не задан в .env")

    elif platform == "instagram":
        await callback.message.answer(
            "📸 <b>Подключение Instagram</b>\n\n"
            "Отправь логин и пароль через пробел:\n"
            "<code>username password</code>\n\n"
            "⚠️ Рекомендуется использовать отдельный аккаунт.\n"
            "Данные хранятся только локально на сервере.",
            parse_mode="HTML"
        )
        await state.set_state(UploadFlow.waiting_video)
        await state.update_data(connecting="instagram")

    elif platform == "tiktok":
        client_key = os.getenv("TIKTOK_CLIENT_KEY", "")
        redirect_uri = os.getenv("TIKTOK_REDIRECT_URI", "")
        if client_key and redirect_uri:
            url = (
                f"https://www.tiktok.com/v2/auth/authorize/"
                f"?client_key={client_key}"
                f"&scope=user.info.basic,video.upload,video.publish"
                f"&response_type=code"
                f"&redirect_uri={redirect_uri}"
            )
            await callback.message.answer(
                f"🎵 <b>Подключение TikTok</b>\n\n"
                f"1. Перейди по ссылке:\n{url}\n\n"
                f"2. Разреши доступ\n"
                f"3. Скопируй код из URL и отправь мне",
                parse_mode="HTML"
            )
            await state.set_state(UploadFlow.waiting_video)
            await state.update_data(connecting="tiktok")
        else:
            await callback.message.answer(
                "❌ TikTok не настроен. Нужен TIKTOK_CLIENT_KEY и TIKTOK_REDIRECT_URI в .env"
            )

    await callback.answer()

# --- /disconnect ---
@router.message(Command("disconnect"))
async def cmd_disconnect(message: Message):
    tokens = get_user_tokens(message.from_user.id)
    buttons = []
    for p_id, p_info in PLATFORMS.items():
        if p_id in tokens:
            buttons.append([InlineKeyboardButton(
                text=f"❌ Отключить {p_info['emoji']} {p_info['name']}",
                callback_data=f"disconnect_{p_id}"
            )])
    if not buttons:
        await message.answer("Нет подключённых платформ.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выбери что отключить:", reply_markup=kb)

@router.callback_query(F.data.startswith("disconnect_"))
async def disconnect_platform(callback: CallbackQuery):
    platform = callback.data.replace("disconnect_", "")
    remove_user_token(callback.from_user.id, platform)
    name = PLATFORMS[platform]["name"]
    await callback.message.answer(f"✅ {name} отключён")
    await callback.answer()

# --- /post and video handling ---
@router.message(Command("post"))
async def cmd_post(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("📹 Отправь мне видео для публикации:")
    await state.set_state(UploadFlow.waiting_video)

# Handle incoming text (for auth codes and credentials)
@router.message(UploadFlow.waiting_video, F.text)
async def handle_auth_text(message: Message, state: FSMContext):
    data = await state.get_data()
    connecting = data.get("connecting")

    if not connecting:
        await message.answer("Отправь видео или нажми /post")
        return

    user_id = message.from_user.id
    text = message.text.strip()

    if connecting == "youtube":
        try:
            creds = await asyncio.to_thread(youtube.exchange_code, text)
            set_user_token(user_id, "youtube", creds)
            await message.answer("✅ YouTube подключён!")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")

    elif connecting == "vk":
        # Extract token from URL
        if "access_token=" in text:
            import re
            match = re.search(r"access_token=([^&]+)", text)
            if match:
                token = match.group(1)
                set_user_token(user_id, "vk", {"access_token": token})
                await message.answer("✅ VK подключён!")
            else:
                await message.answer("❌ Не удалось извлечь токен из ссылки")
        else:
            await message.answer("❌ Отправь полную ссылку из адресной строки")

    elif connecting == "instagram":
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            username, password = parts
            try:
                result = await asyncio.to_thread(instagram.login, username, password)
                set_user_token(user_id, "instagram", {
                    "username": username,
                    "session": result
                })
                await message.answer("✅ Instagram подключён!")
            except Exception as e:
                await message.answer(f"❌ Ошибка входа: {e}")
        else:
            await message.answer("Отправь логин и пароль через пробел")

    elif connecting == "tiktok":
        try:
            token_data = await asyncio.to_thread(tiktok.exchange_code, text)
            set_user_token(user_id, "tiktok", token_data)
            await message.answer("✅ TikTok подключён!")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")

    await state.clear()

# Handle video
@router.message(F.video)
async def handle_video(message: Message, state: FSMContext):
    await state.clear()
    video = message.video

    if video.file_size > 256 * 1024 * 1024:
        await message.answer("❌ Видео слишком большое (макс 256 МБ)")
        return

    await state.update_data(
        video_file_id=video.file_id,
        video_file_size=video.file_size,
    )
    await message.answer(
        "✏️ Введи <b>заголовок</b> для видео (или отправь <code>-</code> чтобы пропустить):",
        parse_mode="HTML"
    )
    await state.set_state(UploadFlow.waiting_title)

@router.message(F.video_note)
async def handle_video_note(message: Message):
    await message.answer("❌ Кружки не поддерживаются. Отправь обычное видео.")

# Title
@router.message(UploadFlow.waiting_title, F.text)
async def handle_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if title == "-":
        title = f"Video {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    await state.update_data(title=title)
    await message.answer(
        "📝 Введи <b>описание</b> (или <code>-</code> чтобы пропустить):",
        parse_mode="HTML"
    )
    await state.set_state(UploadFlow.waiting_description)

# Description
@router.message(UploadFlow.waiting_description, F.text)
async def handle_description(message: Message, state: FSMContext):
    desc = message.text.strip()
    if desc == "-":
        desc = ""
    await state.update_data(description=desc)

    # Show platform selection
    tokens = get_user_tokens(message.from_user.id)
    if not tokens:
        await message.answer(
            "❌ Нет подключённых платформ!\n"
            "Используй /connect чтобы подключить аккаунты."
        )
        await state.clear()
        return

    buttons = []
    selected = list(tokens.keys())
    await state.update_data(selected_platforms=selected)

    for p_id in tokens:
        p_info = PLATFORMS[p_id]
        checked = "☑️" if p_id in selected else "⬜"
        buttons.append([InlineKeyboardButton(
            text=f"{checked} {p_info['emoji']} {p_info['name']}",
            callback_data=f"toggle_{p_id}"
        )])
    buttons.append([InlineKeyboardButton(text="🚀 Опубликовать", callback_data="publish")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выбери платформы:", reply_markup=kb)
    await state.set_state(UploadFlow.choosing_platforms)

# Toggle platform
@router.callback_query(UploadFlow.choosing_platforms, F.data.startswith("toggle_"))
async def toggle_platform(callback: CallbackQuery, state: FSMContext):
    platform = callback.data.replace("toggle_", "")
    data = await state.get_data()
    selected = data.get("selected_platforms", [])

    if platform in selected:
        selected.remove(platform)
    else:
        selected.append(platform)
    await state.update_data(selected_platforms=selected)

    tokens = get_user_tokens(callback.from_user.id)
    buttons = []
    for p_id in tokens:
        p_info = PLATFORMS[p_id]
        checked = "☑️" if p_id in selected else "⬜"
        buttons.append([InlineKeyboardButton(
            text=f"{checked} {p_info['emoji']} {p_info['name']}",
            callback_data=f"toggle_{p_id}"
        )])
    buttons.append([InlineKeyboardButton(text="🚀 Опубликовать", callback_data="publish")])

    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()

# Publish
@router.callback_query(UploadFlow.choosing_platforms, F.data == "publish")
async def publish(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_platforms", [])

    if not selected:
        await callback.answer("⚠️ Выбери хотя бы одну платформу!", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    status_msg = await callback.message.answer("⏳ Скачиваю видео...")

    bot_instance = callback.bot
    user_id = callback.from_user.id
    tokens = get_user_tokens(user_id)
    title = data.get("title", "Video")
    description = data.get("description", "")
    video_file_id = data["video_file_id"]

    # Download video
    file = await bot_instance.get_file(video_file_id)
    local_path = str(DOWNLOAD_DIR / f"{uuid.uuid4().hex}.mp4")
    await bot_instance.download_file(file.file_path, local_path)

    file_size_mb = os.path.getsize(local_path) / 1024 / 1024
    logger.info(f"Video downloaded: {local_path} ({file_size_mb:.1f} MB)")

    results = []
    errors = []

    for p_id in selected:
        p_info = PLATFORMS[p_id]
        p_tokens = tokens.get(p_id, {})

        await status_msg.edit_text(f"⏳ Загружаю на {p_info['emoji']} {p_info['name']}...")

        try:
            uploader = p_info["uploader"]
            result = await asyncio.to_thread(
                uploader.upload,
                file_path=local_path,
                title=title,
                description=description,
                token_data=p_tokens
            )
            results.append(f"{p_info['emoji']} {p_info['name']}: ✅ {result.get('url', 'OK')}")
        except Exception as e:
            logger.exception(f"Upload to {p_id} failed")
            errors.append(f"{p_info['emoji']} {p_info['name']}: ❌ {e}")

    # Cleanup
    try:
        os.remove(local_path)
    except:
        pass

    # Report
    lines = ["<b>📊 Результат публикации:</b>\n"]
    if results:
        lines.extend(results)
    if errors:
        lines.append("")
        lines.extend(errors)

    await status_msg.edit_text("\n".join(lines), parse_mode="HTML")
    await state.clear()

# --- /help ---
@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🚀 <b>CrossyPosty — помощь</b>\n\n"
        "<b>Основные команды:</b>\n"
        "  /post — отправить видео на публикацию\n"
        "  /connect — подключить платформу\n"
        "  /disconnect — отключить платформу\n"
        "  /status — статус подключений\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Подключи аккаунты через /connect\n"
        "2. Отправь видео или нажми /post\n"
        "3. Введи заголовок и описание\n"
        "4. Выбери платформы\n"
        "5. Нажми Опубликовать\n\n"
        "<b>Поддерживаемые платформы:</b>\n"
        "  ▶️ YouTube Shorts\n"
        "  📱 VK Clips\n"
        "  📸 Instagram Reels\n"
        "  🎵 TikTok",
        parse_mode="HTML"
    )

# --- Main ---
async def main():
    if not BOT_TOKEN:
        print("[!] BOT_TOKEN not set in .env")
        sys.exit(1)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Start"),
        BotCommand(command="post", description="Post video"),
        BotCommand(command="connect", description="Connect platform"),
        BotCommand(command="disconnect", description="Disconnect platform"),
        BotCommand(command="status", description="Account status"),
        BotCommand(command="help", description="Help"),
    ])

    logger.info("CrossyPosty bot started!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
