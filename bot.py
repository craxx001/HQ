import asyncio
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = 8853678390

DATA_FILE = Path(os.getenv("DATA_FILE", "data.json"))
DEFAULT_VERIFY_NAME = "✅ VERIFY"
DEFAULT_VERIFY_URL = "https://vplink.in/HQ-FREE-LIKES"
DEFAULT_STORE_NAME = "🛒 STORE"
DEFAULT_STORE_URL = "https://t.me/princezz_bot?start"
DEFAULT_HOW_NAME = "HOW TO VERIFY ❓"
DEFAULT_HOW_URL = "https://t.me/+pOFsX6VyngU4MTc9"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("like-bot")


def default_db():
    return {
        "admins": [OWNER_ID],
        "verify": {
            "name": DEFAULT_VERIFY_NAME,
            "url": DEFAULT_VERIFY_URL,
            "active": True,
        },
        "store": {"name": DEFAULT_STORE_NAME, "url": DEFAULT_STORE_URL},
        "howto": {"name": DEFAULT_HOW_NAME, "url": DEFAULT_HOW_URL},
        "users": {},              # user_id -> basic profile
        "verify_clicks": [],      # unique user IDs who clicked VERIFY
        "states": {},             # admin UI state
    }


def load_db():
    if not DATA_FILE.exists():
        db = default_db()
        save_db(db)
        return db
    try:
        db = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        db = default_db()

    # Merge missing top-level/default keys without overwriting existing settings.
    defaults = default_db()
    for k, v in defaults.items():
        if k not in db:
            db[k] = v
    if OWNER_ID not in db["admins"]:
        db["admins"].append(OWNER_ID)
    return db


def save_db(db):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


db = load_db()


def is_admin(user_id: int) -> bool:
    return user_id in set(map(int, db.get("admins", [])))


def remember_user(user):
    uid = str(user.id)
    db.setdefault("users", {})[uid] = {
        "id": user.id,
        "name": user.full_name,
        "username": user.username,
    }
    save_db(db)


def valid_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def mention(user):
    name = (user.full_name or "User").replace("<", "&lt;").replace(">", "&gt;")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def like_help():
    return (
        "❌ <b>Invalid Format</b>\n\n"
        "📝 <b>Usage:</b> /like [region] [uid]\n\n"
        "📌 <b>Example:</b> /like ind 12234555"
    )


def verify_keyboard():
    v = db["verify"]
    s = db["store"]
    h = db["howto"]
    verify_button = InlineKeyboardButton(
        text=v["name"],
        callback_data="verify_click"
    )
    store_button = InlineKeyboardButton(
        text=s["name"],
        url=s["url"]
    )
    how_button = InlineKeyboardButton(
        text=h["name"],
        url=h["url"]
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [verify_button, store_button],
        [how_button],
    ])


def admin_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 VERIFY LINK", callback_data="adm_verify")],
        [InlineKeyboardButton(text="🛒 STORE", callback_data="adm_store")],
        [InlineKeyboardButton(text="HOW TO VERIFY ❓", callback_data="adm_howto")],
        [InlineKeyboardButton(text="👥 USERS", callback_data="adm_users")],
        [InlineKeyboardButton(text="👮 ADMINS", callback_data="adm_admins")],
    ])


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 BACK", callback_data="adm_back")]
    ])


def setting_keyboard(kind):
    label = {
        "verify": "VERIFY",
        "store": "STORE",
        "howto": "HOW TO VERIFY",
    }[kind]
    rows = [
        [InlineKeyboardButton(text="✏️ Edit Button Name", callback_data=f"edit_name:{kind}")],
        [InlineKeyboardButton(text="🔗 Set / Edit Link", callback_data=f"edit_url:{kind}")],
    ]
    if kind == "verify":
        rows.append([
            InlineKeyboardButton(text="➕ New / Add", callback_data="verify_new"),
            InlineKeyboardButton(text="🗑 Delete", callback_data="verify_delete"),
        ])
        rows.append([
            InlineKeyboardButton(text="🟢 Active", callback_data="verify_active"),
            InlineKeyboardButton(text="⚫ Inactive", callback_data="verify_inactive"),
        ])
    rows.append([InlineKeyboardButton(text="🔴 BACK", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 NUM LIST", callback_data="users_num")],
        [InlineKeyboardButton(text="📋 USER LIST", callback_data="users_list")],
        [InlineKeyboardButton(text="🔴 BACK", callback_data="adm_back")],
    ])


def admins_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ADD ADMIN", callback_data="admin_add")],
        [InlineKeyboardButton(text="➖ REMOVE ADMIN", callback_data="admin_remove")],
        [InlineKeyboardButton(text="📋 ADMIN LIST", callback_data="admin_list")],
        [InlineKeyboardButton(text="🔴 BACK", callback_data="adm_back")],
    ])


def state_set(uid, state):
    db.setdefault("states", {})[str(uid)] = state
    save_db(db)


def state_get(uid):
    return db.get("states", {}).get(str(uid))


def state_clear(uid):
    db.setdefault("states", {}).pop(str(uid), None)
    save_db(db)


async def safe_delete(message: Message):
    try:
        await message.delete()
    except (TelegramBadRequest, TelegramForbiddenError):
        pass


async def temporary_warning(message: Message, text: str, delay: float = 3):
    try:
        sent = await message.answer(text)
        await asyncio.sleep(delay)
        await safe_delete(sent)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass


async def send_invalid(message: Message):
    sent = await message.answer(like_help())
    await asyncio.sleep(3)
    await safe_delete(sent)


async def handle_like(message: Message, parts):
    if len(parts) != 3 or not parts[1] or not parts[2]:
        await send_invalid(message)
        return

    region = parts[1].lower()
    uid = parts[2]

    # Keep the accepted syntax simple and predictable.
    if len(uid) < 5 or len(uid) > 20 or not uid.isdigit():
        await send_invalid(message)
        return

    remember_user(message.from_user)

    processing = await message.answer(
        "🎮 <b>Processing your like request...</b>\n\n"
        "⌛ <i>Please wait</i>",
        parse_mode="HTML",
    )

    await asyncio.sleep(2)

    v = db["verify"]
    if not v.get("active") or not v.get("url"):
        text = (
            "⚠️ <b>Verification is currently unavailable.</b>\n\n"
            "Please try again later."
        )
        await processing.edit_text(text, parse_mode="HTML")
        return

    username = message.from_user.full_name.replace("<", "&lt;").replace(">", "&gt;")
    text = (
        "🎮 <b>LIKE REQUEST VERIFICATION</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>Name:</b> {username}\n"
        f"🆔 <b>UID:</b> <code>{uid}</code>\n"
        f"🌍 <b>Region:</b> <code>{region.upper()}</code>\n\n"
        f"🔗 <b>Verification Link:</b>\n"
        f'<a href="{v["url"]}">{v["url"]}</a>\n\n'
        "⚠️ <b>Verify to send free likes</b>"
    )
    await processing.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=verify_keyboard(),
        disable_web_page_preview=True,
    )


async def admin_only_message(message: Message):
    if not is_admin(message.from_user.id):
        await temporary_warning(message, "🚫 <b>Admins only</b>", 3)
        return False
    return True


async def on_admin_command(message: Message):
    if not await admin_only_message(message):
        return
    remember_user(message.from_user)
    state_clear(message.from_user.id)
    await message.answer(
        "👋 <b>Welcome Admins!!</b>\n\nChoose an option:",
        parse_mode="HTML",
        reply_markup=admin_main_keyboard(),
    )


async def on_message(message: Message):
    if not message.from_user:
        return

    remember_user(message.from_user)

    # Admin UI input states have priority.
    if is_admin(message.from_user.id) and state_get(message.from_user.id):
        state = state_get(message.from_user.id)
        if not message.text:
            await temporary_warning(message, "❌ Please send text only.", 3)
            return

        if state["action"] == "set_name":
            kind = state["kind"]
            db[kind]["name"] = message.text.strip()[:64]
            save_db(db)
            state_clear(message.from_user.id)
            await message.answer(
                f"✅ {kind.upper()} button name updated to:\n<b>{db[kind]['name']}</b>",
                parse_mode="HTML",
                reply_markup=setting_keyboard(kind),
            )
            return

        if state["action"] == "set_url":
            url = message.text.strip()
            if not valid_http_url(url):
                await temporary_warning(message, "❌ Invalid link. Use http:// or https://", 3)
                return
            kind = state["kind"]
            db[kind]["url"] = url
            save_db(db)
            state_clear(message.from_user.id)
            await message.answer(
                f"✅ {kind.upper()} link updated.",
                reply_markup=setting_keyboard(kind),
            )
            return

        if state["action"] in ("add_admin", "remove_admin"):
            raw = message.text.strip()
            if not raw.isdigit():
                await temporary_warning(message, "❌ Send a numeric Telegram user ID.", 3)
                return
            target = int(raw)
            if state["action"] == "add_admin":
                if target not in db["admins"]:
                    db["admins"].append(target)
                    save_db(db)
                reply = f"✅ <code>{target}</code> is now an admin."
            else:
                if target == OWNER_ID:
                    reply = "⚠️ Owner admin cannot be removed."
                else:
                    db["admins"] = [x for x in db["admins"] if int(x) != target]
                    save_db(db)
                    reply = f"✅ <code>{target}</code> removed from admins."
            state_clear(message.from_user.id)
            await message.answer(reply, parse_mode="HTML", reply_markup=admins_keyboard())
            return

    # Ignore/deal with commands first.
    if message.text and message.text.startswith("/"):
        command = message.text.split()[0].split("@")[0].lower()
        parts = message.text.split()

        if command == "/like":
            await handle_like(message, parts)
            return
        if command == "/admin":
            await on_admin_command(message)
            return
        if command == "/start":
            # No public command other than /like is exposed; /start gets the same format help.
            await send_invalid(message)
            return

        # Any other command is invalid in the group.
        await send_invalid(message)
        return

    # Normal messages: delete immediately and send a short warning.
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await safe_delete(message)
        await temporary_warning(
            message,
            "🚫 <b>Only commands are allowed here.</b>\n\n"
            "Use: <code>/like {region} {uid}</code>\n"
            "Example: <code>/like ind 12234555</code>",
            3,
        )


async def verify_click(callback: CallbackQuery):
    user = callback.from_user
    remember_user(user)
    if user.id not in db["verify_clicks"]:
        db["verify_clicks"].append(user.id)
        save_db(db)

    url = db["verify"].get("url")
    if db["verify"].get("active") and url:
        # answerCallbackQuery(url=...) opens the configured URL while also
        # letting the bot record the click.
        await callback.answer(url=url)
    else:
        await callback.answer("Verification is currently inactive.", show_alert=True)


async def admin_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admins only", show_alert=True)
        return

    data = callback.data or ""

    if data == "adm_back":
        state_clear(callback.from_user.id)
        await callback.message.edit_text(
            "👋 <b>Welcome Admins!!</b>\n\nChoose an option:",
            parse_mode="HTML",
            reply_markup=admin_main_keyboard(),
        )
        await callback.answer()
        return

    if data == "adm_verify":
        v = db["verify"]
        status = "🟢 Active" if v.get("active") else "⚫ Inactive"
        await callback.message.edit_text(
            "🔗 <b>VERIFY LINK SETTINGS</b>\n\n"
            f"Button: <b>{v.get('name')}</b>\n"
            f"Link: <code>{v.get('url') or 'Not set'}</code>\n"
            f"Status: {status}",
            parse_mode="HTML",
            reply_markup=setting_keyboard("verify"),
        )
        await callback.answer()
        return

    if data in ("adm_store", "adm_howto"):
        kind = "store" if data == "adm_store" else "howto"
        title = "🛒 STORE SETTINGS" if kind == "store" else "HOW TO VERIFY SETTINGS"
        item = db[kind]
        await callback.message.edit_text(
            f"<b>{title}</b>\n\n"
            f"Button: <b>{item['name']}</b>\n"
            f"Link: <code>{item['url']}</code>",
            parse_mode="HTML",
            reply_markup=setting_keyboard(kind),
        )
        await callback.answer()
        return

    if data == "adm_users":
        await callback.message.edit_text(
            "👥 <b>USERS</b>\n\nChoose an option:",
            parse_mode="HTML",
            reply_markup=user_keyboard(),
        )
        await callback.answer()
        return

    if data == "users_num":
        await callback.message.edit_text(
            f"🔢 <b>Total {len(db.get('verify_clicks', []))}</b>\n\n"
            "Users counted here are those who clicked VERIFY.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        await callback.answer()
        return

    if data == "users_list":
        ids = db.get("verify_clicks", [])
        if not ids:
            body = "📋 <b>User List</b>\n\nNo users have clicked VERIFY yet."
        else:
            lines = [f"{i}. <code>{uid}</code>" for i, uid in enumerate(ids, 1)]
            body = "📋 <b>User List</b>\n\n" + "\n".join(lines)
        # Telegram messages have a 4096 character limit.
        body = body[:3900]
        await callback.message.edit_text(body, parse_mode="HTML", reply_markup=back_keyboard())
        await callback.answer()
        return

    if data == "adm_admins":
        await callback.message.edit_text(
            "👮 <b>ADMINS</b>\n\nAdd/remove admin IDs or view the current list.",
            parse_mode="HTML",
            reply_markup=admins_keyboard(),
        )
        await callback.answer()
        return

    if data == "admin_add":
        state_set(callback.from_user.id, {"action": "add_admin"})
        await callback.message.edit_text(
            "➕ <b>ADD ADMIN</b>\n\nSend the Telegram numeric user ID.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        await callback.answer()
        return

    if data == "admin_remove":
        state_set(callback.from_user.id, {"action": "remove_admin"})
        await callback.message.edit_text(
            "➖ <b>REMOVE ADMIN</b>\n\nSend the Telegram numeric user ID.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        await callback.answer()
        return

    if data == "admin_list":
        ids = db.get("admins", [])
        body = "👮 <b>Admin List</b>\n\n" + "\n".join(
            f"{i}. <code>{uid}</code>" for i, uid in enumerate(ids, 1)
        )
        await callback.message.edit_text(body, parse_mode="HTML", reply_markup=back_keyboard())
        await callback.answer()
        return

    if data.startswith("edit_name:"):
        kind = data.split(":", 1)[1]
        state_set(callback.from_user.id, {"action": "set_name", "kind": kind})
        await callback.message.edit_text(
            f"✏️ <b>Edit {kind.upper()} button name</b>\n\nSend the new button text.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        await callback.answer()
        return

    if data.startswith("edit_url:"):
        kind = data.split(":", 1)[1]
        state_set(callback.from_user.id, {"action": "set_url", "kind": kind})
        await callback.message.edit_text(
            f"🔗 <b>Set {kind.upper()} link</b>\n\nSend the new http/https link.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        await callback.answer()
        return

    if data == "verify_new":
        db["verify"] = {"name": DEFAULT_VERIFY_NAME, "url": DEFAULT_VERIFY_URL, "active": True}
        save_db(db)
        await callback.message.edit_text(
            "➕ <b>New VERIFY link configuration created/reset.</b>",
            parse_mode="HTML",
            reply_markup=setting_keyboard("verify"),
        )
        await callback.answer()
        return

    if data == "verify_delete":
        db["verify"] = {"name": DEFAULT_VERIFY_NAME, "url": "", "active": False}
        save_db(db)
        await callback.message.edit_text(
            "🗑 <b>VERIFY link deleted.</b>\n\nSet a new link before activating it.",
            parse_mode="HTML",
            reply_markup=setting_keyboard("verify"),
        )
        await callback.answer()
        return

    if data == "verify_active":
        if not db["verify"].get("url"):
            await callback.answer("Set a link first.", show_alert=True)
            return
        db["verify"]["active"] = True
        save_db(db)
        await callback.answer("🟢 VERIFY activated.")
        return

    if data == "verify_inactive":
        db["verify"]["active"] = False
        save_db(db)
        await callback.answer("⚫ VERIFY deactivated.")
        return


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Put your BotFather token in .env")

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(on_message)
    dp.callback_query.register(verify_click, F.data == "verify_click")
    dp.callback_query.register(admin_callback, F.data != "verify_click")

    log.info("Bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
