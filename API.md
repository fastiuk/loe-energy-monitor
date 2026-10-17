# LOE (Львівобленерго) — неофіційне API

Знайдено в JS-бандлі `poweron.loe.lviv.ua` (React SPA). Обидва бекенди — API Platform (Symfony),
відповіді у форматі JSON-LD / Hydra, без авторизації, за Cloudflare.

| База | Призначення |
|---|---|
| `https://api.loe.lviv.ua/api` | CMS: меню, сторінки, **графік ГПВ** |
| `https://power-api.loe.lviv.ua/api` | Довідники адрес, черги по адресі, аварійні/планові відключення |

## Графік погодинних відключень (ГПВ)

```
GET https://api.loe.lviv.ua/api/menus?page=1&type=photo-grafic
```

`hydra:member[0].menuItems`:

| index | name | Що всередині |
|---|---|---|
| 0 | `Today` | `rawHtml` — текстовий графік на сьогодні, `imageUrl` — PNG-картинка |
| 1 | `Arhiv` | `children[]` — історія змін графіка (`"Графік передано HH:MM DD.MM.YYYY"` + `imageUrl`) |
| 2 | `Tomorrow` | те саме на завтра; порожнє, поки не опубліковано |

Приклад `rawHtml`:

```html
<div><p><b>Графік погодинних відключень на 17.10.2026</b></p>
<p><b>Інформація станом на 10:59 17.10.2026</b></p>
<p>Група 1.1. Електроенергії немає з 20:00 до 22:30.</p>
<p>Група 4.2. Електроенергії немає з 10:00 до 12:00, з 21:00 до 22:30.</p>
...
</div>
```

Картинки: `https://api.loe.lviv.ua` + `imageUrl` (desktop) або + `slug` (mobile).

**Кеш:** `cache-control: public, max-age=3600, s-maxage=7200`, Cloudflare кешує. Довільний query-параметр
(`&_=<timestamp>`) дає `cf-cache-status: MISS`, тобто свіжі дані з origin.

## Пошук черги за адресою

```
GET /pw_otgs?pagination=false                                   # громади (~85)
GET /pw_cities?pagination=false&otg.id={otg}                    # населені пункти (otg порожній = всі, ~2000)
GET /pw_streets?pagination=false&city.id={city}                 # вулиці
GET /pw_accounts?pagination=false&city.id={city}&street.id={street}[&buildingName={bld}]
```

`pw_accounts` повертає `buildingName`, `chergGpv` (група ГПВ, напр. `"5.1"` або `"Не входить"`),
`chergGav`, `chergAchr`, `chergGvsp`, `chergSgav` (інші типи черг).

Приклад: Львів — `otg.id=28`, `city.id=693`.

## Поточні відключення по вулицях

```
GET /pw_accidents?page={n}&otg.id={otg}&city.id={city}&street.id={street}
```

Поля: `dateEvent`, `datePlanIn` (план відновлення), `koment` (напр. `"Застосування ГПВ"`),
`typeOff`, `switchSubReason`, `buildingNames`, `street`, `city`. Пагінація: Hydra (`hydra:totalItems`).

## Інше

```
GET /options?option_key=successful_last_synk     # час останньої синхронізації power-api
GET /check-light?number={особовий_рахунок}        # чи є світло за рахунком
GET /last-register-no-power?account={рахунок}
```
