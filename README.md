# chat_bot

Репозиторий AI-консультанта интернет-магазина «Веста Трейдинг».

**Весь код и вся документация — в [`vestatrade_bot/`](vestatrade_bot/README.md).**
Начинайте оттуда: архитектура, запуск, API, конфигурация, тесты и оценка
качества описаны в [vestatrade_bot/README.md](vestatrade_bot/README.md).

```bash
cd vestatrade_bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## Что лежит в корне

| Файл | Что это |
|---|---|
| `vestatrade_bot/` | Приложение: FastAPI-сервис, агенты диалога, тесты, скрипты, отчёты |
| `full_cart_agent_spec.md` | ТЗ на агента-комплектатора полной корзины: разбор живого теста от 27.07.2026 и требования к поведению |
| `bot_test_report_2026-07-27.md` | Отчёт живого тестирования бота на проде |
| `img.png` | Скриншот интерфейса |
| `.env` | Локальные ключи. **Приложение его не читает** — оно загружает `vestatrade_bot/.env` |

> Корневой `.env` — исторический остаток. `app/config.py` берёт настройки строго
> из `vestatrade_bot/.env`, поэтому правки в корневом файле ни на что не влияют.
