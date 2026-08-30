import os
import subprocess
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

    if not video:
        await update.message.reply_text("❌ ویدیو دریافت نشد.")
        return

    file = await context.bot.get_file(video.file_id)

    video_path = f"/tmp/video_{update.effective_user.id}.mp4"
    await file.download_to_drive(video_path)

    context.user_data["video_path"] = video_path

    await update.message.reply_text(
        "🎬 ویدیو دریافت و ذخیره شد.\n\n"
        "حالا 🎵 آهنگت را بفرست."
    )


async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("video_path"):
        await update.message.reply_text("اول 🎬 ویدیو را بفرست.")
        return

    audio = update.message.audio

    if not audio:
        await update.message.reply_text("❌ آهنگ دریافت نشد.")
        return

    file = await context.bot.get_file(audio.file_id)

    audio_path = f"/tmp/audio_{update.effective_user.id}.mp3"
    await file.download_to_drive(audio_path)

    context.user_data["audio_path"] = audio_path

    await update.message.reply_text(
        "🎵 آهنگ دریافت و ذخیره شد.\n\n"
        "حالا 📝 دستور ادیتت را بنویس."
    )


async def receive_instruction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.user_data.get("video_path"):
        await update.message.reply_text("اول 🎬 ویدیو را بفرست.")
        return

    if not context.user_data.get("audio_path"):
        await update.message.reply_text("اول 🎵 آهنگ را بفرست.")
        return

    instruction = update.message.text
    context.user_data["instruction"] = instruction

    await update.message.reply_text(
        "⏳ دستور ادیت دریافت شد.\n\n"
        "🤖 در حال آماده‌سازی پروژه..."
    )

    video_path = context.user_data["video_path"]
    audio_path = context.user_data["audio_path"]

    output_path = f"/tmp/madrid_era_{update.effective_user.id}.mp4"

    try:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-i",
            audio_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            output_path,
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            await update.message.reply_text(
                "❌ هنگام پردازش ویدیو خطا رخ داد."
            )
            return

        await update.message.reply_video(
            video=open(output_path, "rb"),
            caption="👑 MADRID ERA\n✅ ادیت آماده شد."
        )

    except Exception as e:
        print("ERROR:", e)

        await update.message.reply_text(
            "❌ پردازش انجام نشد. لطفاً دوباره امتحان کن."
        )

    finally:
        for path in [video_path, audio_path, output_path]:
            if os.path.exists(path):
                os.remove(path)

        context.user_data.clear()


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "🗑️ پروژه پاک شد.\n"
        "برای شروع دوباره /start را بزن."
    )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(
        MessageHandler(filters.VIDEO, receive_video)
    )

    app.add_handler(
        MessageHandler(filters.AUDIO, receive_audio)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_instruction
        )
    )

    print("MADRID ERA CONTROL is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
