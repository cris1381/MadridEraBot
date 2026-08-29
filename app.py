import os
import asyncio
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "👑 MADRID ERA CONTROL\n\n"
        "🎬 ویدیو را بفرست.\n"
        "🎵 بعد آهنگ را بفرست.\n"
        "📝 در آخر دستور ادیتت را بنویس."
    )


async def receive_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video

    file = await context.bot.get_file(video.file_id)
    video_path = f"/tmp/video_{update.effective_user.id}.mp4"

    await file.download_to_drive(video_path)

    context.user_data["video_path"] = video_path

    await update.message.reply_text(
        "🎬 ویدیو دریافت شد.\n\n"
        "حالا 🎵 آهنگت را بفرست."
    )


async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("video_path"):
        await update.message.reply_text("اول 🎬 ویدیو را بفرست.")
        return

    audio = update.message.audio

    file = await context.bot.get_file(audio.file_id)
    audio_path = f"/tmp/audio_{update.effective_user.id
