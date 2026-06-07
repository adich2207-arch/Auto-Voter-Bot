"""
bot.py — Auto Voter Bot with premium Telegram emoji support.
Uses HTML parse mode to render <tg-emoji> custom stickers.
Run: python bot.py
"""

import asyncio
import json
import logging
import os
import time

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ConversationHandler, ContextTypes, MessageHandler, filters,
)
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError

import database as db
import account_manager as am

load_dotenv()
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID  = int(os.getenv("OWNER_ID", "0"))

# ── Conversation states ────────────────────────────────────────────────────────
ASK_PHONE, ASK_OTP, ASK_2FA, ASK_CAMP_TARGET, ASK_VOTE_OPTION, ASK_EMOJI = range(6)

ACTION_LABELS = {
    "react":           "React Only",
    "vote":            "Vote Only",
    "react_vote":      "React + Vote",
    "view":            "View Only",
    "react_view":      "React + View",
    "vote_view":       "Vote + View",
    "react_vote_view": "React + Vote + View",
    "join":            "Join Channel",
    "leave":           "Leave Channel",
    "dm":              "Bulk DM",
    "refer":           "Refer",
}

POST_ACTIONS = {"react", "vote", "view", "react_vote", "react_view", "vote_view", "react_vote_view"}
CHAN_ACTIONS  = {"join", "leave"}
SPEED_LABELS  = {1: "Very Fast (0.5s)", 2: "Fast (1.5s)", 3: "Normal (3s)",
                 4: "Slow (5s)", 5: "Very Slow (10s)"}

# ══════════════════════════════════════════════════════════════════════
#  PREMIUM EMOJI HELPER
#  Usage: E("check")  →  <tg-emoji emoji-id="...">⭐</tg-emoji>
#  Fallback plain emoji is shown in clients that don't support custom emoji.
# ══════════════════════════════════════════════════════════════════════

_PE = {
    # name            : (emoji_id,               fallback)
    "check_green"     : ("6125082079488121878",   "✅"),
    "check_blue"      : ("6129472184604695207",   "✔️"),
    "sparkle"         : ("6125239923831217642",   "✨"),
    "lightning"       : ("6129805465476929485",   "⚡"),
    "crown"           : ("6129705083501293112",   "👑"),
    "fire"            : ("6129903231817488942",   "🔥"),
    "fire2"           : ("6129797920566589031",   "🔥"),
    "party"           : ("6129579803600231171",   "🎉"),
    "star"            : ("6129444065453808638",   "⭐"),
    "heart"           : ("6129494286506401122",   "💝"),
    "gift"            : ("6129932613688764241",   "🎁"),
    "chart"           : ("6129801569941592173",   "📊"),
    "warning"         : ("6129782440157256336",   "⚠️"),
    "lock"            : ("6129736819014639296",   "🔐"),
    "cloud"           : ("6129393837823753679",   "☁️"),
    "ice"             : ("6129987078333956715",   "❄️"),
    "top"             : ("6129627894349045589",   "🔝"),
    "money"           : ("6129731974291527294",   "💰"),
    "megaphone"       : ("6129492160497589882",   "📢"),
    "peacock"         : ("6129846551134084367",   "🦚"),
    "dragon"          : ("6129522839448984992",   "🐉"),
    "red_check"       : ("6129812419028982717",   "✅"),
    "green_check"     : ("6129982611055689014",   "✔️"),
    "pin"             : ("6131886699925438857",   "📌"),
    "bear"            : ("6129550284290006595",   "🐻"),
    "cancel"          : ("6129477982810545152",   "❌"),
    "phone"           : ("6127638387441838954",   "📱"),
    "verify_white"    : ("6127296324107769784",   "✔️"),
    "robot"           : ("6125264332130359953",   "🤖"),
    "leave_red"       : ("6129903231817488942",   "🚪"),
}


def E(name: str) -> str:
    """Return a premium tg-emoji HTML tag with plain-text fallback."""
    if name not in _PE:
        return ""
    eid, fallback = _PE[name]
    return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'


def h(text: str) -> str:
    """Escape text for HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ══════════════════════════════════════════════════════════════════════
#  KEYBOARDS  (buttons use plain emoji — tg-emoji only works in message text)
# ══════════════════════════════════════════════════════════════════════

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Add Account",   callback_data="add_account"),
         InlineKeyboardButton("✅ My Accounts",   callback_data="my_accounts")],
        [InlineKeyboardButton("⭐ New Campaign",  callback_data="new_campaign"),
         InlineKeyboardButton("💝 My Campaigns",  callback_data="my_campaigns")],
        [InlineKeyboardButton("📊 My Stats",      callback_data="my_stats"),
         InlineKeyboardButton("🦚 My Profile",    callback_data="my_profile")],
        [InlineKeyboardButton("⚡ Settings",      callback_data="settings"),
         InlineKeyboardButton("🔥 Help & Guide",  callback_data="help")],
    ])


def kb_campaign_action():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ React Only",          callback_data="camp_react"),
         InlineKeyboardButton("💝 Vote Only",           callback_data="camp_vote")],
        [InlineKeyboardButton("🐉 React + Vote",        callback_data="camp_react_vote"),
         InlineKeyboardButton("👑 View Only",           callback_data="camp_view")],
        [InlineKeyboardButton("⭐ React + View",        callback_data="camp_react_view"),
         InlineKeyboardButton("💝 Vote + View",         callback_data="camp_vote_view")],
        [InlineKeyboardButton("🐉 React + Vote + View", callback_data="camp_react_vote_view")],
        [InlineKeyboardButton("⚡ Join Channel",        callback_data="camp_join"),
         InlineKeyboardButton("🔥 Leave Channel",       callback_data="camp_leave")],
        [InlineKeyboardButton("📢 Bulk DM",             callback_data="camp_dm")],
        [InlineKeyboardButton("💰 Refer",               callback_data="camp_refer"),
         InlineKeyboardButton("⚡ Speed",               callback_data="camp_speed")],
        [InlineKeyboardButton("❌ Cancel",              callback_data="cancel")],
    ])


def kb_speed():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Very Fast  (0.5s/acc) ⚠️",  callback_data="speed_1")],
        [InlineKeyboardButton("⚡ Fast        (1.5s/acc)",     callback_data="speed_2")],
        [InlineKeyboardButton("✅ Normal      (3s/acc) ← default", callback_data="speed_3")],
        [InlineKeyboardButton("❄️ Slow        (5s/acc)",       callback_data="speed_4")],
        [InlineKeyboardButton("🛡️ Very Slow  (10s/acc) safe", callback_data="speed_5")],
        [InlineKeyboardButton("🏠 Back", callback_data="main_menu")],
    ])


def kb_emoji():
    emojis = ["👍", "❤️", "🔥", "🥰", "👏", "😁", "🎉", "🤩", "😱", "💯"]
    rows = [
        [InlineKeyboardButton(e, callback_data=f"emoji_{e}") for e in emojis[:5]],
        [InlineKeyboardButton(e, callback_data=f"emoji_{e}") for e in emojis[5:]],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(rows)


def kb_confirm(cid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Run Now", callback_data=f"run_{cid}"),
         InlineKeyboardButton("❌ Cancel",  callback_data="cancel")],
    ])


def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]])


# ══════════════════════════════════════════════════════════════════════
#  MAIN MENU
# ══════════════════════════════════════════════════════════════════════

async def show_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    name  = h(update.effective_user.first_name or "User")
    total = await db.count_active(uid)

    text = (
        f"{E('sparkle')} Welcome back, <b>{name}</b>!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{E('crown')} <b>Auto Voter</b>\n"
        f"<i>Telegram Automation Bot</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{E('star')} React  {E('heart')} Vote  {E('chart')} View  {E('lightning')} Join  {E('megaphone')} DM\n"
        f"<i>Fast, reliable &amp; smart Telegram automation</i>\n\n"
        f"{E('phone')} <b>{total}</b> active account(s) available.\n\n"
        f"Choose an option:"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
    else:
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )


# ══════════════════════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update, ctx)


# ══════════════════════════════════════════════════════════════════════
#  ADD ACCOUNT  — ConversationHandler
# ══════════════════════════════════════════════════════════════════════

async def h_add_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        f"{E('robot')} <b>Add New Account</b>\n\n"
        f"{E('phone')} Send your phone number with country code:\n"
        f"<code>+919876543210</code>\n\n"
        f"<i>/cancel to abort</i>",
        parse_mode=ParseMode.HTML
    )
    return ASK_PHONE


async def h_got_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    msg   = await update.message.reply_text(
        f"{E('cloud')} Sending OTP to <code>{h(phone)}</code>...",
        parse_mode=ParseMode.HTML
    )
    try:
        client, code_hash = await am.request_code(phone)
        ctx.user_data.update({"lc": client, "lp": phone, "lh": code_hash})
        await msg.edit_text(
            f"{E('check_green')} OTP sent!\n\n"
            f"Enter the code Telegram sent you:\n"
            f"Format: <code>12345</code>\n\n"
            f"<i>/cancel to abort</i>",
            parse_mode=ParseMode.HTML
        )
        return ASK_OTP
    except Exception as e:
        await msg.edit_text(
            f"{E('cancel')} Error: <code>{h(str(e))}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_back()
        )
        return ConversationHandler.END


async def h_got_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code   = update.message.text.strip().replace(" ", "")
    phone  = ctx.user_data["lp"]
    client = ctx.user_data["lc"]
    hash_  = ctx.user_data["lh"]
    try:
        session, info = await am.complete_login(client, phone, hash_, code)
        await db.save_account(update.effective_user.id, phone, session,
                              info["username"], info["first_name"])
        ctx.user_data.clear()
        await update.message.reply_text(
            f"{E('party')} <b>Account added successfully!</b>\n\n"
            f"{E('verify_white')} Name:  <b>{h(info['first_name'])}</b>\n"
            f"{E('phone')} Phone: <code>{h(phone)}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_back()
        )
        return ConversationHandler.END
    except SessionPasswordNeededError:
        await update.message.reply_text(
            f"{E('lock')} <b>2FA Enabled</b>\n\n"
            f"This account has Two-Factor Authentication.\n"
            f"Send your 2FA password:\n\n<i>/cancel to abort</i>",
            parse_mode=ParseMode.HTML
        )
        return ASK_2FA
    except PhoneCodeInvalidError:
        await update.message.reply_text(
            f"{E('cancel')} Wrong OTP. Try again or /cancel",
            parse_mode=ParseMode.HTML
        )
        return ASK_OTP
    except Exception as e:
        await update.message.reply_text(
            f"{E('cancel')} <code>{h(str(e))}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_back()
        )
        return ConversationHandler.END


async def h_got_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    phone  = ctx.user_data["lp"]
    client = ctx.user_data["lc"]
    try:
        session, info = await am.complete_2fa(client, update.message.text.strip())
        await db.save_account(update.effective_user.id, phone, session,
                              info["username"], info["first_name"])
        ctx.user_data.clear()
        await update.message.reply_text(
            f"{E('party')} <b>Account added (2FA)!</b>\n\n"
            f"{E('lock')} Name:  <b>{h(info['first_name'])}</b>\n"
            f"{E('phone')} Phone: <code>{h(phone)}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_back()
        )
    except Exception as e:
        await update.message.reply_text(
            f"{E('cancel')} Wrong password: <code>{h(str(e))}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_back()
        )
    return ConversationHandler.END


async def cancel_conv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    c = ctx.user_data.pop("lc", None)
    if c:
        try:
            await c.disconnect()
        except Exception:
            pass
    ctx.user_data.clear()
    if update.message:
        await update.message.reply_text(
            f"{E('cancel')} Cancelled.",
            parse_mode=ParseMode.HTML, reply_markup=kb_back()
        )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════
#  NEW CAMPAIGN
# ══════════════════════════════════════════════════════════════════════

async def h_new_campaign(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    await q.answer()
    total = await db.count_active(update.effective_user.id)
    await q.edit_message_text(
        f"{E('fire')} <b>New Campaign</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{E('phone')} <code>{total}</code> active account(s) available.\n\n"
        f"{E('pin')} <b>Step 1</b> — Choose what your accounts should do:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_campaign_action()
    )


async def h_camp_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query
    await q.answer()
    action = q.data.replace("camp_", "")

    if action == "speed":
        await q.edit_message_text(
            f"{E('lightning')} <b>Speed Settings</b>\n\n"
            f"Choose delay between each account action:\n"
            f"{E('warning')} <i>Faster = higher ban risk</i>",
            parse_mode=ParseMode.HTML, reply_markup=kb_speed()
        )
        return ConversationHandler.END

    ctx.user_data["ca"] = action
    ctx.user_data["ce"] = {}

    if "react" in action:
        await q.edit_message_text(
            f"{E('star')} <b>Choose Reaction Emoji</b>\n\n"
            f"Pick the emoji your accounts will react with:",
            parse_mode=ParseMode.HTML, reply_markup=kb_emoji()
        )
        return ASK_EMOJI

    await _ask_target_prompt(q, action)
    return ASK_CAMP_TARGET


async def h_emoji_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    await q.answer()
    emoji = q.data.replace("emoji_", "")
    ctx.user_data["ce"]["emoji"] = emoji
    await _ask_target_prompt(q, ctx.user_data["ca"])
    return ASK_CAMP_TARGET


async def _ask_target_prompt(q, action: str):
    if action in POST_ACTIONS:
        text = (
            f"{E('pin')} <b>Step 2 — Target Message</b>\n\n"
            f"Send the post link in this format:\n"
            f"<code>@channelusername/123</code>\n\n"
            f"<i>where 123 is the message ID</i>\n\n/cancel to abort"
        )
    elif action in CHAN_ACTIONS:
        text = (
            f"{E('pin')} <b>Step 2 — Target Channel</b>\n\n"
            f"Send the channel username or invite link:\n"
            f"<code>@username</code>  or  <code>https://t.me/+hash</code>\n\n/cancel to abort"
        )
    elif action == "refer":
        text = (
            f"{E('money')} <b>Step 2 — Referral Info</b>\n\n"
            f"Send in this format:\n"
            f"<code>@BotUsername referral_code</code>\n\n"
            f"Example: <code>@MyBot REF12345</code>\n\n/cancel to abort"
        )
    elif action == "dm":
        text = (
            f"{E('megaphone')} <b>Step 2 — Bulk DM Info</b>\n\n"
            f"Send in this format:\n"
            f"<code>@username Your message here</code>\n\n/cancel to abort"
        )
    else:
        text = f"{E('pin')} Send the target:\n\n/cancel to abort"
    await q.edit_message_text(text, parse_mode=ParseMode.HTML)


async def h_got_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text   = update.message.text.strip()
    action = ctx.user_data["ca"]

    if action == "refer":
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await update.message.reply_text(
                f"{E('cancel')} Format: <code>@BotUsername referral_code</code> — try again or /cancel",
                parse_mode=ParseMode.HTML
            )
            return ASK_CAMP_TARGET
        ctx.user_data["ct"] = parts[1]
        ctx.user_data["ce"]["bot_username"] = parts[0]
    elif action == "dm":
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await update.message.reply_text(
                f"{E('cancel')} Format: <code>@username message</code> — try again or /cancel",
                parse_mode=ParseMode.HTML
            )
            return ASK_CAMP_TARGET
        ctx.user_data["ct"] = parts[0]
        ctx.user_data["ce"]["message"] = parts[1]
    else:
        ctx.user_data["ct"] = text

    if "vote" in action:
        await update.message.reply_text(
            f"{E('heart')} <b>Poll Option Number</b>\n\n"
            f"Send the option index to vote for (starts at 0):\n"
            f"<code>0</code> = first option, <code>1</code> = second, etc.\n\n/cancel to abort",
            parse_mode=ParseMode.HTML
        )
        return ASK_VOTE_OPTION

    return await _build_confirm(update, ctx)


async def h_got_vote_option(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text(
            f"{E('cancel')} Send a number like <code>0</code>, <code>1</code>, <code>2</code> — try again:",
            parse_mode=ParseMode.HTML
        )
        return ASK_VOTE_OPTION
    ctx.user_data["ce"]["option_index"] = int(text)
    return await _build_confirm(update, ctx)


async def _build_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    action = ctx.user_data["ca"]
    target = ctx.user_data["ct"]
    extra  = ctx.user_data.get("ce", {})
    speed  = ctx.user_data.get("cs", 3)
    total  = await db.count_active(uid)

    cid = await db.create_campaign(uid, ACTION_LABELS.get(action, action),
                                   action, target, extra, total, speed)
    ctx.user_data["cid"] = cid

    details = ""
    if extra.get("emoji"):
        details += f"\n{E('star')} Emoji    : <code>{h(extra['emoji'])}</code>"
    if extra.get("option_index") is not None:
        details += f"\n{E('heart')} Vote opt : <code>{extra['option_index']}</code>"
    if extra.get("message"):
        details += f"\n{E('megaphone')} Message  : <code>{h(extra['message'][:40])}</code>"
    if extra.get("bot_username"):
        details += f"\n{E('money')} Bot      : <code>{h(extra['bot_username'])}</code>"

    await update.message.reply_text(
        f"{E('chart')} <b>Campaign Summary</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{E('fire')} Action   : <b>{h(ACTION_LABELS.get(action, action))}</b>\n"
        f"{E('pin')} Target   : <code>{h(target[:50])}</code>\n"
        f"{E('phone')} Accounts : <code>{total}</code>\n"
        f"{E('lightning')} Speed    : <code>{h(SPEED_LABELS.get(speed, 'Normal'))}</code>"
        f"{details}\n\n"
        f"{E('sparkle')} Ready to launch?",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_confirm(cid)
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════
#  RUN CAMPAIGN
# ══════════════════════════════════════════════════════════════════════

async def h_run_campaign(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    await q.answer()
    cid = int(q.data.replace("run_", ""))
    uid = update.effective_user.id

    camps    = await db.get_campaigns(uid)
    campaign = next((c for c in camps if c["id"] == cid), None)
    if not campaign:
        await q.edit_message_text(
            f"{E('cancel')} Campaign not found.",
            parse_mode=ParseMode.HTML, reply_markup=kb_back()
        )
        return

    accounts = await db.get_accounts(uid)
    if not accounts:
        await q.edit_message_text(
            f"{E('cancel')} No active accounts. Add accounts first.",
            parse_mode=ParseMode.HTML, reply_markup=kb_back()
        )
        return

    prog_msg = await q.edit_message_text(
        f"{E('cloud')} <b>Launching Campaign...</b>\n\n"
        f"{E('fire')} <code>{h(campaign['name'])}</code>\n"
        f"{E('phone')} 0 / {len(accounts)} done\n"
        f"{E('check_green')} 0  {E('cancel')} 0",
        parse_mode=ParseMode.HTML
    )

    extra  = json.loads(campaign.get("extra", "{}"))
    last_t = [0.0]

    async def on_progress(done, total, ok, fail):
        now = time.time()
        if now - last_t[0] < 2.5 and done < total:
            return
        last_t[0] = now
        try:
            await prog_msg.edit_text(
                f"{E('lightning')} <b>Running...</b>\n\n"
                f"{E('fire')} <code>{h(campaign['name'])}</code>\n"
                f"{E('phone')} {done} / {total} done\n"
                f"{E('check_green')} {ok}  {E('cancel')} {fail}",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

    ok, fail = await am.run_campaign(
        accounts, campaign["action"], campaign["target"],
        extra, campaign.get("speed", 3), on_progress
    )
    await db.finish_campaign(cid, ok, fail)

    await prog_msg.edit_text(
        f"{E('party')} <b>Campaign Completed!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{E('fire')} <code>{h(campaign['name'])}</code>\n\n"
        f"{E('phone')} Total    : <code>{len(accounts)}</code>\n"
        f"{E('check_green')} Success  : <code>{ok}</code>\n"
        f"{E('cancel')} Failed   : <code>{fail}</code>",
        parse_mode=ParseMode.HTML, reply_markup=kb_back()
    )


# ══════════════════════════════════════════════════════════════════════
#  MY ACCOUNTS
# ══════════════════════════════════════════════════════════════════════

async def h_my_accounts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    accounts = await db.get_accounts(update.effective_user.id, active_only=False)

    if not accounts:
        await q.edit_message_text(
            f"{E('cancel')} <b>No accounts added yet.</b>\n\nUse <b>Add Account</b> to get started.",
            parse_mode=ParseMode.HTML, reply_markup=kb_back()
        )
        return

    lines = []
    for i, a in enumerate(accounts, 1):
        icon  = E("check_green") if a["is_active"] else E("cancel")
        name  = h(a.get("first_name") or "Unknown")
        phone = h(a["phone"])
        uname = f"@{h(a['username'])}" if a.get("username") else "—"
        lines.append(f"{i}. {icon} <b>{name}</b> | <code>{phone}</code> | {uname}")

    rows = []
    for a in accounts:
        label = f"🗑️ {a.get('first_name') or a['phone']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"rm_{a['phone']}")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])

    await q.edit_message_text(
        f"{E('phone')} <b>My Accounts</b> ({len(accounts)} total)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows)
    )


async def h_remove_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    await q.answer()
    phone = q.data[3:]
    await db.remove_account(update.effective_user.id, phone)
    await q.edit_message_text(
        f"{E('cancel')} Account <code>{h(phone)}</code> removed.",
        parse_mode=ParseMode.HTML, reply_markup=kb_back()
    )


# ══════════════════════════════════════════════════════════════════════
#  MY CAMPAIGNS
# ══════════════════════════════════════════════════════════════════════

async def h_my_campaigns(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    await q.answer()
    camps = await db.get_campaigns(update.effective_user.id)

    if not camps:
        await q.edit_message_text(
            f"{E('cancel')} No campaigns yet. Start one from <b>New Campaign</b>.",
            parse_mode=ParseMode.HTML, reply_markup=kb_back()
        )
        return

    lines = []
    for c in camps[:10]:
        icon = E("check_green") if c["status"] == "completed" else E("cloud")
        name = h(ACTION_LABELS.get(c["action"], c["action"]))
        tgt  = h(c["target"][:28])
        lines.append(
            f"{icon} <b>{name}</b>\n"
            f"   <code>{tgt}</code>  "
            f"{E('check_green')}{c['success']}  {E('cancel')}{c['fail']}"
        )

    await q.edit_message_text(
        f"{E('gift')} <b>My Campaigns</b> (last 10)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n\n".join(lines),
        parse_mode=ParseMode.HTML, reply_markup=kb_back()
    )


# ══════════════════════════════════════════════════════════════════════
#  MY STATS
# ══════════════════════════════════════════════════════════════════════

async def h_my_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    await q.answer()
    stats = await db.get_stats(update.effective_user.id)
    total = await db.count_active(update.effective_user.id)

    if not stats:
        await q.edit_message_text(
            f"{E('chart')} No stats yet. Run a campaign first.",
            parse_mode=ParseMode.HTML, reply_markup=kb_back()
        )
        return

    lines = [f"{E('phone')} Active accounts: <code>{total}</code>\n"]
    for action, s in stats.items():
        label = h(ACTION_LABELS.get(action, action))
        lines.append(
            f"{E('fire')} <b>{label}</b>\n"
            f"   Runs <code>{s['runs']}</code> | "
            f"{E('check_green')} <code>{s['success']}</code>  "
            f"{E('cancel')} <code>{s['fail']}</code>"
        )

    await q.edit_message_text(
        f"{E('chart')} <b>My Stats</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n\n".join(lines),
        parse_mode=ParseMode.HTML, reply_markup=kb_back()
    )


# ══════════════════════════════════════════════════════════════════════
#  MY PROFILE
# ══════════════════════════════════════════════════════════════════════

async def h_my_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    await q.answer()
    uid   = update.effective_user.id
    user  = update.effective_user
    acc   = await db.count_active(uid)
    camps = await db.get_campaigns(uid)
    await q.edit_message_text(
        f"{E('peacock')} <b>My Profile</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{E('sparkle')} Name      : <b>{h(user.first_name or 'Unknown')}</b>\n"
        f"{E('verify_white')} User ID   : <code>{uid}</code>\n"
        f"{E('phone')} Accounts  : <code>{acc}</code> active\n"
        f"{E('gift')} Campaigns : <code>{len(camps)}</code> total",
        parse_mode=ParseMode.HTML, reply_markup=kb_back()
    )


# ══════════════════════════════════════════════════════════════════════
#  SETTINGS
# ══════════════════════════════════════════════════════════════════════

async def h_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    await q.answer()
    speed = ctx.user_data.get("cs", 3)
    await q.edit_message_text(
        f"{E('lightning')} <b>Settings</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Current speed: <b>{h(SPEED_LABELS.get(speed, 'Normal'))}</b>\n\n"
        f"Select new campaign execution speed:",
        parse_mode=ParseMode.HTML, reply_markup=kb_speed()
    )


async def h_speed_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    await q.answer()
    level = int(q.data.replace("speed_", ""))
    ctx.user_data["cs"] = level
    icons = {1: E("fire"), 2: E("lightning"), 3: E("check_green"), 4: E("ice"), 5: E("ice")}
    await q.edit_message_text(
        f"{icons.get(level, E('check_green'))} Speed set to <b>{h(SPEED_LABELS[level])}</b>\n\n"
        f"This will apply to your next campaign.",
        parse_mode=ParseMode.HTML, reply_markup=kb_back()
    )


# ══════════════════════════════════════════════════════════════════════
#  HELP
# ══════════════════════════════════════════════════════════════════════

async def h_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        f"{E('bear')} <b>Help &amp; Guide</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{E('robot')} <b>1. Add Account</b>\n"
        f"   Login with phone + OTP (2FA supported)\n\n"
        f"{E('fire')} <b>2. New Campaign</b>\n"
        f"   Choose an action, set target, launch!\n\n"
        f"{E('pin')} <b>3. Target Formats</b>\n"
        f"   • Post: <code>@channel/message_id</code>\n"
        f"   • Channel: <code>@username</code> or invite link\n"
        f"   • Refer: <code>@BotUsername refcode</code>\n"
        f"   • DM: <code>@username your message</code>\n\n"
        f"{E('lightning')} <b>4. Speed</b>\n"
        f"   Controls delay between accounts\n"
        f"   {E('warning')} Faster = higher ban risk\n\n"
        f"{E('sparkle')} <b>5. Tips</b>\n"
        f"   — Use Normal or Slow speed for safety\n"
        f"   — Don't add too many accounts at once\n"
        f"   — Keep your session file secure",
        parse_mode=ParseMode.HTML, reply_markup=kb_back()
    )


# ══════════════════════════════════════════════════════════════════════
#  MISC CALLBACKS
# ══════════════════════════════════════════════════════════════════════

async def h_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await show_main_menu(update, ctx)


async def h_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        f"{E('cancel')} Cancelled.",
        parse_mode=ParseMode.HTML, reply_markup=kb_back()
    )


# ══════════════════════════════════════════════════════════════════════
#  APP SETUP
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
#  APP SETUP
# ══════════════════════════════════════════════════════════════════════

def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))

    # ── Login flow ─────────────────────────────────────────────────────
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(h_add_account, pattern="^add_account$")],
        states={
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, h_got_phone)],
            ASK_OTP:   [MessageHandler(filters.TEXT & ~filters.COMMAND, h_got_otp)],
            ASK_2FA:   [MessageHandler(filters.TEXT & ~filters.COMMAND, h_got_2fa)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        allow_reentry=True,
        per_message=False,
    ))

    # ── Campaign flow ──────────────────────────────────────────────────
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(h_camp_action, pattern="^camp_")],
        states={
            ASK_EMOJI:       [CallbackQueryHandler(h_emoji_pick,      pattern="^emoji_")],
            ASK_CAMP_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, h_got_target)],
            ASK_VOTE_OPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, h_got_vote_option)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        allow_reentry=True,
        per_message=False,
    ))

    # ── Button callbacks ───────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(h_main_menu,      pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(h_new_campaign,   pattern="^new_campaign$"))
    app.add_handler(CallbackQueryHandler(h_my_accounts,    pattern="^my_accounts$"))
    app.add_handler(CallbackQueryHandler(h_remove_account, pattern="^rm_"))
    app.add_handler(CallbackQueryHandler(h_my_campaigns,   pattern="^my_campaigns$"))
    app.add_handler(CallbackQueryHandler(h_my_stats,       pattern="^my_stats$"))
    app.add_handler(CallbackQueryHandler(h_my_profile,     pattern="^my_profile$"))
    app.add_handler(CallbackQueryHandler(h_settings,       pattern="^settings$"))
    app.add_handler(CallbackQueryHandler(h_speed_set,      pattern="^speed_[1-5]$"))
    app.add_handler(CallbackQueryHandler(h_help,           pattern="^help$"))
    app.add_handler(CallbackQueryHandler(h_run_campaign,   pattern=r"^run_\d+$"))
    app.add_handler(CallbackQueryHandler(h_cancel,         pattern="^cancel$"))

    return app


# ══════════════════════════════════════════════════════════════════════
#  ENTRY POINT
#  We drive the bot manually with asyncio.run() to avoid the
#  asyncio.get_event_loop() removal in Python 3.14 that breaks
#  PTB's synchronous run_polling() wrapper.
# ══════════════════════════════════════════════════════════════════════

async def main() -> None:
    await db.init_db()
    log.info("Database ready.")

    app = build_app()

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    log.info("Bot is running. Press Ctrl+C to stop.")

    # Block until SIGINT / SIGTERM (works on Linux/Render and Windows)
    stop_event = asyncio.Event()

    import signal, sys

    def _request_stop(*_):
        stop_event.set()

    # Register OS signals only on platforms that support it (Linux/Mac)
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_stop)

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        log.info("Shutting down...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    log.info("Auto Voter Bot starting...")
    asyncio.run(main())
