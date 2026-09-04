from dataclasses import dataclass

from app.catalog.units import STORAGE_UNITS


@dataclass(frozen=True)
class CatalogProduct:
    name: str
    unit: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.unit not in STORAGE_UNITS:
            raise ValueError(f"Unknown unit {self.unit!r} for {self.name}")


# One product → one storage unit. Voice supplies name + quantity only.
PRODUCTS: tuple[CatalogProduct, ...] = (
    # Молочка
    CatalogProduct("молоко", "бутылка", ("молочка", "молоко топленое", "топлёное молоко")),
    CatalogProduct("кефир", "бутылка", ("биокефир",)),
    CatalogProduct("ряженка", "бутылка"),
    CatalogProduct("простокваша", "бутылка"),
    CatalogProduct("снежок", "бутылка"),
    CatalogProduct("айран", "бутылка"),
    CatalogProduct("тан", "бутылка"),
    CatalogProduct("сливки", "пачка", ("сливки для взбивания",)),
    CatalogProduct("сметана", "банка"),
    CatalogProduct("творог", "пачка", ("творожная масса",)),
    CatalogProduct("йогурт", "шт", ("греческий йогурт", "биойогурт")),
    CatalogProduct("йогурт питьевой", "бутылка"),
    CatalogProduct("масло сливочное", "пачка", ("масло", "сливочное масло", "топленое масло", "топлёное масло")),
    CatalogProduct("маргарин", "пачка", ("спред",)),
    CatalogProduct("сыр", "упаковка", ("российский сыр", "колбасный сыр")),
    CatalogProduct("плавленый сыр", "упаковка"),
    CatalogProduct("творожный сыр", "упаковка", ("сливочный сыр",)),
    CatalogProduct("моцарелла", "упаковка"),
    CatalogProduct("сулугуни", "упаковка"),
    CatalogProduct("брынза", "упаковка"),
    CatalogProduct("фета", "упаковка"),
    CatalogProduct("пармезан", "упаковка"),
    CatalogProduct("чеддер", "упаковка"),
    CatalogProduct("маасдам", "упаковка"),
    CatalogProduct("яйца", "десяток", ("яйцо",)),
    CatalogProduct("яйца перепелиные", "упаковка"),
    CatalogProduct("сырок", "шт", ("глазированный сырок", "творожный сырок")),
    CatalogProduct("сгущёнка", "банка", ("сгущенка", "сгущённое молоко", "сгущенное молоко")),
    # Крупы, мука, макароны
    CatalogProduct("гречка", "пачка"),
    CatalogProduct("рис", "пачка"),
    CatalogProduct("овсянка", "пачка", ("овсяные хлопья", "геркулес")),
    CatalogProduct("пшено", "пачка"),
    CatalogProduct("перловка", "пачка"),
    CatalogProduct("манка", "пачка"),
    CatalogProduct("чечевица", "пачка"),
    CatalogProduct("горох", "пачка", ("горох сухой",)),
    CatalogProduct("нут", "пачка"),
    CatalogProduct("фасоль сухая", "пачка"),
    CatalogProduct("булгур", "пачка"),
    CatalogProduct("кус-кус", "пачка", ("кускус",)),
    CatalogProduct("киноа", "пачка"),
    CatalogProduct("мука", "пачка"),
    CatalogProduct("сахар", "пачка"),
    CatalogProduct("соль", "пачка"),
    CatalogProduct("крахмал", "пачка"),
    CatalogProduct("макароны", "пачка", ("спагетти", "паста", "вермишель")),
    CatalogProduct("хлопья", "пачка", ("мюсли", "кукурузные хлопья")),
    CatalogProduct("каша быстрого приготовления", "пачка"),
    CatalogProduct("отруби", "пачка"),
    CatalogProduct("дрожжи", "пачка"),
    CatalogProduct("разрыхлитель", "пачка"),
    CatalogProduct("перец чёрный", "пачка", ("черный перец", "чёрный перец", "молотый перец")),
    CatalogProduct("лавровый лист", "пачка"),
    CatalogProduct("желатин", "пачка"),
    CatalogProduct("панировочные сухари", "пачка"),
    # Масла и соусы
    CatalogProduct("масло подсолнечное", "бутылка", ("подсолнечное масло", "растительное масло")),
    CatalogProduct("масло оливковое", "бутылка", ("оливковое масло",)),
    CatalogProduct("масло кунжутное", "бутылка", ("кунжутное масло",)),
    CatalogProduct("масло кокосовое", "банка", ("кокосовое масло",)),
    CatalogProduct("уксус", "бутылка"),
    CatalogProduct("кетчуп", "шт"),
    CatalogProduct("майонез", "шт"),
    CatalogProduct("горчица", "шт"),
    CatalogProduct("хрен", "банка"),
    CatalogProduct("аджика", "банка"),
    CatalogProduct("соевый соус", "бутылка"),
    CatalogProduct("томатная паста", "банка"),
    CatalogProduct("сироп", "бутылка"),
    # Консервы
    CatalogProduct("тушёнка", "банка", ("тушенка",)),
    CatalogProduct("кукуруза", "банка", ("кукуруза консервированная",)),
    CatalogProduct("горошек", "банка", ("зеленый горошек", "зелёный горошек")),
    CatalogProduct("фасоль", "банка", ("фасоль консервированная",)),
    CatalogProduct("огурцы консервированные", "банка", ("соленые огурцы", "солёные огурцы", "маринованные огурцы")),
    CatalogProduct("помидоры консервированные", "банка"),
    CatalogProduct("лечо", "банка"),
    CatalogProduct("икра кабачковая", "банка"),
    CatalogProduct("икра баклажанная", "банка"),
    CatalogProduct("оливки", "банка"),
    CatalogProduct("маслины", "банка"),
    CatalogProduct("грибы маринованные", "банка", ("маринованные грибы",)),
    CatalogProduct("паштет", "банка"),
    CatalogProduct("варенье", "банка", ("джем",)),
    CatalogProduct("мёд", "банка", ("мед",)),
    CatalogProduct("кофе растворимый", "банка"),
    CatalogProduct("кофе", "пачка", ("кофе молотый", "кофе в зернах", "кофе зерновой")),
    CatalogProduct("чай", "пачка"),
    CatalogProduct("цикорий", "банка"),
    CatalogProduct("шпроты", "банка"),
    CatalogProduct("сайра", "банка"),
    CatalogProduct("килька", "банка"),
    CatalogProduct("тунец", "банка", ("тунец консервированный",)),
    CatalogProduct("горбуша консервированная", "банка"),
    CatalogProduct("сельдь", "банка", ("селёдка", "селедка")),
    CatalogProduct("икра", "банка"),
    # Овощи и фрукты
    CatalogProduct("картофель", "кг", ("картошка",)),
    CatalogProduct("морковь", "кг"),
    CatalogProduct("лук", "кг", ("лук репчатый",)),
    CatalogProduct("свёкла", "кг", ("свекла",)),
    CatalogProduct("капуста", "кг"),
    CatalogProduct("капуста цветная", "кг", ("цветная капуста",)),
    CatalogProduct("брокколи", "кг"),
    CatalogProduct("кабачки", "кг", ("цукини",)),
    CatalogProduct("баклажаны", "кг"),
    CatalogProduct("перец болгарский", "кг", ("болгарский перец", "перец")),
    CatalogProduct("тыква", "кг"),
    CatalogProduct("редис", "кг"),
    CatalogProduct("чеснок", "шт"),
    CatalogProduct("имбирь", "кг"),
    CatalogProduct("огурцы", "кг"),
    CatalogProduct("помидоры", "кг", ("томаты",)),
    CatalogProduct("шампиньоны", "кг", ("грибы",)),
    CatalogProduct("яблоки", "кг"),
    CatalogProduct("груши", "кг"),
    CatalogProduct("бананы", "кг"),
    CatalogProduct("виноград", "кг"),
    CatalogProduct("апельсины", "кг"),
    CatalogProduct("мандарины", "кг"),
    CatalogProduct("грейпфрут", "шт"),
    CatalogProduct("лимон", "шт"),
    CatalogProduct("лайм", "шт"),
    CatalogProduct("киви", "шт"),
    CatalogProduct("авокадо", "шт"),
    CatalogProduct("хурма", "кг"),
    CatalogProduct("персики", "кг"),
    CatalogProduct("нектарины", "кг"),
    CatalogProduct("сливы", "кг"),
    CatalogProduct("арбуз", "шт"),
    CatalogProduct("дыня", "кг"),
    CatalogProduct("ананас", "шт"),
    CatalogProduct("манго", "шт"),
    CatalogProduct("гранат", "шт"),
    CatalogProduct("клубника", "кг"),
    CatalogProduct("малина", "кг"),
    CatalogProduct("черника", "кг"),
    CatalogProduct("зелень", "пучок", ("салат", "руккола", "шпинат")),
    CatalogProduct("укроп", "пучок"),
    CatalogProduct("петрушка", "пучок"),
    CatalogProduct("кинза", "пучок", ("кориандр",)),
    CatalogProduct("базилик", "пучок"),
    CatalogProduct("зелёный лук", "пучок", ("лук зеленый", "зеленый лук")),
    CatalogProduct("сельдерей", "пучок"),
    CatalogProduct("мята", "пучок"),
    # Мясо, рыба, заморозка
    CatalogProduct("курица", "кг", ("куриное филе", "окорочка", "куриные бёдра", "куриные бедра", "куриные крылья", "голень")),
    CatalogProduct("индейка", "кг"),
    CatalogProduct("фарш", "кг"),
    CatalogProduct("говядина", "кг", ("стейк",)),
    CatalogProduct("свинина", "кг", ("ребра", "грудинка")),
    CatalogProduct("баранина", "кг"),
    CatalogProduct("печень", "кг"),
    CatalogProduct("сало", "кг"),
    CatalogProduct("рыба", "кг", ("филе рыбы",)),
    CatalogProduct("семга", "кг", ("лосось",)),
    CatalogProduct("форель", "кг"),
    CatalogProduct("минтай", "кг"),
    CatalogProduct("треска", "кг"),
    CatalogProduct("хек", "кг"),
    CatalogProduct("скумбрия", "кг"),
    CatalogProduct("сосиски", "пачка", ("сардельки",)),
    CatalogProduct("колбаса", "палка", ("сервелат", "салями", "варёная колбаса", "вареная колбаса", "копчёная колбаса", "копченая колбаса")),
    CatalogProduct("ветчина", "упаковка"),
    CatalogProduct("бекон", "пачка"),
    CatalogProduct("пельмени", "пачка"),
    CatalogProduct("вареники", "пачка"),
    CatalogProduct("манты", "пачка"),
    CatalogProduct("хинкали", "пачка"),
    CatalogProduct("котлеты", "пачка"),
    CatalogProduct("наггетсы", "пачка"),
    CatalogProduct("блинчики", "пачка"),
    CatalogProduct("тесто", "пачка", ("слоеное тесто", "слоёное тесто", "дрожжевое тесто")),
    CatalogProduct("пицца", "шт"),
    CatalogProduct("замороженные овощи", "пачка"),
    CatalogProduct("замороженные ягоды", "пачка"),
    CatalogProduct("креветки", "пачка"),
    CatalogProduct("кальмар", "пачка"),
    CatalogProduct("мидии", "пачка"),
    CatalogProduct("крабовые палочки", "пачка"),
    # Хлеб и готовое
    CatalogProduct("хлеб", "батон", ("батон", "буханка")),
    CatalogProduct("лаваш", "шт"),
    CatalogProduct("хлебцы", "пачка"),
    CatalogProduct("сухари", "пачка", ("сухарики",)),
    CatalogProduct("печенье", "пачка"),
    CatalogProduct("вафли", "пачка"),
    CatalogProduct("пряники", "пачка"),
    CatalogProduct("сушки", "пачка"),
    CatalogProduct("крекеры", "пачка"),
    CatalogProduct("чипсы", "пачка"),
    CatalogProduct("попкорн", "пачка"),
    CatalogProduct("шоколад", "шт"),
    CatalogProduct("конфеты", "пачка"),
    CatalogProduct("халва", "пачка"),
    CatalogProduct("зефир", "пачка"),
    CatalogProduct("мармелад", "пачка"),
    CatalogProduct("мороженое", "упаковка"),
    CatalogProduct("орехи", "пачка", ("грецкие орехи", "миндаль", "фундук", "кешью", "арахис", "фисташки")),
    CatalogProduct("семечки", "пачка"),
    CatalogProduct("изюм", "пачка"),
    CatalogProduct("курага", "пачка"),
    CatalogProduct("чернослив", "пачка"),
    CatalogProduct("финики", "пачка"),
    CatalogProduct("лапша быстрого приготовления", "шт", ("доширак", "ролтон")),
    CatalogProduct("бульонные кубики", "пачка"),
    # Напитки
    CatalogProduct("вода", "бутылка", ("минералка", "минеральная вода")),
    CatalogProduct("сок", "бутылка"),
    CatalogProduct("морс", "бутылка"),
    CatalogProduct("квас", "бутылка"),
    CatalogProduct("газировка", "бутылка", ("лимонад", "кола", "спрайт", "фанта")),
    CatalogProduct("энергетик", "банка"),
    CatalogProduct("пиво", "бутылка"),
    CatalogProduct("вино", "бутылка"),
    CatalogProduct("компот", "банка"),
    CatalogProduct("кисель", "пачка"),
)


def _key(value: str) -> str:
    return " ".join(value.strip().lower().split())


_BY_ALIAS: dict[str, CatalogProduct] = {}
for _product in PRODUCTS:
    _BY_ALIAS[_key(_product.name)] = _product
    for _alias in _product.aliases:
        _BY_ALIAS[_key(_alias)] = _product


def lookup_product(spoken_name: str) -> CatalogProduct | None:
    key = _key(spoken_name)
    exact = _BY_ALIAS.get(key)
    if exact:
        return exact
    contained = [
        (alias, product)
        for alias, product in _BY_ALIAS.items()
        if alias and alias in key
    ]
    if not contained:
        return None
    contained.sort(key=lambda item: len(item[0]), reverse=True)
    return contained[0][1]
