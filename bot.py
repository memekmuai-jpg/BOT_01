import os
import asyncio
import logging
import base64
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from storage import Storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ADMIN_ID = int(os.getenv("ADMIN_ID", "1636051561"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8722434343:AAG0nK9GdJyx8_KZjzjqX4NOIWDjLew_vVg")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

db = Storage("data.json")

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_settings():
    return db.get("settings", {
        "api_key": "",
        "mode": "prompt",
        "language": "EN",
        "prompt_instructions": [],
        "caption_instructions": [],
        "active_prompt_instruction": -1,
        "active_caption_instruction": -1,
    })

def save_settings(s):
    db.set("settings", s)

def is_admin(user_id):
    return user_id == ADMIN_ID

def build_core_system(mode, language, custom_instruction):
    # Penegasan bahasa yang sangat ketat
    lang_note = "RESPOND STRICTLY IN ENGLISH." if language == "EN" else "JAWAB STRICTLY DALAM BAHASA INDONESIA."

    if mode == "prompt":
        core = (
            f"You are an expert AI image analyst specializing in generating detailed image prompts. "
            f"{lang_note}\n"
            f"Analyze the given image and generate a highly detailed, descriptive prompt "
            f"that can be used to recreate this image with an AI image generator. "
            f"Focus on: subject, style, lighting, colors, composition, mood, camera angle, "
            f"background, and any relevant technical details. Be specific and descriptive."
        )
    else:
        core = (
            f"You are a professional social media copywriter. "
            f"{lang_note}\n"
            f"Analyze the given image and generate engaging captions for social media. "
            f"IMPORTANT: If you generate multiple caption variations, each variation MUST be separated by exactly this delimiter on its own line: "
            f"---CAPTION_BREAK--- "
        )

    # Instruksi absolut untuk tidak memberikan basa-basi (sesuai permintaan Anda)
    no_header = (
        "\n\nCRITICAL RULE: OUTPUT ONLY THE FINAL RESULT. "
        "DO NOT include any conversational text, pleasantries, headers, titles, or introductions "
        "(e.g., 'Here is the result', 'Tentu, ini hasilnya:', 'Output:', 'Prompt:'). "
        "Just output the raw prompt or captions directly."
    )

    if custom_instruction and custom_instruction.strip():
        full_system = core + no_header + "\n\n[Additional System Instructions]:\n" + custom_instruction.strip()
    else:
        full_system = core + no_header

    return full_system

async def call_groq_vision(api_key, system_prompt, image_data_b64, mime_type="image/jpeg"):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_VISION_MODEL,
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_data_b64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": "Analyze this image based on your instructions."
                    }
                ]
            }
        ]
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(GROQ_API_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

def format_caption_output(raw_text):
    parts = [p.strip() for p in raw_text.split("---CAPTION_BREAK---") if p.strip()]
    if len(parts) <= 1:
        return raw_text.strip()
    result = ""
    for i, part in enumerate(parts, 1):
        result += f"━━━ Variasi {i} ━━━\n{part}\n\n"
    return result.strip()

# ─────────────────────────────────────────────
# QUEUE SYSTEM
# ─────────────────────────────────────────────

image_queues = {}
queue_locks = {}

async def process_queue(chat_id, context):
    if chat_id not in image_queues:
        return
    while image_queues[chat_id]:
        task = image_queues[chat_id].pop(0)
        await process_single_image(task, context)
    del image_queues[chat_id]
    queue_locks.pop(chat_id, None)

async def process_single_image(task, context):
    chat_id = task["chat_id"]
    message_id = task["message_id"]
    file_id = task["file_id"]
    s = get_settings()

    if not s.get("api_key"):
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ API Key belum diset. Hubungi admin.",
            reply_to_message_id=message_id
        )
        return

    mode = s.get("mode", "prompt")
    language = s.get("language", "EN")

    if mode == "prompt":
        idx = s.get("active_prompt_instruction", -1)
        instructions = s.get("prompt_instructions", [])
    else:
        idx = s.get("active_caption_instruction", -1)
        instructions = s.get("caption_instructions", [])

    custom_instr = instructions[idx]["content"] if (0 <= idx < len(instructions)) else ""
    system_prompt = build_core_system(mode, language, custom_instr)

    thinking_msg = None
    try:
        thinking_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ Memproses gambar...",
            reply_to_message_id=message_id
        )

        file = await context.bot.get_file(file_id)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(file.file_path)
            image_bytes = resp.content

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        result = await call_groq_vision(s["api_key"], system_prompt, image_b64)

        if thinking_msg:
            await context.bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)

        # Hasilkan format final tanpa clean_output
        if mode == "caption":
            final_text = format_caption_output(result)
        else:
            final_text = result.strip()

        if len(final_text) > 4096:
            for i in range(0, len(final_text), 4096):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=final_text[i:i+4096],
                    reply_to_message_id=message_id
                )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=final_text,
                reply_to_message_id=message_id
            )

    except httpx.HTTPStatusError as e:
        if thinking_msg:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)
            except Exception:
                pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ API Error {e.response.status_code}:\n{e.response.text[:300]}",
            reply_to_message_id=message_id
        )
    except Exception as e:
        logger.error(f"Error processing image: {e}")
        if thinking_msg:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)
            except Exception:
                pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Error: {str(e)[:300]}",
            reply_to_message_id=message_id
        )

# ─────────────────────────────────────────────
# USER & ADMIN HANDLERS
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_settings()
    mode = s.get("mode", "prompt").capitalize()
    lang = s.get("language", "EN")
    text = (
        f"👋 *Welcome to Vision Bot!*\n\n"
        f"📌 Mode: *{mode}*\n"
        f"🌐 Language: *{lang}*\n\n"
        f"Kirim foto untuk dianalisis!\n"
        f"Maksimal *10 foto* per sesi (diproses satu per satu)."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    message_id = update.message.message_id
    photo = update.message.photo[-1]

    if chat_id not in image_queues:
        image_queues[chat_id] = []

    if len(image_queues[chat_id]) >= 10:
        await update.message.reply_text(
            "⚠️ Antrian penuh (maks 10 foto). Tunggu sebentar.",
            reply_to_message_id=message_id
        )
        return

    image_queues[chat_id].append({
        "chat_id": chat_id,
        "message_id": message_id,
        "file_id": photo.file_id,
    })

    queue_size = len(image_queues[chat_id])
    if queue_size > 1:
        await update.message.reply_text(
            f"📥 Ditambahkan ke antrian (posisi #{queue_size})",
            reply_to_message_id=message_id
        )

    if chat_id not in queue_locks:
        queue_locks[chat_id] = True
        asyncio.create_task(process_queue(chat_id, context))

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Akses ditolak.")
        return
    await show_admin_menu(update.message, context, edit=False)

async def show_admin_menu(message, context, edit=False):
    s = get_settings()
    mode = s.get("mode", "prompt")
    lang = s.get("language", "EN")
    api_key = s.get("api_key", "")
    api_status = "✅ Aktif" if api_key else "❌ Belum diset"

    p_idx = s.get("active_prompt_instruction", -1)
    p_instrs = s.get("prompt_instructions", [])
    p_active = p_instrs[p_idx]["name"] if 0 <= p_idx < len(p_instrs) else "—"

    c_idx = s.get("active_caption_instruction", -1)
    c_instrs = s.get("caption_instructions", [])
    c_active = c_instrs[c_idx]["name"] if 0 <= c_idx < len(c_instrs) else "—"

    text = (
        f"⚙️ *Admin Panel*\n\n"
        f"🔑 API Key: {api_status}\n"
        f"📌 Mode: *{mode.capitalize()}*\n"
        f"🌐 Bahasa: *{lang}*\n\n"
        f"🎨 Instruction Prompt aktif: *{p_active}*\n"
        f"📝 Instruction Caption aktif: *{c_active}*"
    )

    keyboard = [
        [InlineKeyboardButton("🔑 Set API Key", callback_data="admin_apikey")],
        [
            InlineKeyboardButton(f"{'✅' if mode == 'prompt' else '○'} Prompt", callback_data="admin_mode_prompt"),
            InlineKeyboardButton(f"{'✅' if mode == 'caption' else '○'} Caption", callback_data="admin_mode_caption"),
        ],
        [
            InlineKeyboardButton(f"{'✅' if lang == 'EN' else '○'} EN", callback_data="admin_lang_EN"),
            InlineKeyboardButton(f"{'✅' if lang == 'ID' else '○'} ID", callback_data="admin_lang_ID"),
        ],
        [InlineKeyboardButton("🎨 Kelola Instruction — Prompt", callback_data="admin_si_prompt")],
        [InlineKeyboardButton("📝 Kelola Instruction — Caption", callback_data="admin_si_caption")],
    ]

    markup = InlineKeyboardMarkup(keyboard)
    if edit:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await message.reply_text(text, parse_mode="Markdown", reply_markup=markup)

async def show_si_menu(query, mode_key):
    s = get_settings()
    instructions = s.get(f"{mode_key}_instructions", [])
    active_idx = s.get(f"active_{mode_key}_instruction", -1)
    mode_label = "🎨 Prompt" if mode_key == "prompt" else "📝 Caption"

    text = f"*System Instruction — {mode_label}*\n\n"
    if not instructions:
        text += "_Belum ada instruction. Tambahkan instruction baru._\n"
    else:
        for i, instr in enumerate(instructions):
            mark = "✅" if i == active_idx else "○"
            preview = instr['content'][:60] + "..." if len(instr['content']) > 60 else instr['content']
            text += f"{mark} *{i+1}. {instr['name']}*\n  _{preview}_\n\n"

    keyboard = []
    for i, instr in enumerate(instructions):
        keyboard.append([
            InlineKeyboardButton(f"{'✅' if i == active_idx else '○'} {instr['name']}", callback_data=f"si_select_{mode_key}_{i}"),
            InlineKeyboardButton("✏️", callback_data=f"si_edit_{mode_key}_{i}"),
            InlineKeyboardButton("🗑️", callback_data=f"si_delete_{mode_key}_{i}"),
        ])

    keyboard.append([InlineKeyboardButton("➕ Tambah Instruction", callback_data=f"si_add_{mode_key}")])
    if active_idx >= 0:
        keyboard.append([InlineKeyboardButton("🚫 Nonaktifkan Semua", callback_data=f"si_deactivate_{mode_key}")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali ke Admin", callback_data="admin_back")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def send_si_menu(message, mode_key):
    s = get_settings()
    instructions = s.get(f"{mode_key}_instructions", [])
    active_idx = s.get(f"active_{mode_key}_instruction", -1)
    mode_label = "🎨 Prompt" if mode_key == "prompt" else "📝 Caption"

    text = f"*System Instruction — {mode_label}*\n\n"
    if not instructions:
        text += "_Belum ada instruction._\n"
    else:
        for i, instr in enumerate(instructions):
            mark = "✅" if i == active_idx else "○"
            preview = instr['content'][:60] + "..." if len(instr['content']) > 60 else instr['content']
            text += f"{mark} *{i+1}. {instr['name']}*\n  _{preview}_\n\n"

    keyboard = []
    for i, instr in enumerate(instructions):
        keyboard.append([
            InlineKeyboardButton(f"{'✅' if i == active_idx else '○'} {instr['name']}", callback_data=f"si_select_{mode_key}_{i}"),
            InlineKeyboardButton("✏️", callback_data=f"si_edit_{mode_key}_{i}"),
            InlineKeyboardButton("🗑️", callback_data=f"si_delete_{mode_key}_{i}"),
        ])

    keyboard.append([InlineKeyboardButton("➕ Tambah Instruction", callback_data=f"si_add_{mode_key}")])
    if active_idx >= 0:
        keyboard.append([InlineKeyboardButton("🚫 Nonaktifkan Semua", callback_data=f"si_deactivate_{mode_key}")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali ke Admin", callback_data="admin_back")])

    await message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ─────────────────────────────────────────────
# CALLBACK HANDLER
# ─────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.answer("⛔ Akses ditolak.", show_alert=True)
        return

    data = query.data
    s = get_settings()

    if data == "admin_back":
        await show_admin_menu(query.message, context, edit=True)

    elif data == "admin_apikey":
        context.user_data["awaiting"] = "api_key"
        await query.edit_message_text(
            "🔑 *Set API Key Groq*\n\n"
            "Kirim API Key kamu dari https://console.groq.com\n\n"
            "_(Ketik /cancel untuk batal)_",
            parse_mode="Markdown"
        )

    elif data.startswith("admin_mode_"):
        new_mode = data.replace("admin_mode_", "")
        s["mode"] = new_mode
        save_settings(s)
        await show_admin_menu(query.message, context, edit=True)

    elif data.startswith("admin_lang_"):
        new_lang = data.replace("admin_lang_", "")
        s["language"] = new_lang
        save_settings(s)
        await show_admin_menu(query.message, context, edit=True)

    elif data == "admin_si_prompt":
        await show_si_menu(query, "prompt")

    elif data == "admin_si_caption":
        await show_si_menu(query, "caption")

    elif data.startswith("si_select_"):
        _, _, mode_key, idx = data.split("_", 3)
        idx = int(idx)
        s[f"active_{mode_key}_instruction"] = idx
        save_settings(s)
        await show_si_menu(query, mode_key)

    elif data.startswith("si_deactivate_"):
        mode_key = data.replace("si_deactivate_", "")
        s[f"active_{mode_key}_instruction"] = -1
        save_settings(s)
        await show_si_menu(query, mode_key)

    elif data.startswith("si_add_"):
        mode_key = data.replace("si_add_", "")
        context.user_data["awaiting"] = f"si_add_name_{mode_key}"
        await query.edit_message_text(
            f"➕ *Tambah System Instruction*\n\n"
            f"*Langkah 1/2* — Ketik *nama* instruction:\n\n"
            f"Contoh: `Formal`, `Casual`, `3 Variasi`, `Promosi`\n\n"
            f"_(Ketik /cancel untuk batal)_",
            parse_mode="Markdown"
        )

    elif data.startswith("si_edit_"):
        _, _, mode_key, idx = data.split("_", 3)
        idx = int(idx)
        instr = s.get(f"{mode_key}_instructions", [])[idx]
        context.user_data["awaiting"] = f"si_edit_name_{mode_key}_{idx}"
        context.user_data["edit_old_name"] = instr["name"]
        context.user_data["edit_old_content"] = instr["content"]
        await query.edit_message_text(
            f"✏️ *Edit: {instr['name']}*\n\n"
            f"*Langkah 1/2* — Ketik nama baru:\n"
            f"_(Kirim `-` untuk tetap pakai nama lama)_\n\n"
            f"_(Ketik /cancel untuk batal)_",
            parse_mode="Markdown"
        )

    elif data.startswith("si_delete_"):
        _, _, mode_key, idx = data.split("_", 3)
        idx = int(idx)
        instructions = s.get(f"{mode_key}_instructions", [])
        deleted_name = instructions[idx]["name"]
        instructions.pop(idx)
        s[f"{mode_key}_instructions"] = instructions
        active = s.get(f"active_{mode_key}_instruction", -1)
        if active == idx:
            s[f"active_{mode_key}_instruction"] = -1
        elif active > idx:
            s[f"active_{mode_key}_instruction"] = active - 1
        save_settings(s)
        await query.answer(f"🗑️ '{deleted_name}' dihapus.", show_alert=True)
        await show_si_menu(query, mode_key)

# ─────────────────────────────────────────────
# INPUT HANDLERS (TEXT & DOCUMENT)
# ─────────────────────────────────────────────

async def cmd_simpan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    awaiting = context.user_data.get("awaiting")
    if not awaiting or not ("_content_" in awaiting):
        await update.message.reply_text("❌ Tidak ada draft instruction yang sedang dibuat.")
        return

    s = get_settings()
    content = context.user_data.get("si_temp_content", "").strip()

    if not content:
        await update.message.reply_text("⚠️ Draft masih kosong! Kirim teks atau file .txt terlebih dahulu.")
        return

    if awaiting.startswith("si_add_content_"):
        mode_key = awaiting.replace("si_add_content_", "")
        name = context.user_data.get("si_temp_name", "Instruction")

        instructions = s.get(f"{mode_key}_instructions", [])
        instructions.append({"name": name, "content": content})
        s[f"{mode_key}_instructions"] = instructions
        save_settings(s)

        preview = content[:100] + "..." if len(content) > 100 else content
        await update.message.reply_text(
            f"✅ *'{name}'* berhasil ditambahkan!\n\nIsi: _{preview}_",
            parse_mode="Markdown"
        )
        await send_si_menu(update.message, mode_key)

    elif awaiting.startswith("si_edit_content_"):
        parts = awaiting.split("_")
        mode_key = parts[3]
        idx = int(parts[4])
        name = context.user_data.get("si_temp_name", "")

        if content == "-":
            content = context.user_data.get("edit_old_content", "")

        instructions = s.get(f"{mode_key}_instructions", [])
        instructions[idx] = {"name": name, "content": content}
        s[f"{mode_key}_instructions"] = instructions
        save_settings(s)

        await update.message.reply_text(
            f"✅ *'{name}'* berhasil diperbarui!",
            parse_mode="Markdown"
        )
        await send_si_menu(update.message, mode_key)

    context.user_data.clear()

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    awaiting = context.user_data.get("awaiting")
    if awaiting and ("_content_" in awaiting):
        doc = update.message.document
        if doc.mime_type == "text/plain" or doc.file_name.endswith(".txt"):
            file = await context.bot.get_file(doc.file_id)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(file.file_path)
                text = resp.text

            current = context.user_data.get("si_temp_content", "")
            context.user_data["si_temp_content"] = current + "\n" + text if current else text
            await update.message.reply_text(
                f"📄 Teks dari file '{doc.file_name}' ditambahkan ke draft!\n"
                f"Ketik /simpan jika sudah selesai."
            )
        else:
            await update.message.reply_text("❌ Harap kirim file teks berekstensi .txt")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "/cancel":
        context.user_data.clear()
        await update.message.reply_text("❌ Dibatalkan.")
        return

    awaiting = context.user_data.get("awaiting")

    if not awaiting:
        await update.message.reply_text("📸 Kirim foto untuk dianalisis!")
        return

    text = update.message.text.strip()
    s = get_settings()

    # ── Set API Key ──
    if awaiting == "api_key":
        s["api_key"] = text
        save_settings(s)
        context.user_data.pop("awaiting")
        await update.message.reply_text("✅ API Key berhasil disimpan!")
        await show_admin_menu(update.message, context, edit=False)

    # ── Add Step 1: Nama ──
    elif awaiting.startswith("si_add_name_"):
        mode_key = awaiting.replace("si_add_name_", "")
        context.user_data["si_temp_name"] = text
        context.user_data["awaiting"] = f"si_add_content_{mode_key}"
        context.user_data["si_temp_content"] = ""
        await update.message.reply_text(
            f"➕ *Tambah System Instruction*\n\n"
            f"*Langkah 2/2* — Kirim *isi* instruction untuk *'{text}'*.\n\n"
            f"💡 *TIPS TEKS PANJANG:*\n"
            f"Kirim teksnya sepotong-sepotong di chat ini, ATAU upload file *.txt* berisi instruksinya.\n\n"
            f"👉 Jika semuanya sudah dimasukkan, ketik /simpan\n"
            f"_(Ketik /cancel untuk batal)_",
            parse_mode="Markdown"
        )

    # ── Add/Edit Step 2: Kumpulkan Konten ──
    elif awaiting.startswith("si_add_content_") or awaiting.startswith("si_edit_content_"):
        if text == "-" and awaiting.startswith("si_edit_content_"):
            context.user_data["si_temp_content"] = "-"
            await cmd_simpan(update, context)
            return

        current = context.user_data.get("si_temp_content", "")
        context.user_data["si_temp_content"] = current + "\n" + text if current else text
        await update.message.reply_text(
            "📥 Teks ditambahkan ke draft.\nKirim teks lanjutan, upload .txt, atau ketik /simpan jika selesai."
        )

    # ── Edit Step 1: Nama Baru ──
    elif awaiting.startswith("si_edit_name_"):
        parts = awaiting.split("_")
        mode_key = parts[3]
        idx = int(parts[4])
        old_name = context.user_data.get("edit_old_name", "")
        new_name = old_name if text == "-" or not text else text
        
        context.user_data["si_temp_name"] = new_name
        context.user_data["awaiting"] = f"si_edit_content_{mode_key}_{idx}"
        context.user_data["si_temp_content"] = ""
        old_content = context.user_data.get("edit_old_content", "")
        preview_old = old_content[:150] + "..." if len(old_content) > 150 else old_content

        await update.message.reply_text(
            f"✏️ *Edit: {new_name}*\n\n"
            f"*Langkah 2/2* — Kirim isi baru:\n\n"
            f"Isi saat ini:\n`{preview_old}`\n\n"
            f"💡 Kirim teks sepotong-sepotong ATAU upload file *.txt*.\n"
            f"👉 Jika sudah selesai, ketik /simpan\n"
            f"_(Kirim `-` untuk tetap pakai isi lama)_\n"
            f"_(Ketik /cancel untuk batal)_",
            parse_mode="Markdown"
        )

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("simpan", cmd_simpan))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()

