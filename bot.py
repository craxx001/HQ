import asyncio
import html
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest, Forbidden

load_dotenv()

# ============================================================
# CONFIG - same style/flow as main1.py (python-telegram-bot)
# ============================================================
TOKEN = os.getenv("TELEGRAM_TOKEN", os.getenv("BOT_TOKEN", "")).strip()
DB_FILE = Path(os.getenv("DB_FILE", os.getenv("DATA_FILE", "database.json")))
OWNER_IDS = [
    int(x.strip())
    for x in os.getenv("OWNER_IDS", "8853678390").split(",")
    if x.strip().isdigit()
]
if 8853678390 not in OWNER_IDS:
    OWNER_IDS.insert(0, 8853678390)

DEFAULT_VERIFY = {
    "name": "✅ VERIFY",
    "url": "https://vplink.in/HQ-FREE-LIKES",
    "active": True,
}
DEFAULT_STORE = {
    "name": "🛒 STORE",
    "url": "https://t.me/princezz_bot?start",
}
DEFAULT_HOWTO = {
    "name": "HOW TO VERIFY ❓",
    "url": "https://t.me/+pOFsX6VyngU4MTc9",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("like-verification-bot")


def new_db():
    return {
        "config": {
            "verify": dict(DEFAULT_VERIFY),
            "store": dict(DEFAULT_STORE),
            "howto": dict(DEFAULT_HOWTO),
        },
        "admins": list(OWNER_IDS),
        "users": {},
        "verify_clicks": [],
    }


def deep_merge(default, existing):
    if isinstance(default, dict):
        out = dict(default)
        if isinstance(existing, dict):
            for k, v in existing.items():
                if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                    out[k] = deep_merge(out[k], v)
                else:
                    out[k] = v
        return out
    return existing if existing is not None else default


def load_db():
    if not DB_FILE.exists():
        data = new_db()
        save_db(data)
        return data
    try:
        data = json.loads(DB_FILE.read_text(encoding="utf-8"))
        data = deep_merge(new_db(), data)
    except (json.JSONDecodeError, OSError):
        data = new_db()

    # Always keep the owner admin.
    admins = []
    for x in data.get("admins", []):
        try:
            admins.append(int(x))
        except (TypeError, ValueError):
            pass
    for owner in OWNER_IDS:
        if owner not in admins:
            admins.append(owner)
    data["admins"] = admins

    # Repair malformed settings without deleting valid user data.
    for key, default in (("verify", DEFAULT_VERIFY), ("store", DEFAULT_STORE), ("howto", DEFAULT_HOWTO)):
        item = data.setdefault("config", {}).get(key)
        if not isinstance(item, dict):
            data["config"][key] = dict(default)
        else:
            data["config"][key] = deep_merge(default, item)

    data.setdefault("users", {})
    data.setdefault("verify_clicks", [])
    return data


def save_db(data=None):
    data = data if data is not None else db
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DB_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DB_FILE)


db = load_db()
save_db()


def is_admin(user_id: int) -> bool:
    try:
        return int(user_id) in {int(x) for x in db.get("admins", [])}
    except Exception:
        return False


def remember_user(user):
    if not user:
        return
    uid = str(user.id)
    old = db.setdefault("users", {}).get(uid, {})
    db["users"][uid] = {
        "id": user.id,
        "name": user.full_name or user.first_name or old.get("name", "User"),
        "username": user.username or old.get("username", ""),
    }
    save_db()


def user_id_list():
    out = []
    for x in db.get("verify_clicks", []):
        try:
            x = int(x)
        except (TypeError, ValueError):
            continue
        if x not in out:
            out.append(x)
    return out


def valid_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def valid_like_region(region: str) -> bool:
    # Keep region flexible like the screenshots: ind is the normal example,
    # while admin can still configure links independently of region.
    return bool(re.fullmatch(r"[A-Za-z]{2,12}", region.strip()))


def valid_uid(uid: str) -> bool:
    return bool(re.fullmatch(r"\d{5,15}", uid.strip()))


# ============================================================
# KEYBOARDS
# ============================================================
def styled_url_button(text, url, style):
    # PTB 22.5 does not expose the newer `style=` constructor argument yet,
    # but it supports `api_kwargs`. Telegram Bot API now accepts the style
    # field there, so we can keep the project pinned to PTB 22.5 while still
    # getting the real Telegram button colours.
    return InlineKeyboardButton(
        text,
        url=url,
        api_kwargs={"style": style},
    )


def verify_keyboard():
    cfg = db["config"]
    v = cfg["verify"]
    s = cfg["store"]
    h = cfg["howto"]
    # Direct URL buttons: tapping them opens the link directly, without a
    # callback popup/alert.
    return InlineKeyboardMarkup([
        [
            styled_url_button(
                v.get("name", DEFAULT_VERIFY["name"]),
                v.get("url", DEFAULT_VERIFY["url"]),
                "success",
            ),
            styled_url_button(
                s.get("name", DEFAULT_STORE["name"]),
                s.get("url", DEFAULT_STORE["url"]),
                "success",
            ),
        ],
        [styled_url_button(
            h.get("name", DEFAULT_HOWTO["name"]),
            h.get("url", DEFAULT_HOWTO["url"]),
            "primary",
        )],
    ])


def admin_main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 VERIFY LINK", callback_data="adm_verify")],
        [InlineKeyboardButton("🛒 STORE", callback_data="adm_store")],
        [InlineKeyboardButton("HOW TO VERIFY ❓", callback_data="adm_howto")],
        [InlineKeyboardButton("👥 USERS", callback_data="adm_users")],
        [InlineKeyboardButton("👮 ADMINS", callback_data="adm_admins")],
    ])


def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔴 BACK", callback_data="adm_back")]])


def settings_kb(kind):
    rows = [
        [InlineKeyboardButton("✏️ Edit Button Name", callback_data=f"edit_name:{kind}")],
        [InlineKeyboardButton("🔗 Set / Edit Link", callback_data=f"edit_url:{kind}")],
    ]
    if kind == "verify":
        rows += [
            [
                InlineKeyboardButton("➕ NEW / ADD", callback_data="verify_new"),
                InlineKeyboardButton("🗑 DELETE", callback_data="verify_delete"),
            ],
            [
                InlineKeyboardButton("🟢 ACTIVE", callback_data="verify_active"),
                InlineKeyboardButton("⚫ INACTIVE", callback_data="verify_inactive"),
            ],
        ]
    rows.append([InlineKeyboardButton("🔴 BACK", callback_data="adm_back")])
    return InlineKeyboardMarkup(rows)


def users_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔢 NUM LIST", callback_data="users_num")],
        [InlineKeyboardButton("📋 USER LIST", callback_data="users_list")],
        [InlineKeyboardButton("🔴 BACK", callback_data="adm_back")],
    ])


def admins_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ADD ADMIN", callback_data="admin_add")],
        [InlineKeyboardButton("➖ REMOVE ADMIN", callback_data="admin_remove")],
        [InlineKeyboardButton("📋 ADMIN LIST", callback_data="admin_list")],
        [InlineKeyboardButton("🔴 BACK", callback_data="adm_back")],
    ])


def set_state(context, state):
    context.user_data["admin_state"] = state


def get_state(context):
    return context.user_data.get("admin_state")


def clear_state(context):
    context.user_data.pop("admin_state", None)


# ============================================================
# MESSAGE HELPERS
# ============================================================
async def safe_delete(message):
    if not message:
        return
    try:
        await message.delete()
    except (BadRequest, Forbidden):
        pass
    except Exception as exc:
        log.debug("Delete failed: %s", exc)


async def temporary_warning(message, text, seconds=3):
    try:
        sent = await message.reply_text(text, parse_mode="HTML")
        await asyncio.sleep(seconds)
        await safe_delete(sent)
    except Exception as exc:
        log.debug("Temporary warning failed: %s", exc)


async def invalid_format(message):
    sent = await message.reply_text(
        "❌ <b>ɪɴᴠᴀʟɪᴅ ᴜꜱᴀɢᴇ</b>\n\n"
        "✅ <b>ᴄᴏʀʀᴇᴄᴛ:</b> /like <region> <uid>\n""
        "📌 <b>ᴇxᴀᴍᴘʟᴇ:</b> /like IND 12345609",
        parse_mode="HTML",
    )
    # Keep it visible, as in the reference screenshot. Telegram command
    # messages themselves are not deleted here.
    return sent


# ============================================================
# /LIKE FLOW
# ============================================================
async def cmd_like(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.from_user:
        return

    remember_user(message.from_user)
    args = context.args

    if len(args) != 2 or not valid_like_region(args[0]) or not valid_uid(args[1]):
        await invalid_format(message)
        return

    region = args[0].upper()
    uid = args[1]

    processing = await message.reply_text(
        "⚡ <b>Vɛʀɪꜰʏɪɴɢ ʏᴏᴜʀ ʟɪᴋᴇ ʀᴇǫᴜᴇꜱᴛ...</b>\n\n"
        "⌛ ɢᴇɴᴇʀᴀᴛɪɴɢ ʟɪɴᴋ...",
        parse_mode="HTML",
    )

    # Same edit-based flow as the reference project: one message is changed
    # instead of sending a second verification message.
    await asyncio.sleep(2)

    verify = db["config"]["verify"]
    if not verify.get("active") or not verify.get("url"):
        await processing.edit_text(
            "⚠️ <b>Vᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ɪꜱ ᴄᴜʀʀᴇɴᴛʟʏ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ!</b>\n\nPlease try again later.",
            parse_mode="HTML",
        )
        return

    name = html.escape(message.from_user.full_name or message.from_user.first_name or "User")
    url = verify["url"]
    text = (
        "🎮 <b>LIKE REQUEST VERIFICATION</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>ɴᴀᴍɛ:</b> {name}\n"
        f"🆔 <b>ᴜɪᴅ:</b> <code>{uid}</code>\n"
        f"🌍 <b>ʀɛɢɪᴏɴ:</b> <code>{region}</code>\n\n"
        "🔗 <b>Vɛʀɪꜰɪᴄᴀᴛɪᴏɴ Lɪɴᴋ:</b>\n"
        f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>\n\n'
        "⚠️ <b>ᴠᴇʀɪꜰʏ ᴛʜɪꜱ ʟɪɴᴋ ᴛᴏ ɢᴇᴛ ʟɪᴋᴇꜱ</b>"
    )

    await processing.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=verify_keyboard(),
        disable_web_page_preview=True,
    )


# ============================================================
# /ADMIN
# ============================================================
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.from_user:
        return

    if not is_admin(message.from_user.id):
        await temporary_warning(message, "🚫 <b>Admins only</b>", 3)
        return

    remember_user(message.from_user)
    clear_state(context)
    await message.reply_text(
        "👋 <b>Welcome Admins!!</b>\n\nᴄʜᴏᴏꜱᴇ ᴀɴ ᴏᴘᴛɪᴏɴ :",
        parse_mode="HTML",
        reply_markup=admin_main_kb(),
    )


# ============================================================
# ADMIN TEXT INPUT
# ============================================================
async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    message = update.message
    state = get_state(context)
    if not state:
        return False

    action = state.get("action")

    if action == "set_name":
        kind = state["kind"]
        value = text.strip()
        if not value:
            await temporary_warning(message, "❌ Button name cannot be empty.", 3)
            return True
        db["config"][kind]["name"] = value[:64]
        save_db()
        clear_state(context)
        await message.reply_text(
            f"✅ <b>{kind.upper()}</b> button name updated.\n\n"
            f"New name: <b>{html.escape(value[:64])}</b>",
            parse_mode="HTML",
            reply_markup=settings_kb(kind),
        )
        return True

    if action == "set_url":
        kind = state["kind"]
        url = text.strip()
        if not valid_url(url):
            await temporary_warning(message, "❌ Invalid link. Use http:// or https://", 3)
            return True
        db["config"][kind]["url"] = url
        save_db()
        clear_state(context)
        await message.reply_text(
            f"✅ <b>{kind.upper()}</b> link updated.",
            parse_mode="HTML",
            reply_markup=settings_kb(kind),
        )
        return True

    if action == "add_admin":
        if not text.isdigit():
            await temporary_warning(message, "❌ Send a numeric Telegram user ID.", 3)
            return True
        target = int(text)
        if target not in db["admins"]:
            db["admins"].append(target)
            save_db()
        clear_state(context)
        await message.reply_text(
            f"✅ <code>{target}</code> is now an admin.",
            parse_mode="HTML",
            reply_markup=admins_kb(),
        )
        return True

    if action == "remove_admin":
        if not text.isdigit():
            await temporary_warning(message, "❌ Send a numeric Telegram user ID.", 3)
            return True
        target = int(text)
        if target in OWNER_IDS:
            reply = "⚠️ Owner admin cannot be removed."
        else:
            db["admins"] = [x for x in db["admins"] if int(x) != target]
            save_db()
            reply = f"✅ <code>{target}</code> removed from admins."
        clear_state(context)
        await message.reply_text(reply, parse_mode="HTML", reply_markup=admins_kb())
        return True

    return False


# ============================================================
# ALL NON-COMMAND MESSAGES
# ============================================================
async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.from_user:
        return

    remember_user(message.from_user)

    # Admin setting input must be processed before group moderation, otherwise
    # the newly entered link/name would be deleted as a normal group message.
    if is_admin(message.from_user.id) and get_state(context):
        if not message.text:
            await temporary_warning(message, "❌ Please send text only.", 3)
            return
        if await handle_admin_text(update, context, message.text.strip()):
            return

    # Only the group/supergroup is command-only. Private chat remains usable
    # for admin configuration and future additions.
    if message.chat and message.chat.type in ("group", "supergroup"):
        # Delete the user's ordinary message FIRST. Then send a separate
        # warning message and remove that warning after 3 seconds. This avoids
        # leaving the user's original message visible while the warning is up.
        try:
            chat_id = message.chat.id
            await safe_delete(message)
            name = html.escape(message.from_user.full_name or message.from_user.first_name or "User")
            warning = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "💞 <b>WELCOME TO FREE LIKE BOT</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    "<blockquote>"
                    "👤 <b>ɴᴀᴍɛ</b>» {name}\n"
                    "⚡ <b>ᴘʟᴀɴ</b>» ꜰʀᴇᴇ ᴜꜱᴇʀ\n"
                    "❤️ <b>ᴅᴀɪʟʏ ʟɪᴍɪᴛ</b>» 𝟣\n"
                    "🕓 <b>ᴅᴀɪʟʏ ʀᴇꜱᴇᴛ</b>» 𝟦:𝟢𝟢 ᴀᴍ\n"
                    "</blockquote>\n"
                    "<b>ᴜsᴀɢᴇ:</b>\n"
                    "🎁 <code>/like {region} {uid}</code> -\n\n"
                    "<b>ᴇxᴀᴍᴘʟᴇ:</b>\n"
                    "📌 <code>/like ind 12345609</code>"
                ),
                parse_mode="HTML",
            )
            await asyncio.sleep(3)
            await safe_delete(warning)
        except Exception as exc:
            log.debug("Group moderation failed: %s", exc)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    # /admin and /like have their own handlers. Every other command is treated
    # exactly like the invalid format flow.
    await invalid_format(update.message)


# ============================================================
# CALLBACKS
# ============================================================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    data = q.data or ""

    # All remaining callbacks are admin-only.
    if not is_admin(q.from_user.id):
        await q.answer("🚫 Admins only", show_alert=True)
        return

    await q.answer()

    if data == "adm_back":
        clear_state(context)
        await q.edit_message_text(
            "👋 <b>Welcome Admins!!</b>\n\nChoose an option:",
            parse_mode="HTML",
            reply_markup=admin_main_kb(),
        )
        return

    if data == "adm_verify":
        v = db["config"]["verify"]
        status = "🟢 Active" if v.get("active") else "⚫ Inactive"
        await q.edit_message_text(
            "🔗 <b>VERIFY LINK SETTINGS</b>\n\n"
            f"Button: <b>{html.escape(str(v.get('name', '')))}</b>\n"
            f"Link: <code>{html.escape(str(v.get('url') or 'Not set'))}</code>\n"
            f"Status: {status}",
            parse_mode="HTML",
            reply_markup=settings_kb("verify"),
        )
        return

    if data in ("adm_store", "adm_howto"):
        kind = "store" if data == "adm_store" else "howto"
        item = db["config"][kind]
        title = "🛒 STORE SETTINGS" if kind == "store" else "HOW TO VERIFY SETTINGS"
        await q.edit_message_text(
            f"<b>{title}</b>\n\n"
            f"Button: <b>{html.escape(str(item.get('name', '')))}</b>\n"
            f"Link: <code>{html.escape(str(item.get('url', '')))}</code>",
            parse_mode="HTML",
            reply_markup=settings_kb(kind),
        )
        return

    if data == "adm_users":
        await q.edit_message_text(
            "👥 <b>USERS</b>\n\nChoose an option:",
            parse_mode="HTML",
            reply_markup=users_kb(),
        )
        return

    if data == "users_num":
        await q.edit_message_text(
            f"🔢 <b>Total {len(user_id_list())}</b>\n\n"
            "Users counted here are users who clicked VERIFY.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )
        return

    if data == "users_list":
        ids = user_id_list()
        if not ids:
            body = "📋 <b>User List</b>\n\nNo users have clicked VERIFY yet."
        else:
            body = "📋 <b>User List</b>\n\n" + "\n".join(
                f"{i}. <code>{uid}</code>" for i, uid in enumerate(ids, 1)
            )
        # Telegram message text limit is 4096; leave some room for formatting.
        await q.edit_message_text(body[:3900], parse_mode="HTML", reply_markup=back_kb())
        return

    if data == "adm_admins":
        await q.edit_message_text(
            "👮 <b>ADMINS</b>\n\nManage admin IDs below.",
            parse_mode="HTML",
            reply_markup=admins_kb(),
        )
        return

    if data == "admin_add":
        set_state(context, {"action": "add_admin"})
        await q.edit_message_text(
            "➕ <b>ADD ADMIN</b>\n\nSend the numeric Telegram user ID.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )
        return

    if data == "admin_remove":
        set_state(context, {"action": "remove_admin"})
        await q.edit_message_text(
            "➖ <b>REMOVE ADMIN</b>\n\nSend the numeric Telegram user ID.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )
        return

    if data == "admin_list":
        ids = db.get("admins", [])
        body = "👮 <b>Admin List</b>\n\n" + "\n".join(
            f"{i}. <code>{int(uid)}</code>" for i, uid in enumerate(ids, 1)
        )
        await q.edit_message_text(body, parse_mode="HTML", reply_markup=back_kb())
        return

    if data.startswith("edit_name:"):
        kind = data.split(":", 1)[1]
        if kind not in ("verify", "store", "howto"):
            return
        set_state(context, {"action": "set_name", "kind": kind})
        await q.edit_message_text(
            f"✏️ <b>Edit {kind.upper()} button name</b>\n\nSend the new button text.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )
        return

    if data.startswith("edit_url:"):
        kind = data.split(":", 1)[1]
        if kind not in ("verify", "store", "howto"):
            return
        set_state(context, {"action": "set_url", "kind": kind})
        await q.edit_message_text(
            f"🔗 <b>Set {kind.upper()} link</b>\n\nSend the new http/https link.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )
        return

    if data == "verify_new":
        db["config"]["verify"] = dict(DEFAULT_VERIFY)
        save_db()
        await q.edit_message_text(
            "➕ <b>New VERIFY configuration created/reset.</b>",
            parse_mode="HTML",
            reply_markup=settings_kb("verify"),
        )
        return

    if data == "verify_delete":
        db["config"]["verify"] = {
            "name": DEFAULT_VERIFY["name"],
            "url": "",
            "active": False,
        }
        save_db()
        await q.edit_message_text(
            "🗑 <b>VERIFY link deleted.</b>\n\nSet a new link before activating it.",
            parse_mode="HTML",
            reply_markup=settings_kb("verify"),
        )
        return

    if data == "verify_active":
        if not db["config"]["verify"].get("url"):
            await q.answer("Set a verification link first.", show_alert=True)
            return
        db["config"]["verify"]["active"] = True
        save_db()
        await q.edit_message_text(
            "🟢 <b>VERIFY is now active.</b>",
            parse_mode="HTML",
            reply_markup=settings_kb("verify"),
        )
        return

    if data == "verify_inactive":
        db["config"]["verify"]["active"] = False
        save_db()
        await q.edit_message_text(
            "⚫ <b>VERIFY is now inactive.</b>",
            parse_mode="HTML",
            reply_markup=settings_kb("verify"),
        )
        return


# ============================================================
# START / HELP / POST INIT
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        remember_user(update.effective_user)
    await update.message.reply_text(
        "💞 <b>WELCOME TO FREE LIKE BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        "👤 <b>ɴᴀᴍɛ</b>» {name}\n"
        "⚡ <b>ᴘʟᴀɴ</b>» ꜰʀᴇᴇ ᴜꜱᴇʀ\n"
        "❤️ <b>ᴅᴀɪʟʏ ʟɪᴍɪᴛ</b>» 𝟣\n"
        "🕓 <b>ᴅᴀɪʟʏ ʀᴇꜱᴇᴛ</b>» 𝟦:𝟢𝟢 ᴀᴍ\n"
        "</blockquote>\n"
        "<b>Usage:</b>\n"
        "🎁 <code>/like {region} {uid}</code> -\n\n"
        "<b>Example:</b>\n"
        "📌 <code>/like ind 12345609</code>",
        parse_mode="HTML",
    )


async def post_init(application: Application):
    # Match main1.py's PTB startup flow and command registration.
    commands = [
        BotCommand("start", "Open bot"),
        BotCommand("like", "Send like verification"),
    ]
    await application.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    for admin_id in db.get("admins", []):
        try:
            await application.bot.set_my_commands(
                commands + [BotCommand("admin", "Open Admin Panel")],
                scope=BotCommandScopeChat(chat_id=int(admin_id)),
            )
        except Exception:
            pass
    log.info("Like verification bot online")


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set in .env")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    # Command handlers first, like main1.py.
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("like", cmd_like))
    app.add_handler(CommandHandler("admin", cmd_admin))

    # Unknown command -> invalid format.
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # Callback router handles VERIFY + admin menus.
    app.add_handler(CallbackQueryHandler(callback_router))

    # All ordinary messages go through one router. Admin input states are
    # handled before group moderation, then ordinary group messages are deleted.
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_router))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
