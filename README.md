# LOE Energy Monitor

Графіки погодинних відключень світла Львівобленерго
([poweron.loe.lviv.ua](https://poweron.loe.lviv.ua/)): Telegram-бот і інтеграція для
[Home Assistant](#home-assistant).

**Telegram-бот:**

- підписка на одну чи кілька груп: 1.1, 1.2, …, 6.2;
- повідомлення, коли опублікували графік на завтра або змінили поточний (видно, що було і що стало);
- нагадування за N хвилин до вимкнення й до увімкнення світла (вибір: 5–60 хв або вимкнено);
- `/today`, `/tomorrow` — графік для своїх груп або для всіх;
- працює в особистих чатах, групах і каналах.

Дані беруться з того ж неофіційного API, що й сайт: див. [API.md](API.md).

## Запуск

1. Створіть бота в [@BotFather](https://t.me/BotFather) і скопіюйте токен.
2. Візьміть [`docker-compose.yml`](docker-compose.yml) і [`.env.example`](.env.example), налаштуйте й запустіть:

   ```bash
   cp .env.example .env   # вкажіть BOT_TOKEN
   docker compose pull
   docker compose up -d
   ```

   Готовий образ `ghcr.io/fastiuk/loe-energy-monitor` публікується для кожної версії
   (amd64 і arm64, тож підійде й Raspberry Pi). Щоб зібрати з коду: `docker compose up -d --build`.

3. Напишіть боту `/start` і оберіть групи.

База (підписки, останній графік) зберігається в томі `bot-data`.
Оновлення: `docker compose pull && docker compose up -d`.

### У групі або каналі

Бот може писати в спільний чат замість особистих повідомлень:

- **Група:** додайте бота — він сам надішле вибір груп. Обирати можуть лише адміністратори.
- **Канал:** додайте бота адміністратором з правом публікації й опублікуйте в каналі `/start`.
  Після налаштування службові дописи можна видалити.

## Команди

| Команда | |
|---|---|
| `/start`, `/groups` | обрати групи |
| `/today`, `/tomorrow` | графік на сьогодні / завтра |
| `/reminder` | за скільки хвилин нагадувати |
| `/stop` | скасувати підписку |

## Налаштування

| Змінна | За замовчуванням | |
|---|---|---|
| `BOT_TOKEN` | — | токен бота, обовʼязково |
| `POLL_INTERVAL` | `180` | як часто перевіряти графік, секунд (мінімум 60) |
| `DEFAULT_REMINDER` | `15` | нагадування для нових чатів, хвилин (`0` — вимкнено) |
| `DB_PATH` | `/data/bot.db` | файл SQLite |
| `LOG_LEVEL` | `INFO` | |

## Як це працює

Кожні `POLL_INTERVAL` секунд бот завантажує графік, порівнює з попереднім і повідомляє лише
про групи, що змінилися. Під час першого запуску він тільки запамʼятовує поточний графік.
Якщо сайт віддає недоредагований графік (частина груп зникла), ці групи не вважаються зміненими.
Відключення через північ (22:00–24:00 і 00:00–02:00 наступного дня) вважається одним.

## Home Assistant

Інтеграція `loe_outages` додає для обраної групи пристрій «LOE 4.2» з такими сутностями:

| Сутність | |
|---|---|
| `binary_sensor` «Відключення зараз» | увімкнено, поки триває відключення за графіком; атрибути `today` / `tomorrow` — інтервали текстом (`tomorrow` = `null`, поки графік не опубліковано) |
| `calendar` «Графік відключень» | кожне відключення — подія; відключення через північ — одна подія |
| `sensor` «Наступне відключення» | час початку наступного відключення |
| `sensor` «Увімкнення світла» | коли мають увімкнути світло: кінець поточного або наступного відключення |
| `sensor` «Без світла сьогодні» | скільки годин без світла сьогодні за графіком |
| `sensor` «Графік оновлено» | коли ЛОЕ востаннє оновили графік (діагностика) |

Стан змінюється рівно на межах відключень, а не лише після чергового опитування.
Якщо API ЛОЕ недоступний, сутності показують останній відомий графік.

### Встановлення через HACS

1. HACS → ⋮ → **Custom repositories** → `https://github.com/fastiuk/loe-energy-monitor`,
   тип **Integration**.
2. Знайдіть **LOE Outages** у HACS, встановіть і перезапустіть Home Assistant.
3. **Налаштування → Пристрої та служби → Додати інтеграцію → LOE Outages**, оберіть групу.
   Для кількох груп додайте інтеграцію кілька разів.

Інтервал оновлення (за замовчуванням 5 хв) змінюється в параметрах інтеграції.

Без HACS: скопіюйте `custom_components/loe_outages` у `config/custom_components/`.

### Приклад автоматизації

```yaml
automation:
  - alias: "Зарядити павербанк перед відключенням"
    triggers:
      - trigger: calendar
        event: start
        offset: "-01:00:00"
        entity_id: calendar.loe_4_2_outage_schedule
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.powerbank_charger
```

## Структура

- `src/loe_core` — завантаження та розбір графіка, порівняння версій; не залежить ні від Telegram,
  ні від Home Assistant;
- `src/loe_bot` — Telegram-бот (aiogram 3, SQLite);
- `custom_components/loe_outages` — інтеграція для Home Assistant з власною копією `loe_core`
  (тест `tests/test_vendored_core.py` стежить, щоб копія не відставала).

## Розробка

```bash
uv sync
uv run pytest
BOT_TOKEN=... uv run loe-bot
```

Тести Home Assistant запускаються в окремому середовищі (HA вимагає Python 3.14 і свої версії
залежностей):

```bash
uv run --isolated --no-project --python 3.14 \
  --with pytest-homeassistant-custom-component==0.13.373 \
  pytest tests/ha -o asyncio_mode=auto
```

Після змін у `src/loe_core` оновіть копію:

```bash
rm -rf custom_components/loe_outages/loe_core && cp -R src/loe_core custom_components/loe_outages/loe_core
```

### CI

| Workflow | Коли | Що робить |
|---|---|---|
| `Tests` | кожен push у гілку, PR | тести ядра й бота (Python 3.13) і тести інтеграції HA (Python 3.14) |
| `Validate` | кожен push, PR, щодня | перевірки HACS і hassfest |
| `Docker` | тег `v*` або вручну | тести, потім збірка образу для amd64/arm64 і публікація в GHCR |

Випуск версії: оновіть `version` у `pyproject.toml` і `custom_components/loe_outages/manifest.json`,
закомітьте, створіть тег `vX.Y.Z` і GitHub Release. Образ отримає теги `X.Y.Z`, `X.Y` і `latest`.

## Ліцензія

[MIT](LICENSE)
