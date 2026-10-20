<p align="center"><img src="assets/icon.png" width="128" height="128" alt=""></p>

# Ukraine Energy Monitor — графіки відключень світла

[![Tests](https://img.shields.io/github/actions/workflow/status/fastiuk/ukraine-energy-monitor/tests.yml?branch=main&label=tests&logo=githubactions&logoColor=white)](https://github.com/fastiuk/ukraine-energy-monitor/actions/workflows/tests.yml)
[![Sources](https://img.shields.io/github/actions/workflow/status/fastiuk/ukraine-energy-monitor/sources.yml?branch=main&label=джерела&logo=githubactions&logoColor=white)](https://github.com/fastiuk/ukraine-energy-monitor/actions/workflows/sources.yml)
[![Validate](https://img.shields.io/github/actions/workflow/status/fastiuk/ukraine-energy-monitor/validate.yml?branch=main&label=HACS%20%2B%20hassfest&logo=homeassistant&logoColor=white)](https://github.com/fastiuk/ukraine-energy-monitor/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/fastiuk/ukraine-energy-monitor?sort=semver&logo=github&logoColor=white)](https://github.com/fastiuk/ukraine-energy-monitor/releases)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5?logo=homeassistantcommunitystore&logoColor=white)](#home-assistant)
[![Telegram](https://img.shields.io/badge/Telegram-@grafik__ua__bot-26A5E4?logo=telegram&logoColor=white)](https://t.me/grafik_ua_bot)
[![License](https://img.shields.io/github/license/fastiuk/ukraine-energy-monitor)](LICENSE)

Графіки погодинних відключень світла (ГПВ) для всіх областей України: інтеграція для
[Home Assistant](#home-assistant) і Python-бібліотека, що бере графіки з сайтів обленерго, ДТЕК і
YASNO, розбирає їх в одну модель і знаходить чергу за адресою. Проєкт неофіційний.

Telegram-бот на цих даних: **[@grafik_ua_bot](https://t.me/grafik_ua_bot)**.

## Статус джерел

Щогодини Telegram-бот із сервера в Україні опитує джерела кожного оператора й оновлює бейджі
(докладно, з відповіддю кожного джерела, — у
[ukraine-energy-monitor-status](https://github.com/fastiuk/ukraine-energy-monitor-status)).
**працює** — основне джерело відповідає у відомому форматі; **резервне джерело** — основне не
відповідає, але працює запасне; **заблоковано захистом сайту** — сайт закрився від автоматичних
запитів; **згодом** / **не публікує** / **недоступно** — джерела поки немає; **тимчасово окупована**
— АР Крим і Севастополь у списку, щоб кожен знайшов свій дім: графіки буде після повернення
додому 🇺🇦.

**API з офіційного ресурсу:** ✅ — графік приходить даними (JSON) з API самого оператора; ❌ — його
доводиться читати зі сторінки сайту, картинки, Telegram-каналу оператора чи відкритого дзеркала.
🔎 — чергу можна знайти за адресою.

| Область | Оператор | Статус | API з офіційного ресурсу | 🔎 |
|---|---|---|---|---|
| м. Київ | ДТЕК Київські електромережі | [![dtek-kem](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fdtek-kem.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ✅ | 🔎 |
| АР Крим | — | [![crimea](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fcrimea.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | — |  |
| Київська | ДТЕК Київські регіональні електромережі | [![dtek-krem](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fdtek-krem.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ |  |
| Вінницька | Вінницяобленерго | [![vinnytsia-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fvinnytsia-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ | 🔎 |
| Волинська | Волиньобленерго | [![volyn-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fvolyn-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ |  |
| Дніпропетровська | ДТЕК Дніпровські електромережі | [![dtek-dnem](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fdtek-dnem.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ✅ | 🔎 |
| Дніпропетровська | Центральна енергетична компанія (ЦЕК) | [![cek](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fcek.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ✅ | 🔎 |
| Донецька | ДТЕК Донецькі електромережі | [![dtek-dem](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fdtek-dem.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | — |  |
| Житомирська | Житомиробленерго | [![zhytomyr-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fzhytomyr-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ | 🔎 |
| Закарпатська | Закарпаттяобленерго | [![zakarpattia-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fzakarpattia-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ |  |
| Запорізька | Запоріжжяобленерго | [![zaporizhzhia-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fzaporizhzhia-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ |  |
| Івано-Франківська | Прикарпаттяобленерго | [![prykarpattia-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fprykarpattia-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ✅ |  |
| Кіровоградська | Кіровоградобленерго | [![kirovohrad-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fkirovohrad-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ |  |
| Луганська | Луганське енергетичне об'єднання | [![luhansk-leo](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fluhansk-leo.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | — |  |
| Львівська | Львівобленерго | [![lviv-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Flviv-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ✅ | 🔎 |
| Миколаївська | Миколаївобленерго | [![mykolaiv-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fmykolaiv-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ✅ | 🔎 |
| Одеська | ДТЕК Одеські електромережі | [![dtek-oem](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fdtek-oem.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ |  |
| Полтавська | Полтаваобленерго | [![poltava-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fpoltava-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ | 🔎 |
| Рівненська | Рівнеобленерго | [![rivne-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Frivne-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ |  |
| Сумська | Сумиобленерго | [![sumy-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fsumy-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ |  |
| Тернопільська | Тернопільобленерго | [![ternopil-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fternopil-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ✅ | 🔎 |
| Харківська | Харківобленерго | [![kharkiv-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fkharkiv-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ |  |
| Херсонська | Херсонобленерго | [![kherson-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fkherson-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | — |  |
| Хмельницька | Хмельницькобленерго | [![khmelnytskyi-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fkhmelnytskyi-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ | 🔎 |
| Черкаська | Черкасиобленерго | [![cherkasy-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fcherkasy-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ✅ |  |
| Чернівецька | Чернівціобленерго | [![chernivtsi-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fchernivtsi-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ |  |
| Чернігівська | Чернігівобленерго | [![chernihiv-oe](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fchernihiv-oe.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | ❌ |  |
| м. Севастополь | — | [![sevastopol](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2Fsevastopol.json&label=&cacheSeconds=1800)](docs/SOURCES.md) | — |  |

Де і як береться графік кожного оператора — [docs/SOURCES.md](docs/SOURCES.md). Захист сайтів від
ботів не обходимо: для Київщини, де сайт ДТЕК закритий, дані беруться з відкритого дзеркала
[outage-data-ua](https://github.com/Baskerville42/outage-data-ua).

## Home Assistant

Інтеграція «Ukraine Power Outages» додає для обраної черги пристрій з такими сутностями:

| Сутність | |
|---|---|
| `binary_sensor` «Відключення зараз» | увімкнено, поки триває відключення за графіком; атрибути `today` / `tomorrow` — інтервали текстом, `today_possible` / `tomorrow_possible` — можливі відключення (`null`, поки день не опубліковано) |
| `binary_sensor` «Можливе відключення зараз» | для операторів, що публікують «можливі» відключення (вимкнена за замовчуванням) |
| `calendar` «Графік відключень» | кожне відключення — подія; можливі позначені; відключення через північ — одна подія |
| `sensor` «Наступне відключення» | час початку наступного відключення |
| `sensor` «Увімкнення світла» | коли мають увімкнути світло: кінець поточного або наступного відключення |
| `sensor` «Без світла сьогодні» | скільки годин без світла сьогодні за графіком |
| `sensor` «Графік оновлено» | коли оператор востаннє оновив графік (діагностика) |
| `sensor` «Джерело даних» | з якого джерела прийшли дані (діагностика, вимкнена за замовчуванням) |

Стан змінюється рівно на межах відключень, а не лише після чергового опитування. Якщо сайт
оператора недоступний, сутності показують останній відомий графік. Кілька черг одного оператора
опитують його сайт один раз.

### Встановлення через HACS

1. HACS → ⋮ → **Custom repositories** → `https://github.com/fastiuk/ukraine-energy-monitor`,
   тип **Integration**.
2. Знайдіть **Ukraine Power Outages** у HACS, встановіть і перезапустіть Home Assistant.
3. **Налаштування → Пристрої та служби → Додати інтеграцію → Ukraine Power Outages**: оберіть
   область, оператора (якщо їх кілька) і чергу — зі списку або за адресою. Для кількох черг
   додайте інтеграцію кілька разів.

Тернопільобленерго віддає графік лише для конкретної адреси, тож його черга додається тільки
пошуком за адресою; адреса зберігається в налаштуваннях інтеграції. Пошук не використовує
особового рахунку чи інших персональних даних.

Інтервал оновлення (за замовчуванням 5 хв, не частіше, ніж дозволяє джерело) змінюється в
параметрах інтеграції.

Без HACS: скопіюйте `custom_components/ukraine_outages` у `config/custom_components/`.

### Приклад автоматизації

```yaml
automation:
  - alias: "Зарядити павербанк перед відключенням"
    triggers:
      - trigger: calendar
        event: start
        offset: "-01:00:00"
        entity_id: calendar.m_kiiv_grupa_19_1_outage_schedule
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.powerbank_charger
```

## Бібліотека `ua_outages`

```bash
pip install "ukraine-energy-monitor @ https://github.com/fastiuk/ukraine-energy-monitor/archive/refs/tags/v0.3.1.tar.gz"
```

```python
import asyncio

from ua_outages import Fetcher, current_outage, next_outage, now_kyiv, operator
from ua_outages.http import new_session


async def main() -> None:
    kyiv = operator("dtek-kem")
    source = kyiv.sources[0]
    async with new_session() as session:
        schedules = await source.fetch(Fetcher(session, source.hosts), now_kyiv())
    days, now = list(schedules.values()), now_kyiv()
    print("зараз:", current_outage(days, "19.1", now))
    print("наступне:", next_outage(days, "19.1", now))


asyncio.run(main())
```

Ще є реєстр усіх регіонів і операторів (`ua_outages.REGIONS`, `OPERATORS`), пошук черги за
адресою (`Operator.lookup`) і `diff_schedules` / `merge_schedules`, щоб знаходити зміни між двома
завантаженнями.

Відповіді сайтів — недовірені дані: запити лише до оголошених хостів джерела, обмеження розміру й
часу відповіді, розбір лінійний за часом, жоден текст із джерела не стає розміткою.

## Структура

- `src/ua_outages` — моделі, джерела (по модулю на тип сайту), пошук за адресою, реєстр регіонів;
- `custom_components/ukraine_outages` — інтеграція для Home Assistant з власною копією
  `ua_outages` (тест `tests/test_vendored_core.py` стежить, щоб копія не відставала);
- `scripts/source_status.py` — перевірка джерел (`ua_outages.status`), та сама, що публікує бот.

## Розробка

```bash
uv sync
uv run pytest
LIVE=1 uv run pytest tests/test_live.py      # запити до справжніх джерел
uv run python scripts/source_status.py       # статус кожного оператора
```

Тести Home Assistant запускаються в окремому середовищі (HA вимагає Python 3.14 і свої версії
залежностей):

```bash
uv run --isolated --no-project --python 3.14 \
  --with pytest-homeassistant-custom-component==0.13.373 \
  pytest tests/ha -o asyncio_mode=auto
```

Після змін у `src/ua_outages` оновіть копію:

```bash
rm -rf custom_components/ukraine_outages/ua_outages && cp -R src/ua_outages custom_components/ukraine_outages/ua_outages
```

### CI

| Workflow | Коли | Що робить |
|---|---|---|
| `Tests` | кожен push, PR, щодня | тести ядра (Python 3.13) і інтеграції HA (Python 3.14); щодня ще й з найновішою версією HA |
| `Sources` | push у `main`, кожні 3 год, щодня | перевірка кожного оператора з-за кордону (сайти, що відповідають лише з України, пропускаються); щодня — детальні живі тести й адресні довідники. Бейджі публікує бот |
| `Validate` | кожен push, PR, щодня | перевірки HACS і hassfest |

Про падіння запланованих запусків GitHub повідомляє листом. Якщо в репозиторії 60 днів не було
комітів, GitHub вимикає заплановані запуски — їх треба ввімкнути знову у вкладці Actions.

Випуск версії: оновіть `version` у `pyproject.toml` і `custom_components/ukraine_outages/manifest.json`,
закомітьте, створіть тег `vX.Y.Z` і GitHub Release.

## Ліцензія

[MIT](LICENSE). Дані графіків належать операторам систем розподілу.
