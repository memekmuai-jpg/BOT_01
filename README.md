# 🤖 Grok Vision Bot

Telegram bot untuk menganalisa gambar menggunakan AI Grok (xAI).  
Fitur: **Image to Prompt** & **Caption Generator**

---

## ✨ Fitur

- 🎨 **Prompt Mode** — Hasilkan prompt detail dari gambar
- 📝 **Caption Mode** — Hasilkan variasi caption untuk social media
- 🌐 **Bilingual** — Output Bahasa Indonesia atau English
- 📥 **Multi-task Queue** — Proses hingga 10 foto secara antrian (1 per 1)
- ⚙️ **Admin Panel** — Manage API Key, mode, bahasa, dan system instruction via Telegram

---

## 🚀 Deploy ke Railway

### 1. Fork / Clone repo ini ke GitHub

### 2. Buat project baru di [Railway](https://railway.app)
- New Project → Deploy from GitHub Repo
- Pilih repo ini

### 3. Set Environment Variable di Railway
| Variable | Value |
|---|---|
| `TELEGRAM_TOKEN` | Token bot dari @BotFather |
| `ADMIN_ID` | Telegram user ID admin |

> **Catatan:** API Key xAI diset via menu admin di bot Telegram (`/admin`)

### 4. Deploy otomatis!

---

## ⚙️ Admin Commands

- `/admin` — Buka panel admin (hanya admin)
- `/start` — Info bot

### Menu Admin:
- 🔑 Set API Key xAI
- 📌 Switch mode (Prompt / Caption)
- 🌐 Switch bahasa (EN / ID)
- 📋 Kelola System Instruction per mode (tambah, edit, hapus, aktifkan)

---

## 📁 Struktur Project

```
├── bot.py           # Main bot
├── storage.py       # JSON storage helper
├── requirements.txt
├── Procfile
├── railway.json
└── .gitignore
```

---

## 🔑 Environment Variables

| Variable | Keterangan |
|---|---|
| `TELEGRAM_TOKEN` | Token bot Telegram |
| `ADMIN_ID` | ID Telegram admin |
