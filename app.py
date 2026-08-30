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
import imageio_ffmpeg


TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


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
    context.user_data["video_received"] = True

    await update.message.reply_text(
        "🎬 ویدیو دریافت و ذخیره شد.\n\n"
        "حالا 🎵 آهنگت را بفرست."
    )


async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("video_received"):
        await update.message.reply_text(
            "اول 🎬 ویدیو را بفرست."
        )
        return

    audio = update.message.audio

    file = await context.bot.get_file(audio.file_id)

    audio_path = f"/tmp/audio_{update.effective_user.id}.mp3"

    await file.download_to_drive(audio_path)

    context.user_data["audio_path"] = audio_path
    context.user_data["audio_received"] = True

    await update.message.reply_text(
        "🎵 آهنگ دریافت و ذخیره شد.\n\n"
        "حالا 📝 دستور ادیتت را بنویس.\n\n"
        "مثال:\n"
        "کات ساده با موزیک\n"
        "یا\n"
        "موزیک روی ویدیو قرار بگیرد"
    )


async def receive_instruction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.user_data.get("video_received"):
        await update.message.reply_text(
            "اول 🎬 ویدیو را بفرست."
        )
        return

    if not context.user_data.get("audio_received"):
        await update.message.reply_text(
            "اول 🎵 آهنگ را بفرست."
        )
        return

    instruction = update.message.text

    video_path = context.user_data["video_path"]
    audio_path = context.user_data["audio_path"]

    output_path = (
        f"/tmp/madrid_era_result_{update.effective_user.id}.mp4"
    )

    await update.message.reply_text(
        "🤖 ادیت شروع شد...\n"
        "⏳ کمی صبر کن."
    )

    try:
        command = [
            FFMPEG,
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
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",

            "-c:a",
            "aac",
            "-b:a",
            "192k",

            "-shortest",

            output_path,
        ]

        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        await update.message.reply_text(
            "✅ ادیت تمام شد!\n"
            "📤 در حال ارسال ویدیو..."
        )

        with open(output_path, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=(
                    "👑 MADRID ERA\n\n"
                    "✅ ویدیو آماده شد."
                )
            )

        # پاک کردن فایل‌های موقت
        for path in [video_path, audio_path, output_path]:
            try:
                os.remove(path)
            except OSError:
                pass

        context.user_data.clear()

    except Exception as error:
        print("EDIT ERROR:", error)

        await update.message.reply_text(
            "❌ هنگام ادیت خطا اتفاق افتاد.\n\n"
            "دوباره با یک ویدیوی کوتاه‌تر امتحان کن."
        )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "🗑️ پروژه پاک شد.\n"
        "برای شروع دوباره /start را بزن."
    )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("cancel", cancel)
    )

    app.add_handler(
        MessageHandler(
            filters.VIDEO,
            receive_video
        )
    )

    app.add_handler(
        MessageHandler(
            filters.AUDIO,
            receive_audio
        )
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
