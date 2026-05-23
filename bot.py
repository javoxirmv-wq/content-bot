import os
import json
import logging
from datetime import datetime, time, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ─── SOZLAMALAR ──────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
ADMIN_ID   = int(os.environ.get("ADMIN_ID", "0"))
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")          # "-100xxxxxxxxxx"
TZ         = pytz.timezone("Asia/Tashkent")
LOYIHALAR  = ["DARGAH", "Muhammadjon Nuriddin", "Shoira Isakova"]

TRIAL_FILE  = "trial_posts.json"
DAILY_FILE  = "daily_posts.json"
STATE_FILE  = "state.json"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  MA'LUMOTLAR
# ══════════════════════════════════════════════════════════════════════════════

def _load(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_trial():   return _load(TRIAL_FILE)
def save_trial(d):  _save(TRIAL_FILE, d)
def load_daily():   return _load(DAILY_FILE)
def save_daily(d):  _save(DAILY_FILE, d)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"trial_index": 0}
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

def now_tashkent():
    return datetime.now(TZ)

def today_str():
    return now_tashkent().strftime("%Y-%m-%d")


# ══════════════════════════════════════════════════════════════════════════════
#  KANALDAN POST O'QISH
# ══════════════════════════════════════════════════════════════════════════════

def parse_daily_caption(caption: str):
    """
    Kunlik post formati:
        📅 2025-01-15
        📌 Loyiha: DARGAH

        Caption matni...
    """
    if not caption:
        return None
    lines = caption.strip().split("\n")
    result = {"date": None, "project": None, "caption": ""}
    caption_lines = []
    in_caption = False

    for line in lines:
        s = line.strip()
        if s.startswith("📅"):
            result["date"] = s.replace("📅", "").strip()
        elif s.startswith("📌 Loyiha:"):
            result["project"] = s.replace("📌 Loyiha:", "").strip()
        elif s == "" and result["date"] and result["project"]:
            in_caption = True
        elif in_caption:
            caption_lines.append(line)

    result["caption"] = "\n".join(caption_lines).strip()
    if not result["date"] or not result["project"]:
        return None
    return result


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or str(msg.chat_id) != str(CHANNEL_ID):
        return

    caption = msg.caption or msg.text or ""
    file_id = None
    if msg.video:
        file_id = msg.video.file_id
    elif msg.document:
        file_id = msg.document.file_id

    parsed = parse_daily_caption(caption)

    # ── Kunlik post (sana va loyiha bor) ──────────────────────────────────
    if parsed:
        daily = load_daily()
        # Takrorlanishni tekshirish
        for p in daily:
            if p["date"] == parsed["date"] and p["project"] == parsed["project"]:
                log.info("Takror kunlik post, o'tkazildi.")
                return
        daily.append({
            "date":     parsed["date"],
            "project":  parsed["project"],
            "caption":  parsed["caption"],
            "file_id":  file_id,
            "sent":     False,
            "confirmed":False,
        })
        daily.sort(key=lambda x: x["date"])
        save_daily(daily)
        log.info(f"Kunlik post saqlandi: {parsed['date']} | {parsed['project']}")
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"✅ Kunlik post saqlandi!\n📅 {parsed['date']}\n📌 {parsed['project']}"
            )
        except Exception as e:
            log.error(e)
        return

    # ── Trial post (caption yo'q yoki format yo'q) ────────────────────────
    if file_id:
        trial = load_trial()
        trial.append({
            "file_id":   file_id,
            "position":  len(trial) + 1,
            "sent":      False,
            "confirmed": False,
        })
        save_trial(trial)
        log.info(f"Trial post saqlandi. Jami: {len(trial)}")
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"📦 Trial video saqlandi! (#{len(trial)})"
            )
        except Exception as e:
            log.error(e)


# ══════════════════════════════════════════════════════════════════════════════
#  TUGMALAR (INLINE KEYBOARD)
# ══════════════════════════════════════════════════════════════════════════════

def trial_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Qo'ydim", callback_data="trial_done")
    ]])

def daily_keyboard(post_id: str):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Qo'ydim", callback_data=f"daily_done:{post_id}")
    ]])


# ══════════════════════════════════════════════════════════════════════════════
#  TRIAL MODUL
# ══════════════════════════════════════════════════════════════════════════════

async def send_trial_posts(context: ContextTypes.DEFAULT_TYPE):
    """Har kuni 18:00 da ishga tushadi — keyingi 2 ta trial videoni yuboradi."""
    trial = load_trial()
    state = load_state()
    idx   = state.get("trial_index", 0)

    unsent = [p for p in trial if not p["sent"]]
    if not unsent:
        await context.bot.send_message(ADMIN_ID, "🎉 Barcha trial postlar yuborildi!")
        return

    batch = unsent[:2]

    await context.bot.send_message(
        ADMIN_ID,
        "📦 *Bugungi trial postlar!*\nQuyidagi videolarni trial postga qo'ying 👇",
        parse_mode="Markdown"
    )

    for post in batch:
        try:
            await context.bot.send_video(
                chat_id=ADMIN_ID,
                video=post["file_id"],
                caption=f"📹 Trial post #{post['position']}\n\nBu videoni *trial postga* qo'ying!",
                parse_mode="Markdown",
                reply_markup=trial_keyboard()
            )
            # sent deb belgilash
            for p in trial:
                if p["file_id"] == post["file_id"]:
                    p["sent"] = True
                    p["confirmed"] = False
                    p["sent_at"] = now_tashkent().isoformat()
        except Exception as e:
            log.error(f"Trial yuborishda xato: {e}")

    save_trial(trial)

    # Eslatma vazifasini ishga tushiramiz
    context.job_queue.run_repeating(
        trial_reminder_job,
        interval=1800,   # 30 daqiqa
        first=1800,
        name="trial_reminder",
        data={"positions": [p["position"] for p in batch]}
    )


async def trial_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """30 daqiqada bir marta — tasdiqlanmagan trial postlar uchun eslatma."""
    now = now_tashkent()
    # 00:00 dan keyin to'xtasin
    if now.hour == 0 and now.minute < 31:
        _stop_job(context, "trial_reminder")
        return

    trial = load_trial()
    positions = context.job.data.get("positions", [])
    unconfirmed = [p for p in trial if p["position"] in positions and not p.get("confirmed")]

    if not unconfirmed:
        _stop_job(context, "trial_reminder")
        return

    await context.bot.send_message(
        ADMIN_ID,
        "⚠️ *Siz trial postni hali qo'ymadingiz!*\nIltimos, videoni trial postga qo'ying 👇",
        parse_mode="Markdown"
    )
    for post in unconfirmed:
        try:
            await context.bot.send_video(
                chat_id=ADMIN_ID,
                video=post["file_id"],
                caption=f"📹 Trial post #{post['position']}\n\nBu videoni *trial postga* qo'ying!",
                parse_mode="Markdown",
                reply_markup=trial_keyboard()
            )
        except Exception as e:
            log.error(e)


async def trial_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi '✅ Qo'ydim' tugmasini bosganida."""
    query = update.callback_query
    await query.answer()

    trial = load_trial()
    # Eng so'nggi yuborilgan, tasdiqlanmagan postlarni topamiz
    unconfirmed = [p for p in trial if p.get("sent") and not p.get("confirmed")]

    if not unconfirmed:
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(ADMIN_ID, "✅ Allaqachon belgilangan!")
        return

    # Hammasi tasdiqlanmagan bo'lsa, barchasini confirmed qilamiz
    for p in unconfirmed:
        p["confirmed"] = True
        p["confirmed_at"] = now_tashkent().isoformat()

    save_trial(trial)
    _stop_job(context, "trial_reminder")

    await query.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_message(
        ADMIN_ID,
        "✅ *Trial postlar qo'yildi!* Zo'r ish 💪",
        parse_mode="Markdown"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  KUNLIK POST MODUL
# ══════════════════════════════════════════════════════════════════════════════

async def send_daily_posts(context: ContextTypes.DEFAULT_TYPE):
    """Har kuni 19:00 da ishga tushadi — bugungi kunlik postlarni yuboradi."""
    daily  = load_daily()
    today  = today_str()
    todays = [p for p in daily if p["date"] == today and not p["sent"]]

    if not todays:
        return  # Bugun uchun post yo'q — jim turadi

    await context.bot.send_message(
        ADMIN_ID,
        f"📅 *Bugungi postlar* ({len(todays)} ta)\nInstagramga qo'ying 👇",
        parse_mode="Markdown"
    )

    post_ids = []
    for post in todays:
        post_id = f"{post['date']}_{post['project'].replace(' ', '_')}"
        post_ids.append(post_id)
        caption = (
            f"📌 *{post['project']}*\n\n"
            f"{post['caption']}"
        )
        try:
            if post.get("file_id"):
                await context.bot.send_video(
                    chat_id=ADMIN_ID,
                    video=post["file_id"],
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=daily_keyboard(post_id)
                )
            else:
                await context.bot.send_message(
                    ADMIN_ID,
                    caption,
                    parse_mode="Markdown",
                    reply_markup=daily_keyboard(post_id)
                )
            post["sent"]     = True
            post["confirmed"] = False
            post["sent_at"]  = now_tashkent().isoformat()
        except Exception as e:
            log.error(f"Kunlik post yuborishda xato: {e}")

    save_daily(daily)

    # Eslatma vazifasini ishga tushiramiz
    context.job_queue.run_repeating(
        daily_reminder_job,
        interval=1800,
        first=1800,
        name="daily_reminder",
        data={"post_ids": post_ids, "date": today}
    )


async def daily_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """30 daqiqada bir marta — tasdiqlanmagan kunlik postlar uchun eslatma."""
    now = now_tashkent()
    if now.hour == 0 and now.minute < 31:
        _stop_job(context, "daily_reminder")
        return

    daily    = load_daily()
    post_ids = context.job.data.get("post_ids", [])
    date     = context.job.data.get("date", today_str())

    unconfirmed = []
    for p in daily:
        pid = f"{p['date']}_{p['project'].replace(' ', '_')}"
        if pid in post_ids and p.get("sent") and not p.get("confirmed"):
            unconfirmed.append((pid, p))

    if not unconfirmed:
        _stop_job(context, "daily_reminder")
        return

    await context.bot.send_message(
        ADMIN_ID,
        "⚠️ *Siz postni hali qo'ymadingiz!*\nIltimos, Instagramga qo'ying 👇",
        parse_mode="Markdown"
    )
    for pid, post in unconfirmed:
        caption = f"📌 *{post['project']}*\n\n{post['caption']}"
        try:
            if post.get("file_id"):
                await context.bot.send_video(
                    chat_id=ADMIN_ID,
                    video=post["file_id"],
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=daily_keyboard(pid)
                )
            else:
                await context.bot.send_message(
                    ADMIN_ID, caption,
                    parse_mode="Markdown",
                    reply_markup=daily_keyboard(pid)
                )
        except Exception as e:
            log.error(e)


async def daily_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi '✅ Qo'ydim' tugmasini bosganida (kunlik post)."""
    query   = update.callback_query
    post_id = query.data.split(":", 1)[1]
    await query.answer()

    daily = load_daily()
    found = False
    for p in daily:
        pid = f"{p['date']}_{p['project'].replace(' ', '_')}"
        if pid == post_id:
            p["confirmed"]    = True
            p["confirmed_at"] = now_tashkent().isoformat()
            found = True

    if found:
        save_daily(daily)
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            ADMIN_ID,
            "✅ *Post qo'yildi!* Zo'r ish 💪",
            parse_mode="Markdown"
        )

        # Agar hammasi tasdiqlangan bo'lsa, eslatmani to'xtatamiz
        post_ids = context.job_queue.get_jobs_by_name("daily_reminder")
        if post_ids:
            job_data = post_ids[0].data.get("post_ids", [])
            remaining = [
                p for p in daily
                if f"{p['date']}_{p['project'].replace(' ', '_')}" in job_data
                and not p.get("confirmed")
            ]
            if not remaining:
                _stop_job(context, "daily_reminder")
    else:
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(ADMIN_ID, "⚠️ Post topilmadi.")


# ══════════════════════════════════════════════════════════════════════════════
#  YORDAMCHI
# ══════════════════════════════════════════════════════════════════════════════

def _stop_job(context, name):
    jobs = context.job_queue.get_jobs_by_name(name)
    for j in jobs:
        j.schedule_removal()


# ══════════════════════════════════════════════════════════════════════════════
#  BUYRUQLAR
# ══════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = (
        "👋 *Salom! Men sizning content botingizman.*\n\n"
        "📋 *Buyruqlar:*\n"
        "/status — Umumiy holat\n"
        "/today — Bugungi postlar\n"
        "/upcoming — Kelgusi postlar\n"
        "/trial — Trial postlar holati\n"
        "/format — Post yozish formati\n\n"
        "⏰ Trial: har kuni *18:00*\n"
        "⏰ Kunlik: har kuni *19:00*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    trial  = load_trial()
    daily  = load_daily()
    today  = today_str()

    t_total     = len(trial)
    t_confirmed = len([p for p in trial if p.get("confirmed")])
    t_remaining = t_total - t_confirmed

    d_total     = len(daily)
    d_upcoming  = len([p for p in daily if p["date"] >= today])

    text = (
        "📊 *Umumiy holat:*\n\n"
        f"📦 *Trial postlar:*\n"
        f"  ✅ Qo'yildi: {t_confirmed} ta\n"
        f"  ⏳ Qoldi: {t_remaining} ta\n"
        f"  📦 Jami: {t_total} ta\n\n"
        f"📅 *Kunlik postlar:*\n"
        f"  📆 Kutilayotgan: {d_upcoming} ta\n"
        f"  📦 Jami: {d_total} ta\n\n"
    )
    for loyiha in LOYIHALAR:
        count = len([p for p in daily if p["project"] == loyiha and p["date"] >= today])
        text += f"  📌 {loyiha}: {count} ta\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def trial_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    trial = load_trial()
    total     = len(trial)
    confirmed = len([p for p in trial if p.get("confirmed")])
    sent      = len([p for p in trial if p.get("sent") and not p.get("confirmed")])
    remaining = total - confirmed - sent

    text = (
        f"📦 *Trial postlar:*\n\n"
        f"✅ Qo'yildi: {confirmed} ta\n"
        f"📤 Yuborildi (tasdiq kutmoqda): {sent} ta\n"
        f"⏳ Hali yuborilmagan: {remaining} ta\n"
        f"📦 Jami: {total} ta\n\n"
        f"📅 Taxminan *{remaining // 2}* kun qoldi"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    daily  = load_daily()
    today  = today_str()
    todays = [p for p in daily if p["date"] == today]

    if not todays:
        await update.message.reply_text("📭 Bugun uchun kunlik post yo'q.")
        return

    text = f"📅 *Bugungi kunlik postlar ({len(todays)} ta):*\n\n"
    for p in todays:
        status = "✅" if p.get("confirmed") else ("📤" if p.get("sent") else "⏳")
        text += f"{status} {p['project']}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def upcoming_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    daily  = load_daily()
    today  = today_str()
    future = [p for p in daily if p["date"] >= today][:10]

    if not future:
        await update.message.reply_text("📭 Kelgusi postlar yo'q.")
        return

    text = "📆 *Kelgusi postlar:*\n\n"
    for p in future:
        text += f"📅 {p['date']} | 📌 {p['project']}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def format_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = (
        "📝 *Kunlik post formati:*\n\n"
        "Yashirin kanalga video yuboring, caption:\n\n"
        "```\n"
        "📅 2025-01-15\n"
        "📌 Loyiha: DARGAH\n\n"
        "Instagram uchun matn...\n"
        "#hashtag1 #hashtag2\n"
        "```\n\n"
        "📌 Loyihalar:\n"
        "`DARGAH`\n"
        "`Muhammadjon Nuriddin`\n"
        "`Shoira Isakova`\n\n"
        "📦 *Trial post uchun:*\n"
        "Shunchaki videoni yuboring — caption shart emas!"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Buyruqlar
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("help",     start))
    app.add_handler(CommandHandler("status",   status_cmd))
    app.add_handler(CommandHandler("trial",    trial_cmd))
    app.add_handler(CommandHandler("today",    today_cmd))
    app.add_handler(CommandHandler("upcoming", upcoming_cmd))
    app.add_handler(CommandHandler("format",   format_cmd))

    # Tugmalar
    app.add_handler(CallbackQueryHandler(trial_done_callback, pattern="^trial_done$"))
    app.add_handler(CallbackQueryHandler(daily_done_callback, pattern="^daily_done:"))

    # Kanaldan postlarni o'qish
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))

    # Har kunlik vazifalar (Toshkent vaqti)
    jq = app.job_queue
    jq.run_daily(
        send_trial_posts,
        time=time(hour=18, minute=0, tzinfo=TZ),
        name="trial_daily",
    )
    jq.run_daily(
        send_daily_posts,
        time=time(hour=19, minute=0, tzinfo=TZ),
        name="daily_post",
    )

    log.info("Bot ishga tushdi ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
