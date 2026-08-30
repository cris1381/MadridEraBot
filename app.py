import os
import re
import json
import math
import shutil
import asyncio
import tempfile
import subprocess
from pathlib import Path

import numpy as np
import cv2
import yt_dlp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

WORK = Path(tempfile.gettempdir()) / "madrid_era"
WORK.mkdir(parents=True, exist_ok=True)

# One Telegram bot process only: Railway should run a single replica.
JOBS = {}

def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-5000:] or "command failed")
    return p.stdout

def ffprobe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(out.strip())

def download_url(url, out_dir):
    opts = {
        "outtmpl": str(Path(out_dir) / "reference.%(ext)s"),
        "format": "bestvideo[height<=1920]+bestaudio/best[height<=1920]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
        if not path.exists():
            candidates = list(Path(out_dir).glob("reference.*"))
            if not candidates:
                raise RuntimeError("Reference video could not be downloaded")
            path = candidates[0]
        return path

def extract_audio(video, wav):
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "22050",
         "-c:a", "pcm_s16le", str(wav)])

def estimate_beats(wav):
    # Lightweight beat estimate: energy peaks + autocorrelation.
    import wave
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    if len(data) < sr:
        return [0.0]
    hop = max(1, int(sr * 0.055))
    frame = max(hop * 4, int(sr * 0.12))
    energies = []
    times = []
    for i in range(0, max(1, len(data)-frame), hop):
        x = data[i:i+frame]
        energies.append(float(np.sqrt(np.mean(x*x) + 1e-9)))
        times.append(i/sr)
    e = np.asarray(energies)
    if len(e) < 5:
        return [0.0]
    smooth = np.convolve(e, np.ones(5)/5, mode="same")
    novelty = np.maximum(0, e - smooth)
    min_gap = max(1, int(0.25 / (hop/sr)))
    threshold = float(np.percentile(novelty, 72))
    peaks = []
    last = -min_gap
    for i in range(1, len(novelty)-1):
        if i-last < min_gap:
            continue
        if novelty[i] >= threshold and novelty[i] >= novelty[i-1] and novelty[i] >= novelty[i+1]:
            peaks.append(times[i]); last = i
    # If detection is sparse, infer a stable beat grid from autocorrelation.
    if len(peaks) < 5:
        ac = np.correlate(e-e.mean(), e-e.mean(), mode="full")[len(e)-1:]
        lo = int(0.35/(hop/sr)); hi = min(len(ac)-1, int(1.0/(hop/sr)))
        lag = lo + int(np.argmax(ac[lo:hi])) if hi > lo else int(0.55/(hop/sr))
        step = max(0.28, min(0.9, lag*hop/sr))
        peaks = list(np.arange(0, len(data)/sr, step))
    return peaks

def analyze_reference(video):
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = n / fps if n else ffprobe_duration(video)
    sample_every = max(1, int(fps * 0.12))
    prev = None
    scores = []
    frames = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % sample_every == 0:
            small = cv2.resize(frame, (96, 54))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (5,5), 0)
            if prev is not None:
                scores.append((idx/fps, float(np.mean(cv2.absdiff(gray, prev)))))
            prev = gray
            frames.append(idx/fps)
        idx += 1
    cap.release()

    if not scores:
        cuts = [0.0, duration]
    else:
        vals = np.array([s for _,s in scores], dtype=np.float32)
        threshold = max(float(np.percentile(vals, 92)), float(vals.mean() + 1.8*vals.std()))
        raw = [t for t,s in scores if s >= threshold]
        # Avoid false multiple cuts inside the same transition.
        cuts = [0.0]
        for t in raw:
            if t-cuts[-1] >= 0.35:
                cuts.append(t)
        if duration-cuts[-1] > 0.2:
            cuts.append(duration)
        if len(cuts) < 3:
            # A reference with few hard cuts still gets a useful rhythm.
            step = min(1.2, max(0.45, duration/8))
            cuts = list(np.arange(0, duration, step))
            if cuts[-1] != duration:
                cuts.append(duration)

    segments = [(cuts[i], cuts[i+1]) for i in range(len(cuts)-1)
                if cuts[i+1]-cuts[i] >= 0.18]
    # Keep the edit pattern bounded but preserve the reference order.
    if len(segments) > 80:
        segments = segments[:80]

    return {
        "duration": duration,
        "cuts": cuts,
        "segments": segments,
        "count": len(segments),
    }

def parse_duration(text, fallback=15):
    # User controls final duration. Accept "10", "10 sec", "20 ثانیه", "30s".
    m = re.search(r"(?<!\d)(\d{1,3})(?:\s*(?:s|sec|secs|second|seconds|ثانیه))?", text, re.I)
    if not m:
        return min(30, max(1, fallback))
    return min(30, max(1, int(m.group(1))))

def build_edit(input_video, music, ref, target, out):
    info = analyze_reference(ref)
    beats = estimate_beats(WORK / "music.wav")

    # Reference-derived rhythm. The reference determines relative shot lengths,
    # while the user determines total duration.
    pattern = [b-a for a,b in info["segments"]]
    if not pattern:
        pattern = [0.55]
    total = sum(pattern)
    scaled = [max(0.18, x * target / total) for x in pattern]
    # Re-normalize exactly to target.
    factor = target / sum(scaled)
    scaled = [x*factor for x in scaled]

    # Use beat positions as preferred cut points. If a reference cut is close
    # to a beat, snap it to the beat; otherwise preserve the reference timing.
    boundaries = [0.0]
    acc = 0.0
    for d in scaled:
        acc += d
        if acc >= target:
            break
        if beats:
            near = min(beats, key=lambda x: abs(x-acc))
            if abs(near-acc) <= 0.16:
                acc = near
        boundaries.append(min(acc, target))
    if boundaries[-1] < target-0.01:
        boundaries.append(target)

    # Make enough source slices by looping the user's footage if necessary.
    src_dur = ffprobe_duration(input_video)
    segments = []
    for i in range(len(boundaries)-1):
        st, en = boundaries[i], boundaries[i+1]
        length = max(0.12, en-st)
        # Cycle through source footage so the reference pattern remains usable.
        src_start = (sum(s[1] for s in segments) % max(0.01, src_dur))
        if src_start + length > src_dur:
            src_start = 0.0
        segments.append((src_start, length))

    parts = []
    temp = Path(out).parent
    for i,(ss,ln) in enumerate(segments):
        part = temp / f"part_{i:03d}.mp4"
        # Reference-inspired micro motion: alternating subtle zoom in/out.
        zoom = 1.0 + (0.035 if i % 2 == 0 else 0.018)
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            f"zoompan=z='{zoom}':d=1:s=1080x1920:fps=30,"
            "setsar=1"
        )
        # zoompan with one frame can be fragile; use crop/scale plus a safe
        # subtle zoom through scale/crop instead.
        if i % 2 == 0:
            vf = "scale=1118:1985:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
        else:
            vf = "scale=1098:1952:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
        run(["ffmpeg","-y","-ss",f"{ss:.3f}","-i",str(input_video),
             "-t",f"{ln:.3f}","-vf",vf,"-r","30",
             "-an","-c:v","libx264","-preset","veryfast","-crf","18",
             str(part)])
        parts.append(part)

    concat = temp / "concat.txt"
    concat.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8")
    silent = temp / "silent.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),
         "-t",f"{target:.3f}","-an","-c","copy",str(silent)])

    # Music is trimmed to the user's chosen duration and looped if needed.
    run(["ffmpeg","-y","-stream_loop","-1","-i",str(music),"-t",f"{target:.3f}",
         "-vn","-c:a","aac","-b:a","192k",str(temp/"music.m4a")])

    run(["ffmpeg","-y","-i",str(silent),"-i",str(temp/"music.m4a"),
         "-t",f"{target:.3f}","-map","0:v:0","-map","1:a:0",
         "-c:v","libx264","-preset","veryfast","-crf","18",
         "-c:a","aac","-b:a","192k","-movflags","+faststart",
         "-pix_fmt","yuv420p",str(out)])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    JOBS[update.effective_user.id] = {"video":None,"music":None,"reference":None,"duration":15}
    await update.message.reply_text(
        "👑 MADRID ERA CONTROL\n\n"
        "1️⃣ ویدیوی خودت را بفرست\n"
        "2️⃣ آهنگ را بفرست\n"
        "3️⃣ لینک TikTok مرجع را بفرست\n"
        "4️⃣ مدت را بگو: مثلاً 10 یا 15 یا 30 ثانیه\n"
        "5️⃣ دستور ادیت را بنویس.\n\n"
        "حداکثر خروجی 30 ثانیه است."
    )

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    job = JOBS.setdefault(uid, {"video":None,"music":None,"reference":None,"duration":15})
    msg = update.message
    text = (msg.text or "").strip()

    if text.startswith("http://") or text.startswith("https://"):
        job["reference"] = text
        await msg.reply_text("🔗 لینک مرجع دریافت شد. حالا مدت خروجی را بگو (مثلاً 15 ثانیه).")
        return

    d = parse_duration(text, fallback=job.get("duration",15))
    if re.search(r"\d", text) and ("ثانیه" in text.lower() or "sec" in text.lower() or text.isdigit()):
        job["duration"] = d
        await msg.reply_text(f"⏱️ مدت خروجی: {d} ثانیه\nحالا دستور ادیت را بنویس.")
        return

    if msg.video:
        if msg.video.file_size and msg.video.file_size > 49*1024*1024:
            await msg.reply_text("❌ ویدیو بزرگ‌تر از حد مجاز تلگرام است.")
            return
        f = await context.bot.get_file(msg.video.file_id)
        p = WORK / f"{uid}_input.mp4"
        await f.download_to_drive(str(p))
        job["video"] = p
        await msg.reply_text("🎬 ویدیوی اصلی دریافت شد.")
        return

    if msg.audio or msg.document:
        obj = msg.audio or msg.document
        name = (getattr(obj, "file_name", "") or "").lower()
        if name.endswith((".mp3",".m4a",".wav",".aac",".ogg",".flac")) or msg.audio:
            f = await context.bot.get_file(obj.file_id)
            p = WORK / f"{uid}_music"
            await f.download_to_drive(str(p))
            job["music"] = p
            await msg.reply_text("🎵 آهنگ دریافت و ذخیره شد.")
            return

    if text and job.get("video") and job.get("music") and job.get("reference"):
        await msg.reply_text("⚙️ تحلیل مرجع و ساخت ادیت شروع شد... ممکن است کمی زمان ببرد.")
        folder = WORK / str(uid)
        folder.mkdir(exist_ok=True)
        try:
            ref = download_url(job["reference"], folder)
            extract_audio(job["music"], folder/"music.wav")
            out = folder / "MADRID_ERA_FINAL.mp4"
            build_edit(job["video"], job["music"], ref, job.get("duration",15), out)
            await msg.reply_video(video=out.open("rb"), caption=f"👑 MADRID ERA\n✅ آماده شد — {job.get('duration',15)} ثانیه")
        except Exception as e:
            print("PROCESS ERROR:", repr(e))
            await msg.reply_text("❌ پردازش انجام نشد.\nاگر دوباره خطا داد، متن Logs را بفرست.")
        return

    if text:
        await msg.reply_text("📝 دستور دریافت شد. برای شروع پردازش، ویدیو + آهنگ + لینک TikTok مرجع را بفرست.")

def main():
    print("👑 MADRID ERA CONTROL IS RUNNING...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, handle))
    # Long polling. Railway must have ONLY ONE running replica/process for this bot.
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
