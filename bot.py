"""Coding Arena — quiz bot.

Bot features:
- /start -> "Admin panel" va "Kod arenasi" tugmalari
- Admin panel: Coding Arena boshqaruvi, Userlar boshqaruvi
- Kod arenasi: faol arenalar ro'yxati
"""

import asyncio
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import Database

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:root@localhost:5432/coding_arena")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)

if not BOT_TOKEN:
    raise SystemExit("Xato: BOT_TOKEN o'zgarichini .env faylga yozing!")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

db = Database(DATABASE_URL)

# Foydalanuvchi holatlari (loop ga bog'liq bo'lmasligi uchun session xomini saqlaymiz)
STATE: "dict[int, dict[str, Any]]" = {}


# ---------------- helpers ----------------

def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


def st(is_active: bool) -> str:
    return "🟢 Faol" if is_active else "🔴 Yaqin"


def state(update: Update) -> "dict[str, Any]":
    uid = update.effective_user.id
    return STATE.setdefault(uid, {})


def is_admin(update: Update, db_user) -> bool:
    return (db_user is not None and db_user["is_admin"]) or update.effective_user.id == ADMIN_ID


async def reply(update: Update, text: str, markup=None) -> None:
    msg = update.effective_message
    if msg is not None:
        await msg.reply_text(text, reply_markup=markup)


async def edit(update: Update, text: str, markup=None) -> None:
    msg = update.effective_message
    if msg is not None:
        await msg.edit_text(text, reply_markup=markup)


async def reply_or_edit(update: Update, text: str, markup=None) -> None:
    if update.callback_query is not None:
        await edit(update, text, markup)
    else:
        await reply(update, text, markup)


# ---------------- main menu ----------------

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, is_admin: bool) -> None:
    rows = []
    if is_admin:
        rows.append([btn("🎮 Admin panel", "adm")])
    rows.append([btn("🏆 Kod arenasi", "user_arena_list")])
    await reply_or_edit(update, "🏆 Quiz bot — Coding Arena\n\nQuyidagilardan birini tanlang:", kb(*rows))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    st_ = state(update)
    st_["arena_id"] = None
    st_["arena_questions"] = None
    st_["wait"] = None

    db_user = await db.get_user_by_tg_id(user.id)
    if db_user is None:
        if user.id == ADMIN_ID:
            await db.create_user(
                username=user.full_name,
                login=f"admin_{user.id}",
                password="",
                is_admin=True,
                telegram_id=user.id,
            )
            await reply(update, "✅ Admin sifatida ro'yxatdan o'tdingiz ❇️")
            await send_main_menu(update, context, is_admin=True)
            return
        st_["wait"] = "signup_confirm"
        extra = f"\n\n💡 Admin sifatida ishlash uchun .env ga ADMIN_ID={user.id} yozing" if ADMIN_ID == 0 else ""
        await reply(
            update,
            "📝 Ro'yxatdan o'tish ❓\n\nAkkount yaratish uchun tasdiqlang:" + extra,
            kb([btn("✅ Tasdiqlash", "signup")]),
        )
        return
    await send_main_menu(update, context, is_admin=is_admin(update, db_user))


async def cb_signup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st_ = state(update)
    st_["wait"] = "signup_login"
    await edit(update, "📝 Loginni kiriting:")


# ---------------- user arena ----------------

async def send_user_arena_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arenas = await db.list_arenas(active_only=True)
    if arenas:
        text = "🏆 Kod arenasi — Faol:"
        rows = [[btn(f"🚪 {a['name']}", f"user_arena_enter_{a['id']}")] for a in arenas]
    else:
        text = "🏆 Kod arenasi — Hozir aktiv arenalar yo'q ❌"
        rows = []
    rows.append([btn("🔙 Main menu", "back_main")])
    await reply_or_edit(update, text, kb(*rows))


async def enter_arena(update: Update, context: ContextTypes.DEFAULT_TYPE, arena_id: int) -> None:
    st_ = state(update)
    arena = await db.get_arena(arena_id)
    if arena is None or not arena["is_active"]:
        await edit(update, "🚪 Arena yopiq ❌", kb([btn("🔙 Ro'yxatga", "user_arena_list")]))
        return
    questions = await db.list_questions(arena_id)
    st_["arena_id"] = arena_id
    st_["arena_questions"] = questions
    st_["arena_index"] = 0
    st_["arena_score"] = 0
    if not questions:
        await edit(
            update,
            f"🏆 Arena: {arena['name']}\n\nSavollar yo'q ❌",
            kb([btn("🔙 Ro'yxatga", "user_arena_list")]),
        )
        return
    await send_arena_question(update, context, edit_msg=True)


async def send_arena_question(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_msg: bool = False) -> None:
    st_ = state(update)
    arena = await db.get_arena(st_["arena_id"])
    idx = st_.get("arena_index", 0)
    questions = st_.get("arena_questions") or []
    q = questions[idx]
    total = len(questions)
    opts = [q["option_a"] or "-", q["option_b"] or "-", q["option_c"] or "-", q["option_d"] or "-"]
    text = (
        f"🏆 Arena: {arena['name']}\n\n"
        f"Savol {idx + 1}/{total}\n{q['question']}\n\n"
        + "\n".join(f"{L}) {o}" for L, o in zip("ABCD", opts))
    )
    rows = [
        [btn("A", f"user_arena_ans_{q['id']}_a"), btn("B", f"user_arena_ans_{q['id']}_b")],
        [btn("C", f"user_arena_ans_{q['id']}_c"), btn("D", f"user_arena_ans_{q['id']}_d")],
        [btn("🚪 Chiqish", f"user_arena_exit_{st_['arena_id']}")],
    ]
    if edit_msg:
        await edit(update, text, kb(*rows))
    else:
        await reply(update, text, kb(*rows))


async def answer_question(update: Update, context: ContextTypes.DEFAULT_TYPE, qid: int, option: str) -> None:
    st_ = state(update)
    questions = st_.get("arena_questions") or []
    idx = st_.get("arena_index", 0)
    if idx >= len(questions):
        st_["arena_id"] = None
        await edit(update, "Arena yakunlandi ❌")
        return
    q = questions[idx]
    opt_num = {"a": 1, "b": 2, "c": 3, "d": 4}[option]
    if opt_num == int(q["correct_option"] or 1):
        st_["arena_score"] = st_.get("arena_score", 0) + 1
    if idx + 1 >= len(questions):
        score = st_.get("arena_score", 0)
        total = len(questions)
        text = f"🎉 Arena yakunlandi!\n\nTo'g'ri javoblar: {score}/{total}"
        if score == total:
            text += "\nMukammal o'yladingiz! ✅"
        rows = [
            [btn("🚪 Ro'yxatga", "user_arena_list")],
            [btn("🔙 Main menu", "back_main")],
        ]
        st_["arena_id"] = None
        st_["arena_questions"] = None
        await edit(update, text, kb(*rows))
    else:
        st_["arena_index"] = idx + 1
        await send_arena_question(update, context, edit_msg=True)


async def exit_arena(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st_ = state(update)
    st_["arena_id"] = None
    st_["arena_questions"] = None
    st_["arena_index"] = None
    await send_user_arena_list(update, context)


async def cmd_cancel_arena(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st_ = state(update)
    if st_.get("arena_id"):
        st_["arena_id"] = None
        st_["arena_questions"] = None
        st_["arena_index"] = None
        await reply(update, "❌ Arenadan chiqdingiz.\n\nQayta ko'rmoqchi bo'lsangiz: /start → Kod arenasi")
        await send_user_arena_list(update, context)
    else:
        await reply(update, "Sizda arena yo'q ❌")


# ---------------- admin: arena management ----------------

async def send_admin_arena_mgmt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_or_edit(update, "🎮 Coding Arena boshqaruvi:", kb(
        [btn("➕ Arena qo'shish", "adm_arena_add")],
        [btn("🏆 Arenalar ro'yxati", "adm_arenas")],
        [btn("🔙 Admin panel", "adm")],
    ))


async def send_admin_arena_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arenas = await db.list_arenas(active_only=False)
    if arenas:
        text = "🏆 Arenalar ro'yxati:"
        rows = [[btn(f"{a['name']} — {st(a['is_active'])}", f"admin_arena_{a['id']}")] for a in arenas]
    else:
        text = "🏆 Arenalar ro'yxati: Bo'sh ❌"
        rows = []
    rows.append([btn("➕ Yangi arena", "adm_arena_add")])
    rows.append([btn("🔙 Coding Arena boshqaruvi", "adm_arena_mgmt")])
    await reply_or_edit(update, text, kb(*rows))


async def send_admin_arena_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, arena_id: int) -> None:
    arena = await db.get_arena(arena_id)
    if arena is None:
        return
    questions = await db.list_questions(arena_id)
    rows = [
        [btn(f"🔄 {'Ochish' if arena['is_active'] else 'Yopish'}", f"admin_arena_{arena_id}_toggle")],
        [btn("➕ Savol qo'shish", f"admin_arena_{arena_id}_qadd"),
         btn("📋 Savollar ro'yxati", f"admin_arena_{arena_id}_qlist")],
        [btn("🗑️ Savol o'chirish", f"admin_arena_{arena_id}_qdel")],
        [btn("🗑️ Arena o'chirish", f"admin_arena_{arena_id}_del")],
        [btn("🔙 Arenalar ro'yxatiga", "adm_arenas")],
    ]
    text = (
        f"🏆 {arena['name']}\n\n"
        f"Status: {st(arena['is_active'])}\n"
        f"Savollar: {len(questions)} ta"
    )
    await reply_or_edit(update, text, kb(*rows))


async def send_admin_questions(update: Update, context: ContextTypes.DEFAULT_TYPE, arena_id: int) -> None:
    questions = await db.list_questions(arena_id)
    rows = []
    if questions:
        lines = [f"📋 Savollar ({len(questions)} ta):"]
        for i, q in enumerate(questions):
            lines.append(
                f"{i + 1}. {q['question']}\n"
                f"   A) {q['option_a'] or '-'}  B) {q['option_b'] or '-'}  "
                f"C) {q['option_c'] or '-'}  D) {q['option_d'] or '-'}\n"
                f"   ✅ To'g'ri: {q['correct_option']}"
            )
            rows.append([btn(f"🗑️ {i + 1}. {q['question'][:40]}", f"qdel_{q['id']}")])
        text = "\n".join(lines)
    else:
        text = "📋 Savollar: Yo'q ❌"
    rows.append([btn("🔙 Arena boshqaruviga", f"admin_arena_{arena_id}")])
    await reply_or_edit(update, text, kb(*rows))


async def cb_arena_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, arena_id: int) -> None:
    arena = await db.get_arena(arena_id)
    if arena:
        await db.set_arena_active(arena_id, not arena["is_active"])
    await send_admin_arena_detail(update, context, arena_id)


async def cb_arena_del(update: Update, context: ContextTypes.DEFAULT_TYPE, arena_id: int) -> None:
    arena = await db.get_arena(arena_id)
    if arena is None:
        return
    await edit(update, f"🗑️ {arena['name']}ni o'chirishni tasdiqlang?", kb(
        [btn("🗑️ Ha, o'chirish", f"admin_arena_{arena_id}_del_confirm")],
        [btn("🔙 Orqaga", f"admin_arena_{arena_id}")],
    ))


async def cb_arena_del_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, arena_id: int) -> None:
    arena = await db.get_arena(arena_id)
    if arena is None:
        return
    await db.delete_arena(arena_id)
    await reply(update, f"🗑️ Arena o'chirildi: {arena['name']}")
    await send_admin_arena_list(update, context)


async def cb_arena_qadd(update: Update, context: ContextTypes.DEFAULT_TYPE, arena_id: int) -> None:
    st_ = state(update)
    st_["addq_arena_id"] = arena_id
    st_["wait"] = "addq_text"
    await reply(update, "📝 Savolni kiriting:")


async def cb_qdel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data
    qid = int(re.fullmatch(r"qdel_(\d+)", data).group(1))
    q = await db.get_question(qid)
    if q:
        await db.delete_question(qid)
        await send_admin_questions(update, context, q["arena_id"])


# ---------------- admin: users management ----------------

async def send_users_mgmt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_or_edit(update, "👥 Userlar boshqaruvi:", kb(
        [btn("➕ Yangi user qo'shish", "add_user")],
        [btn("👥 Userlar ro'yxati", "user_list_admin")],
        [btn("🔙 Admin panel", "adm")],
    ))


async def send_user_list_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    users = await db.list_users()
    rows = []
    for u in users:
        tag = " (admin)" if u["is_admin"] else ""
        rows.append([
            btn(f"👤 {u['username'] or u['login']}{tag}", f"edit_user_{u['id']}"),
            btn("🗑️", f"del_user_confirm_{u['id']}"),
        ])
    if not rows:
        text = "👥 Userlar: Bo'sh ❌"
    else:
        text = f"👥 Userlar ({len(users)} ta):"
    rows.append([btn("🔙 Coding Arena boshqaruvi", "adm_arena_mgmt")])
    await reply_or_edit(update, text, kb(*rows))


async def send_edit_user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    u = await db.get_user(user_id)
    if u is None:
        return
    rows = [
        [btn(f"Ism: {u['username'] or '-'}", f"edit_user_name_{u['id']}")],
        [btn(f"Login: @{u['login']}", f"edit_user_login_{u['id']}")],
        [btn("Parol", f"edit_user_pass_{u['id']}")],
        [btn("🔙 Userlar ro'yxati", "user_list_admin")],
    ]
    await reply_or_edit(update, f"✏️ {u['username'] or u['login']}", kb(*rows))


async def cb_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st_ = state(update)
    st_["wait"] = "add_user_login"
    await reply(update, "🔐 Loginni kiriting:")


async def cb_edit_user_name(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    st_ = state(update)
    st_["edit_user_id"] = user_id
    st_["wait"] = "edit_user_name"
    await reply(update, "✏️ Ismini kiriting:")


async def cb_edit_user_login(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    st_ = state(update)
    st_["edit_user_id"] = user_id
    st_["wait"] = "edit_user_login"
    await reply(update, "🔐 Loginni kiriting:")


async def cb_edit_user_pass(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    st_ = state(update)
    st_["edit_user_id"] = user_id
    st_["wait"] = "edit_user_pass"
    await reply(update, "🔐 Parolni kiriting:")


async def cb_del_user_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    u = await db.get_user(user_id)
    name = (u["username"] or u["login"]) if u else ""
    await edit(update, f"🗑️ {name}ni o'chirishni tasdiqlang?", kb(
        [btn("🗑️ Ha, o'chirish", f"del_user_{user_id}")],
        [btn("🔙 Orqaga", "user_list_admin")],
    ))


async def cb_del_user(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    u = await db.get_user(user_id)
    await db.delete_user(user_id)
    await reply(update, f"🗑️ User o'chirildi: @{u['login']}" if u else "🗑️ User o'chirildi")
    await send_user_list_admin(update, context)


# ---------------- text handlers ----------------

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st_ = state(update)
    text = (update.message.text or "").strip()

    if st_.get("arena_id"):
        await reply(update, "⚠️ Siz kod arenasida ekansiz, to'xtash uchun /cancel_arena tugmasini boshing ❗")
        return

    wait = st_.get("wait")

    if wait == "signup_confirm":
        await reply(
            update,
            "📝 Ro'yxatdan o'tish ❓\n\nAkkount yaratish uchun tasdiqlang:",
            kb([btn("✅ Tasdiqlash", "signup")]),
        )
        return

    if wait == "signup_login":
        if len(text) < 2:
            await reply(update, "Login kamida 2 ta belgidan iborat bo'lishi kerak ❌")
            return
        st_["signup_login"] = text
        st_["wait"] = "signup_pass"
        await reply(update, "🔐 Parolni kiriting:")
        return

    if wait == "signup_pass":
        login = st_.get("signup_login")
        try:
            rec = await db.create_user(
                username=update.effective_user.full_name,
                login=login,
                password=text,
                is_admin=False,
                telegram_id=update.effective_user.id,
            )
        except Exception as exc:
            log.warning("create_user failed: %s", exc)
            st_["wait"] = "signup_login"
            await reply(update, "❌ Bu login band yoki xato ❌")
            return
        st_["wait"] = None
        st_["signup_login"] = None
        await reply(update, f"✅ Akkount yaratildi: @{rec['login']}")
        await send_main_menu(update, context, is_admin=False)
        return

    if wait == "arena_name":
        arena = await db.add_arena(text)
        st_["wait"] = None
        await reply(
            update,
            f"✅ Arena qo'shildi: {arena['name']}",
            kb([btn("🏆 Arenalar ro'yxati", "adm_arenas")], [btn("🔙 Admin panel", "adm")]),
        )
        return

    if wait == "addq_text":
        st_["q_text"] = text
        st_["wait"] = "addq_opt_a"
        await reply(update, "🅰️ Option A:")
        return

    if wait == "addq_opt_a":
        st_["q_opt_a"] = text
        st_["wait"] = "addq_opt_b"
        await reply(update, "🅱️ Option B:")
        return

    if wait == "addq_opt_b":
        st_["q_opt_b"] = text
        st_["wait"] = "addq_opt_c"
        await reply(update, "🅲 Option C:")
        return

    if wait == "addq_opt_c":
        st_["q_opt_c"] = text
        st_["wait"] = "addq_opt_d"
        await reply(update, "🅳 Option D:")
        return

    if wait == "addq_opt_d":
        st_["q_opt_d"] = text
        st_["wait"] = "addq_correct"
        await reply(update, "✅ To'g'ri javobni kiriting (A/B/C/D):")
        return

    if wait == "addq_correct":
        letter = text[:1].upper()
        correct = {"A": 1, "B": 2, "C": 3, "D": 4}.get(letter, 1)
        arena_id = st_.get("addq_arena_id")
        options = [st_.get("q_opt_a"), st_.get("q_opt_b"), st_.get("q_opt_c"), st_.get("q_opt_d")]
        question = st_.get("q_text") or ""
        for key in ("addq_arena_id", "q_text", "q_opt_a", "q_opt_b", "q_opt_c", "q_opt_d", "wait"):
            st_.pop(key, None)
        if arena_id is not None:
            await db.add_question(arena_id=arena_id, question=question, options=options, correct=correct)
            await reply(update, "✅ Savol qo'shildi ✅")
            await send_admin_arena_detail(update, context, arena_id)
        return

    if wait == "add_user_login":
        st_["add_user_login"] = text
        st_["wait"] = "add_user_pass"
        await reply(update, "🔐 Parolni kiriting:")
        return

    if wait == "add_user_pass":
        login = st_.get("add_user_login")
        try:
            rec = await db.create_user(username=login, login=login, password=text,
                                       is_admin=False, telegram_id=None)
        except Exception as exc:
            log.warning("create_user failed: %s", exc)
            st_["wait"] = "add_user_login"
            await reply(update, "❌ Bu login band ❌")
            return
        st_["wait"] = None
        await reply(update, f"✅ User qo'shildi: @{rec['login']}")
        await send_user_list_admin(update, context)
        return

    if wait == "edit_user_name":
        user_id = st_.get("edit_user_id")
        await db.update_user(user_id, username=text)
        st_["wait"] = None
        await send_edit_user_menu(update, context, user_id)
        return

    if wait == "edit_user_login":
        user_id = st_.get("edit_user_id")
        await db.update_user(user_id, login=text)
        st_["wait"] = None
        await send_edit_user_menu(update, context, user_id)
        return

    if wait == "edit_user_pass":
        user_id = st_.get("edit_user_id")
        await db.update_user(user_id, password=text)
        st_["wait"] = None
        await send_edit_user_menu(update, context, user_id)
        return

    await reply(update, "📬 Quyida tugmalar orqali ishlash mumkin ❌")


# ---------------- callback handler ----------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data or ""
    st_ = state(update)

    db_user = await db.get_user_by_tg_id(update.effective_user.id)
    admin = is_admin(update, db_user)

    # --- user arena ---
    mm = re.fullmatch(r"user_arena_list", data)
    if mm:
        await send_user_arena_list(update, context)
        return
    mm = re.fullmatch(r"user_arena_enter_(\d+)", data)
    if mm:
        await enter_arena(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"user_arena_ans_(\d+)_([abcd])", data)
    if mm:
        if st_.get("arena_id"):
            await answer_question(update, context, int(mm.group(1)), mm.group(2))
        return
    mm = re.fullmatch(r"user_arena_exit_(\d+)", data)
    if mm:
        await exit_arena(update, context)
        return
    mm = re.fullmatch(r"back_main", data)
    if mm:
        st_["arena_id"] = None
        st_["wait"] = None
        await send_main_menu(update, context, is_admin=admin)
        return
    mm = re.fullmatch(r"signup", data)
    if mm:
        await cb_signup(update, context)
        return

    if not admin:
        await edit(update, "🔒 Sizda admin huquqi yo'q ❌")
        return

    # --- admin ---
    mm = re.fullmatch(r"adm", data)
    if mm:
        await reply_or_edit(update, "🎮 Admin panel:", kb(
            [btn("🎮 Coding Arena boshqaruvi", "adm_arena_mgmt")],
            [btn("👥 Userlar boshqaruvi", "adm_user_mgmt")],
            [btn("🔙 Main menu", "back_main")],
        ))
        return
    mm = re.fullmatch(r"adm_arena_mgmt", data)
    if mm:
        await send_admin_arena_mgmt(update, context)
        return
    mm = re.fullmatch(r"adm_arena_add", data)
    if mm:
        st_["wait"] = "arena_name"
        await edit(update, "📝 Arenaning nomini kiriting:")
        return
    mm = re.fullmatch(r"adm_arenas", data)
    if mm:
        await send_admin_arena_list(update, context)
        return
    mm = re.fullmatch(r"admin_arena_(\d+)_toggle", data)
    if mm:
        await cb_arena_toggle(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"admin_arena_(\d+)_del", data)
    if mm:
        await cb_arena_del(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"admin_arena_(\d+)_del_confirm", data)
    if mm:
        await cb_arena_del_confirm(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"admin_arena_(\d+)_qadd", data)
    if mm:
        await cb_arena_qadd(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"admin_arena_(\d+)_qlist", data)
    if mm:
        await send_admin_questions(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"admin_arena_(\d+)_qdel", data)
    if mm:
        await send_admin_questions(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"admin_arena_(\d+)", data)
    if mm:
        await send_admin_arena_detail(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"qdel_(\d+)", data)
    if mm:
        await cb_qdel(update, context)
        return
    mm = re.fullmatch(r"adm_user_mgmt", data)
    if mm:
        await send_users_mgmt(update, context)
        return
    mm = re.fullmatch(r"user_list_admin", data)
    if mm:
        await send_user_list_admin(update, context)
        return
    mm = re.fullmatch(r"add_user", data)
    if mm:
        await cb_add_user(update, context)
        return
    mm = re.fullmatch(r"edit_user_(\d+)", data)
    if mm:
        user = await db.get_user(int(mm.group(1)))
        if user is not None:
            await send_edit_user_menu(update, context, user["id"])
        return
    mm = re.fullmatch(r"edit_user_name_(\d+)", data)
    if mm:
        await cb_edit_user_name(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"edit_user_login_(\d+)", data)
    if mm:
        await cb_edit_user_login(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"edit_user_pass_(\d+)", data)
    if mm:
        await cb_edit_user_pass(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"del_user_confirm_(\d+)", data)
    if mm:
        await cb_del_user_confirm(update, context, int(mm.group(1)))
        return
    mm = re.fullmatch(r"del_user_(\d+)", data)
    if mm:
        await cb_del_user(update, context, int(mm.group(1)))
        return


# ---------------- entry point ----------------

async def bootstrap_db(application: Application) -> None:
    await db.connect()
    await db.create_tables()
    log.info("DB ulandi va jadvalar yaratildi ❇️")


def main() -> None:
    # Python 3.14: asyncio.get_event_loop() raises RuntimeError when no current
    # event loop is set, and Application.run_polling relies on it - so create
    # and set an event loop explicitly.
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(BOT_TOKEN).post_init(bootstrap_db).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel_arena", cmd_cancel_arena))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^\w+$"))
    app.run_polling()


if __name__ == "__main__":
    main()
