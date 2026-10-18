# LOE Energy Monitor

[![Tests](https://img.shields.io/github/actions/workflow/status/fastiuk/loe-energy-monitor/tests.yml?branch=main&label=tests&logo=githubactions&logoColor=white)](https://github.com/fastiuk/loe-energy-monitor/actions/workflows/tests.yml)
[![LOE API](https://img.shields.io/github/actions/workflow/status/fastiuk/loe-energy-monitor/live.yml?branch=main&label=LOE%20API&logo=githubactions&logoColor=white)](https://github.com/fastiuk/loe-energy-monitor/actions/workflows/live.yml)
[![Validate](https://img.shields.io/github/actions/workflow/status/fastiuk/loe-energy-monitor/validate.yml?branch=main&label=HACS%20%2B%20hassfest&logo=homeassistant&logoColor=white)](https://github.com/fastiuk/loe-energy-monitor/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/fastiuk/loe-energy-monitor?sort=semver&logo=github&logoColor=white)](https://github.com/fastiuk/loe-energy-monitor/releases)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5?logo=homeassistantcommunitystore&logoColor=white)](#home-assistant)
[![Telegram](https://img.shields.io/badge/Telegram-@loe__grafik__bot-26A5E4?logo=telegram&logoColor=white)](https://t.me/loe_grafik_bot)
[![Bot image](https://img.shields.io/docker/v/fastiuk/loe-grafik-bot?sort=semver&label=bot%20image&logo=docker&logoColor=white)](https://hub.docker.com/r/fastiuk/loe-grafik-bot)
[![License](https://img.shields.io/github/license/fastiuk/loe-energy-monitor)](LICENSE)

Графіки погодинних відключень світла Львівобленерго
([poweron.loe.lviv.ua](https://poweron.loe.lviv.ua/)): інтеграція для [Home Assistant](#home-assistant)
і Python-бібліотека, що завантажує й розбирає графіки. Проєкт неофіційний.

## Telegram-бот

**[@loe_grafik_bot](https://t.me/loe_grafik_bot)** — неофіційний бот на тих самих даних:

- підписка на одну чи кілька груп: 1.1, 1.2, …, 6.2;
- повідомлення, коли опублікували графік на завтра або змінили поточний (видно, що було і що стало);
- нагадування перед вимкненням і увімкненням світла (за 5–60 хв або без нагадувань);
- `/today`, `/tomorrow` — графік для своїх груп або для всіх;
- працює в особистих чатах, групах і каналах.

Відкрийте бота, натисніть **Start** і оберіть свою чергу.

Код бота: [fastiuk/loe-grafik-bot](https://github.com/fastiuk/loe-grafik-bot) ·
Docker-образ для власного екземпляра: [fastiuk/loe-grafik-bot](https://hub.docker.com/r/fastiuk/loe-grafik-bot)
(у тому числі як OCI-контейнер Proxmox без Docker).

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

## Дані

Графіки беруться з того ж неофіційного API, що й сайт ЛОЕ (див. [API.md](API.md)).
Щодня CI робить запити до справжнього API й перевіряє, що сайт досі бере дані звідти і що формат
графіка не змінився — стан видно на беджі **LOE API**.

- Відключення через північ (22:00–24:00 і 00:00–02:00 наступного дня) вважається одним.
- Якщо сайт віддає недоредагований графік (частина груп зникла), ці групи беруться з попередньої
  версії і не вважаються зміненими.
- Cloudflare кешує відповідь API до двох годин; клієнт щоразу просить свіжу копію.

## Бібліотека `loe_core`

```bash
pip install "loe-energy-monitor @ git+https://github.com/fastiuk/loe-energy-monitor@v0.2.1"
```

```python
import asyncio

import aiohttp
from loe_core import LoeClient, current_outage, next_outage, now_kyiv


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        schedules = await LoeClient(session).fetch_schedule()  # {date: DaySchedule}
    days, now = list(schedules.values()), now_kyiv()
    print("зараз:", current_outage(days, "4.2", now))
    print("наступне:", next_outage(days, "4.2", now))


asyncio.run(main())
```

Ще є `diff_schedules` і `merge_schedules`, щоб знаходити зміни між двома завантаженнями —
так їх використовує [бот](https://github.com/fastiuk/loe-grafik-bot).

## Структура

- `src/loe_core` — завантаження та розбір графіка, порівняння версій;
- `custom_components/loe_outages` — інтеграція для Home Assistant з власною копією `loe_core`
  (тест `tests/test_vendored_core.py` стежить, щоб копія не відставала).

## Розробка

```bash
uv sync
uv run pytest
LOE_LIVE=1 uv run pytest tests/test_live_api.py   # запити до справжнього API
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
| `Tests` | кожен push, PR, щодня | тести ядра (Python 3.13) і інтеграції HA (Python 3.14); щодня ще й з найновішою версією HA |
| `LOE API` | push у `main`, щодня | запити до справжнього API ЛОЕ: чи сайт досі бере дані звідти, чи не змінився формат графіка |
| `Validate` | кожен push, PR, щодня | перевірки HACS і hassfest |

Про падіння запланованих запусків GitHub повідомляє листом. Якщо в репозиторії 60 днів не було
комітів, GitHub вимикає заплановані запуски — їх треба ввімкнути знову у вкладці Actions.

Випуск версії: оновіть `version` у `pyproject.toml` і `custom_components/loe_outages/manifest.json`,
закомітьте, створіть тег `vX.Y.Z` і GitHub Release.

## Ліцензія

[MIT](LICENSE)
