"""Every region of Ukraine, its distribution operators and where their schedules come from.

Region and operator ids are stored in the bot's database and in buttons: never rename them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .providers.base import AddressLookup, AddressSource, Provider
from .providers.cherkasy import Cherkasy
from .providers.chernivtsi import Chernivtsi
from .providers.dtek import DtekPage, OutageDataUa, cek_groups
from .providers.kharkiv import Kharkiv
from .providers.loe import Loe, LoeLookup
from .providers.mykolaiv import Mykolaiv, MykolaivLookup
from .providers.poltava import Poltava, PoltavaLookup
from .providers.prykarpattia import Prykarpattia
from .providers.rivne import Rivne
from .providers.sumy import Sumy
from .providers.ternopil import Ternopil, TernopilLookup
from .providers.volyn import Volyn
from .providers.yasno import Yasno, YasnoLookup
from .providers.zaporizhzhia import Zaporizhzhia
from .providers.zhytomyr import Zhytomyr, ZhytomyrLookup

#: 6 queues × 2 subqueues, the layout almost every operator uses.
STANDARD_GROUPS: tuple[str, ...] = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))

NOT_PUBLISHED = "Оператор не публікує графіків погодинних відключень."
LATER = "Графіки цього оператора додамо згодом."
#: Temporarily occupied territories: shown so every Ukrainian finds their home, never fetched.
OCCUPIED = "Тимчасово окупована територія. Буде доступно після повернення додому 🇺🇦"


@dataclass(frozen=True)
class Operator:
    id: str
    name: str
    region: str
    site: str
    #: Primary source first, then fallbacks. Empty: no schedules (see ``note``).
    sources: tuple[Provider, ...] = ()
    groups: tuple[str, ...] = STANDARD_GROUPS
    #: How the operator calls a group: "черга" or "група".
    group_label: str = "черга"
    #: Why there are no schedules, or a caveat about them.
    note: str | None = None
    #: The operator's open directory "address → group", if there is one.
    lookup: AddressLookup | None = None

    @property
    def available(self) -> bool:
        return bool(self.sources)

    @property
    def by_address(self) -> bool:
        """Schedules come per address: subscribing needs one (found with ``lookup``)."""
        return bool(self.sources) and all(isinstance(s, AddressSource) for s in self.sources)

    @property
    def poll_interval(self) -> int:
        return max(s.min_interval for s in self.sources) if self.sources else 0


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    operators: tuple[str, ...]
    #: Temporarily occupied as a whole: no sources, and occupation authorities' sites are
    #: never read (see ``http.FORBIDDEN_SUFFIXES``).
    occupied: bool = False


OPERATORS: tuple[Operator, ...] = (
    Operator("crimea", "Автономна Республіка Крим", "crimea", "", note=OCCUPIED),
    Operator(
        "dtek-kem", "ДТЕК Київські електромережі", "kyiv", "https://www.dtek-kem.com.ua/ua/shutdowns",
        sources=(Yasno(region=25, dso=902), OutageDataUa("kyiv")),
        groups=tuple(f"{n}.1" for n in range(1, 61)),
        group_label="група",
        lookup=YasnoLookup(25, 902, cities=False, street_example="Хрещатик"),
    ),
    Operator(
        "dtek-krem", "ДТЕК Київські регіональні електромережі", "kyivska",
        "https://www.dtek-krem.com.ua/ua/shutdowns",
        sources=(OutageDataUa("kyiv-region"),),
    ),
    Operator(
        "vinnytsia-oe", "Вінницяобленерго", "vinnytska", "https://www.voe.com.ua/disconnection/detailed",
        note="Вінницяобленерго показує графік лише для окремої адреси на своєму сайті, "
        "тож бот не може його отримати.",
    ),
    Operator(
        "volyn-oe", "Волиньобленерго", "volynska",
        "https://energy.volyn.ua/spozhyvacham/perervy-u-elektropostachanni/hrafik-vidkliuchen/",
        sources=(Volyn(),),
    ),
    Operator(
        "dtek-dnem", "ДТЕК Дніпровські електромережі", "dnipropetrovska",
        "https://www.dtek-dnem.com.ua/ua/shutdowns",
        sources=(Yasno(region=3, dso=301), DtekPage("dnem"), OutageDataUa("dnipro")),
        lookup=YasnoLookup(3, 301, cities=True, city_example="Дніпро", street_example="Шевченка"),
    ),
    Operator(
        "cek", "Центральна енергетична компанія (ЦЕК)", "dnipropetrovska", "https://cek.dp.ua",
        sources=(Yasno(region=3, dso=303), DtekPage("dnem", cek_groups), OutageDataUa("dnipro", cek_groups)),
        lookup=YasnoLookup(3, 303, cities=True, city_example="Павлоград", street_example="Шевченка"),
    ),
    Operator(
        "dtek-dem", "ДТЕК Донецькі електромережі", "donetska", "https://www.dtek-dem.com.ua/ua/shutdowns",
        note=NOT_PUBLISHED,
    ),
    Operator(
        "zhytomyr-oe", "Житомиробленерго", "zhytomyrska", "https://www.ztoe.com.ua/unhooking-search.php",
        sources=(Zhytomyr(),),
        lookup=ZhytomyrLookup(),
    ),
    Operator(
        "zakarpattia-oe", "Закарпаттяобленерго", "zakarpatska",
        "https://zakarpat.energy/customers/break-in-electricity-supply/schedule/",
        note="Закарпаттяобленерго зараз не застосовує графіків погодинних відключень. "
        "Коли почне — додамо.",
    ),
    Operator(
        "zaporizhzhia-oe", "Запоріжжяобленерго", "zaporizka", "https://www.zoe.com.ua/outage/",
        sources=(Zaporizhzhia(),),
    ),
    Operator(
        "prykarpattia-oe", "Прикарпаттяобленерго", "ivano-frankivska", "https://svitlo.oe.if.ua",
        sources=(Prykarpattia(),),
    ),
    Operator("kirovohrad-oe", "Кіровоградобленерго", "kirovohradska", "https://kiroe.com.ua/electricity-blackout", note=LATER),
    Operator("luhansk-leo", "Луганське енергетичне об'єднання", "luhanska", "", note=NOT_PUBLISHED),
    Operator(
        "lviv-oe", "Львівобленерго", "lvivska", "https://poweron.loe.lviv.ua",
        sources=(Loe(),), group_label="група", lookup=LoeLookup(),
    ),
    Operator(
        "mykolaiv-oe", "Миколаївобленерго", "mykolaivska", "https://off.energy.mk.ua",
        sources=(Mykolaiv(),),
        lookup=MykolaivLookup(),
    ),
    Operator(
        "dtek-oem", "ДТЕК Одеські електромережі", "odeska", "https://www.dtek-oem.com.ua/ua/shutdowns",
        sources=(DtekPage("oem"), OutageDataUa("odesa")),
    ),
    Operator(
        "poltava-oe", "Полтаваобленерго", "poltavska", "https://www.poe.pl.ua/disconnection/power-outages/",
        sources=(Poltava(),),
        lookup=PoltavaLookup(),
    ),
    Operator(
        "rivne-oe", "Рівнеобленерго", "rivnenska", "https://www.roe.vsei.ua/disconnections",
        sources=(Rivne(),),
    ),
    Operator(
        "sumy-oe", "Сумиобленерго", "sumska", "https://www.soe.com.ua/consumers/shutdowns",
        sources=(Sumy(),),
        note="Сумиобленерго публікує лише графік на поточний день.",
    ),
    Operator(
        "ternopil-oe", "Тернопільобленерго", "ternopilska", "https://poweron.toe.com.ua",
        sources=(Ternopil(),),
        lookup=TernopilLookup(),
        note="Тернопільобленерго показує графік лише для конкретної адреси, тож бот підписує "
        "за адресою: знайдіть свій будинок, і бот стежитиме за чергою цієї адреси.",
    ),
    Operator(
        "kharkiv-oe", "Харківобленерго", "kharkivska", "https://oblenergo.kharkiv.ua/uk/media_centre/news",
        sources=(Kharkiv(),),
    ),
    Operator(
        "kherson-oe", "Херсонобленерго", "khersonska", "https://ksoe.com.ua",
        note="Херсонобленерго не публікує графіків відкрито — лише в особистому кабінеті.",
    ),
    Operator("khmelnytskyi-oe", "Хмельницькобленерго", "khmelnytska", "https://hoe.com.ua/page/pogodinni-vidkljuchennja", note=LATER),
    Operator(
        "cherkasy-oe", "Черкасиобленерго", "cherkaska", "https://www.cherkasyoblenergo.com",
        sources=(Cherkasy(),),
    ),
    Operator(
        "chernivtsi-oe", "Чернівціобленерго", "chernivetska", "https://oblenergo.cv.ua/shutdowns/",
        sources=(Chernivtsi(),),
        groups=tuple(str(n) for n in range(1, 13)),
        group_label="група",
    ),
    Operator("chernihiv-oe", "Чернігівобленерго", "chernihivska", "https://chernihivoblenergo.com.ua/blackouts", note=LATER),
    Operator("sevastopol", "м. Севастополь", "sevastopol", "", note=OCCUPIED),
)  # fmt: skip

REGIONS: tuple[Region, ...] = (
    Region("kyiv", "м. Київ", ("dtek-kem",)),
    Region("crimea", "АР Крим", ("crimea",), occupied=True),
    Region("kyivska", "Київська", ("dtek-krem",)),
    Region("vinnytska", "Вінницька", ("vinnytsia-oe",)),
    Region("volynska", "Волинська", ("volyn-oe",)),
    Region("dnipropetrovska", "Дніпропетровська", ("dtek-dnem", "cek")),
    Region("donetska", "Донецька", ("dtek-dem",)),
    Region("zhytomyrska", "Житомирська", ("zhytomyr-oe",)),
    Region("zakarpatska", "Закарпатська", ("zakarpattia-oe",)),
    Region("zaporizka", "Запорізька", ("zaporizhzhia-oe",)),
    Region("ivano-frankivska", "Івано-Франківська", ("prykarpattia-oe",)),
    Region("kirovohradska", "Кіровоградська", ("kirovohrad-oe",)),
    Region("luhanska", "Луганська", ("luhansk-leo",)),
    Region("lvivska", "Львівська", ("lviv-oe",)),
    Region("mykolaivska", "Миколаївська", ("mykolaiv-oe",)),
    Region("odeska", "Одеська", ("dtek-oem",)),
    Region("poltavska", "Полтавська", ("poltava-oe",)),
    Region("rivnenska", "Рівненська", ("rivne-oe",)),
    Region("sumska", "Сумська", ("sumy-oe",)),
    Region("ternopilska", "Тернопільська", ("ternopil-oe",)),
    Region("kharkivska", "Харківська", ("kharkiv-oe",)),
    Region("khersonska", "Херсонська", ("kherson-oe",)),
    Region("khmelnytska", "Хмельницька", ("khmelnytskyi-oe",)),
    Region("cherkaska", "Черкаська", ("cherkasy-oe",)),
    Region("chernivetska", "Чернівецька", ("chernivtsi-oe",)),
    Region("chernihivska", "Чернігівська", ("chernihiv-oe",)),
    Region("sevastopol", "м. Севастополь", ("sevastopol",), occupied=True),
)

OPERATORS_BY_ID: dict[str, Operator] = {o.id: o for o in OPERATORS}
REGIONS_BY_ID: dict[str, Region] = {r.id: r for r in REGIONS}


def operator(operator_id: str) -> Operator | None:
    return OPERATORS_BY_ID.get(operator_id)


def region(region_id: str) -> Region | None:
    return REGIONS_BY_ID.get(region_id)


def available_operators() -> list[Operator]:
    return [o for o in OPERATORS if o.available]
