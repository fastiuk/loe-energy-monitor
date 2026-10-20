# Архітектура `ua_outages`

Бібліотека перетворює те, що публікує кожен оператор (API, сторінку сайту, картинку, пост у
Telegram-каналі, відкрите дзеркало), на одну модель: `{день: DaySchedule}`, де для кожної черги —
інтервали відключень. Нею користуються інтеграція Home Assistant з цього репозиторію,
Telegram-бот [@grafik_ua_bot](https://t.me/grafik_ua_bot) і перевірка стану джерел (бейджі в README).
Як читається кожен оператор — [SOURCES.md](SOURCES.md).

## Як витягується графік

Від ресурсу оператора до спільної моделі. Кожен крок не довіряє попередньому: `Fetcher` пускає
лише на хости самого джерела, розбір обмежений за розміром і часом, а все, що не схоже на відомий
формат, стає `SourceFormatError`, а не «графіком». Довідники адрес (`AddressLookup`, «адреса →
черга») ходять через той самий `Fetcher`; `status` проганяє кожне джерело й дає бейджі.

```mermaid
flowchart LR
    sites(["Ресурси операторів<br/>API (JSON) · сторінка сайту, RSS<br/>картинка (PNG, XLSX) · Telegram-канал<br/>неофіційне дзеркало"])

    subgraph core["ua_outages"]
        registry["registry<br/>Region → Operator →<br/>джерела по черзі"]
        fetcher["Fetcher<br/>лише хости джерела,<br/>https:443, ліміти, certifi"]
        provider["Provider.fetch()<br/>адаптер джерела"]
        parsers["Розбір<br/>_html · _text<br/>_image · _xlsx"]
        model["DaySchedule<br/>черга → Outage[]"]
        timeline["timeline<br/>trim · merge ·<br/>diff · events"]
    end

    ha(["Home Assistant<br/>SourceHub → OutageCoordinator →<br/>сенсори, календар"])
    bot(["Telegram-бот<br/>Monitor → сповіщення"])
    badges(["Бейджі стану<br/>ukraine-energy-monitor-status"])

    sites --> fetcher --> provider --> parsers --> model --> timeline
    registry -. "основне, далі резервні" .-> provider
    timeline --> ha
    timeline --> bot
    timeline --> badges
```

## Модель і джерела

Головні класи. Кожен оператор має впорядкований список джерел: перше — основне, решта — резерв,
до якого доходить черга, коли попереднє не відповідає. Конкретних адаптерів понад двадцять; на
діаграмі — по одному кожного виду (`Kind`), решта — у [SOURCES.md](SOURCES.md).

```mermaid
classDiagram
    direction TB

    class Region {
        +str id
        +str name
        +tuple operators
        +bool occupied
    }
    class Operator {
        +str id
        +str name
        +tuple groups
        +tuple~Provider~ sources
        +AddressLookup lookup
        +str note
        +available() bool
        +by_address() bool
        +official() bool
        +poll_interval() int
    }
    class Provider {
        <<abstract>>
        +frozenset hosts
        +Kind kind
        +int min_interval
        +bool ukraine_only
        +bool hours_on_picture
        +name() str
        +link() str
        +official() bool
        +fetch(http, now) dict
    }
    class AddressSource {
        <<abstract>>
        +float request_gap
        +valid_address(address) bool
        +fetch_address(http, now, address) dict
    }
    class Kind {
        <<enumeration>>
        API
        PAGE
        PICTURE
        TELEGRAM
        MIRROR
    }
    class AddressLookup {
        <<abstract>>
        +tuple~Step~ steps
        +frozenset hosts
        +options(http, path, query) list~Choice~
        +resolve(http, path) Resolution
    }
    class Step {
        +str prompt
        +bool browse
        +bool house
    }
    class Choice {
        +str id
        +str label
    }
    class Resolution {
        +tuple groups
        +str address
        +str reason
    }

    class Fetcher {
        +frozenset hosts
        +allowed(url) bool
        +get(url) Response
        +post(url, data) Response
    }
    class CachingFetcher {
        +ResponseCache cache
        +Throttle throttle
    }
    class Response {
        +str url
        +int status
        +bytes body
        +text() str
        +json() object
    }
    class FetchError
    class SourceFormatError
    class SourceBlocked

    class DaySchedule {
        +date day
        +datetime updated_at
        +dict groups
        +dict unknown
        +tuple notes
        +str image_url
        +outages(group) tuple~Outage~
        +mentioned_groups() list
    }
    class Outage {
        +datetime start
        +datetime end
        +bool possible
        +duration() timedelta
    }
    class DayChange {
        +DaySchedule schedule
        +DaySchedule previous
        +tuple groups
        +published() bool
    }

    class Yasno {
        API
    }
    class Zhytomyr {
        сторінка сайту
    }
    class SumyNews {
        картинка з сайту
    }
    class Kirovohrad {
        Telegram-канал
    }
    class OutageDataUa {
        неофіційне дзеркало
    }
    class Ternopil {
        API, графік адреси
    }

    Region "1" o-- "1..*" Operator : оператори регіону
    Operator "1" *-- "0..*" Provider : основне, далі резервні
    Operator "1" o-- "0..1" AddressLookup : черга за адресою
    Provider --> Kind
    Provider <|-- AddressSource
    Provider <|-- Yasno
    Provider <|-- Zhytomyr
    Provider <|-- SumyNews
    Provider <|-- Kirovohrad
    Provider <|-- OutageDataUa
    AddressSource <|-- Ternopil
    Provider ..> Fetcher : лише свої hosts
    Provider ..> DaySchedule : повертає за днями
    Provider ..> SourceFormatError : незнайомий формат
    AddressLookup ..> Fetcher
    AddressLookup "1" *-- "1..*" Step
    AddressLookup ..> Choice : варіанти кроку
    AddressLookup ..> Resolution : черги адреси
    Fetcher <|-- CachingFetcher
    Fetcher ..> Response
    Fetcher ..> FetchError
    FetchError <|-- SourceFormatError
    FetchError <|-- SourceBlocked
    DaySchedule "1" *-- "0..*" Outage : відключення черги
    DayChange --> DaySchedule : що змінилось
```

## Модулі

| Модуль | Що в ньому |
|---|---|
| `models.py` | `Outage`, `Slot`, `DaySchedule`: спільна модель, час Києва |
| `http.py` | `Fetcher` (дозволені хости, https:443, ліміти, TLS із `certifi`), `CachingFetcher`, `ResponseCache`, `Throttle`, `redact` |
| `providers/base.py` | `Provider`, `AddressSource`, `AddressLookup` і їхні типи, `Kind`, помилки |
| `providers/*.py` | адаптер кожного джерела й довідника адрес |
| `providers/_html.py`, `_text.py`, `_image.py`, `_xlsx.py` | безпечний розбір HTML, тексту, PNG і XLSX з лімітами |
| `registry.py` | `Region`, `Operator`, усі області й оператори, порядок джерел |
| `timeline.py` | `trim_schedules`, `merge_schedules`, `diff_schedules` → `DayChange`, `group_events`, `current_outage`, `next_outage` |
| `status.py` | перевірка кожного джерела, бейджі, таблиця README |
| `custom_components/ukraine_outages/` | інтеграція Home Assistant: `SourceHub` (одне звернення на оператора для всіх записів) → `OutageCoordinator` → сенсори, бінарні сенсори, календар; власна копія `ua_outages` |
