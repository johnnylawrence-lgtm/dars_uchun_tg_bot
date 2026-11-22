# Coding Arena — Quiz Bot

Telegram bot. **Coding Arena** deb nomlangan quiz bot. Admin panel, kod arena
lari (arena qo'shish, ochish/yopish, savollar, savol o'chirish) va userlar
boshqaruvi.

## O'rnatish

```
pip install -r requirements.txt
```

## Sozlamalar (`.env`)

- `BOT_TOKEN` — BotFather dan olingan.
- `DATABASE_URL` — PostgreSQL ulab, masalan:
  `postgresql://postgres:root@localhost:5432/coding_arena`.
- `ADMIN_ID` — admin sifatida ishlaydigan foydalanuvchining telegram ID.

Jadvalarni `users`, `arenas`, `questions` bot ishga tushganda avtomatik yaratadi.

## Islanishi

```
python bot.py
```

## Litsenziya

MIT License — [LICENSE](LICENSE) fayli.
