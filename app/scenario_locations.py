"""Choose an incident location as a city/object pair, then its relevant zone.

Only the title and authored observations identify the object. Normative features
may mention neighbouring infrastructure which isn't the incident location.
"""
import re

METRO_STATIONS = {
    'Москва': ('Сокол', 'Парк культуры', 'Авиамоторная', 'Таганская', 'Выхино'),
    'Санкт-Петербург': ('Московская', 'Озерки', 'Площадь Восстания', 'Автово'),
    'Нижний Новгород': ('Московская', 'Горьковская', 'Парк культуры', 'Бурнаковская'),
    'Новосибирск': ('Заельцовская', 'Площадь Маркса', 'Сибирская', 'Речной вокзал'),
    'Самара': ('Алабинская', 'Российская', 'Московская', 'Победа'),
    'Екатеринбург': ('Геологическая', 'Уральская', 'Ботаническая', 'Динамо'),
    'Казань': ('Кремлёвская', 'Площадь Тукая', 'Горки', 'Аметьево'),
}
MCK_STATIONS = ('Лужники', 'Автозаводская', 'Балтийская', 'Коптево', 'Ростокино', 'Зорге', 'Крымская', 'Площадь Гагарина')
AIRPORTS = (('Санкт-Петербург', 'Пулково'), ('Химки', 'Шереметьево'), ('Екатеринбург', 'Кольцово'))
RAIL_STATIONS = (('Москва', 'Москва-Пассажирская-Казанская'),
                 ('Санкт-Петербург', 'Санкт-Петербург-Главный'),
                 ('Новосибирск', 'Новосибирск-Главный'),
                 ('Екатеринбург', 'Екатеринбург-Пассажирский'),
                 ('Казань', 'Казань-Пассажирская'), ('Красноярск', 'Красноярск'))
FREIGHT_STATIONS = (('Москва', 'Люблино-Сортировочное'),
                    ('Санкт-Петербург', 'Санкт-Петербург-Сортировочный-Московский'),
                    ('Новосибирск', 'Инская'))
PORTS = (('Санкт-Петербург', 'Большой порт Санкт-Петербург'),)


def location_kind(topic, setting, facts=''):
    title = topic['title'].lower().replace('ё', 'е')
    observations = facts.lower().replace('ё', 'е')
    if setting in ('none', 'unknown'):
        return setting
    if topic['code'] == '12990100':
        return 'rail' if re.search(r'\bпоезд\w*', observations) else 'port'
    if re.search(r'\bмцк\b', title):
        return 'mck'
    if re.search(r'\bметро\b|метрополитен', title):
        return 'metro'
    if re.search(r'в (?:автомобильном )?тоннеле|это тоннель', observations):
        return 'road-tunnel'
    if re.search(r'\bмост\w*|\bэстакад\w*', observations):
        return 'bridge'
    if topic['code'] in ('2021400', '15010100'):
        return 'water'
    if re.search(r'автовокзал|автостанци', title):
        return 'bus-station'
    if topic['code'].startswith('120101'):
        return 'industrial' if topic['code'] == '12010101' else 'open-ground'
    if re.search(r'аэропорт|аэровокзал|аэродром|воздушн\w* транспорт|транспорт воздушн', title) or topic['code'] in ('12010200', '12010400'):
        return 'airport'
    if re.search(r'\bпорт\b|\bпричал|водн\w* транспорт|транспорт водн', title) or topic['code'].startswith(('1203', '1204')):
        return 'port'
    if re.search(r'\bвокзал\b|железнодорож|\bж[/-]?д\b|\bпоезд\w*', title) or setting == 'rail':
        return 'rail'
    if re.search(r'\bлес\b|\bлесу\b|\bторф\b', title) or setting == 'forest':
        return 'forest'
    if re.search(r'гидросооруж|гидротехнич|\bплотин', title):
        return 'dam'
    return setting


def transport_address(kind, facts, choose):
    value = facts.lower().replace('ё', 'е')
    if kind in ('metro', 'mck'):
        if kind == 'mck':
            city, station = 'Москва', choose('mck-station', MCK_STATIONS)
            network = 'МЦК'
        else:
            city = choose('metro-city', tuple(METRO_STATIONS))
            station = choose('metro-station-' + city, METRO_STATIONS[city])
            network = 'метро'
        if 'вентиляционная шахта' in value:
            return f'город {city}, {network}, вентиляционная шахта у станции {station}'
        if 'в тоннеле' in value:
            if kind == 'mck':
                station = 'Площадь Гагарина'
            zone = 'поезд в тоннеле у станции'
            return f'город {city}, {network}, {zone} {station}'
        if re.search(r'вагон|поезд.*(?:стоит|останов|сошел|накрен)|дверь вагона', value):
            zone = 'поезд у платформы'
        elif re.search(r'рельс|у путей|задел поезд|сбил поезд|травм|ранен|части.*тела|останки|неподвижное тело', value):
            zone = 'платформа, у края путей'
        elif 'в переходе' in value:
            zone = 'переход к платформе'
        elif 'у входа' in value:
            zone = 'вестибюль, у входа'
        else:
            zone = choose(kind + '-zone', ('платформа', 'вестибюль, у турникетов', 'переход к платформе'))
        return f'город {city}, станция {network} {station}, {zone}'
    if kind == 'airport':
        city, airport = choose('airport', AIRPORTS)
        if re.search(r'багажн.*транспорт|багаж.*кожух', value):
            zone = 'зона выдачи багажа, у багажного транспортёра'
        elif re.search(r'самолет|воздушное судно|экипаж', value):
            zone = 'аэродром, стоянка воздушных судов' if re.search(r'горит|пламя|дым|взрыв|бесхозн|угроз|удержива|забрали', value) else 'аэродром, у взлётно-посадочной полосы'
        elif 'зоне посадки' in value:
            zone = 'зал вылета, у выхода на посадку'
        elif 'общем зале' in value:
            zone = 'пассажирский терминал, общий зал'
        else:
            zone = choose('airport-zone', ('пассажирский терминал, зал ожидания', 'пассажирский терминал, зона регистрации', 'пассажирский терминал, у входа'))
        return f'город {city}, аэропорт {airport}, {zone}'
    if kind == 'rail':
        freight = bool(re.search(r'грузов\w* поезд|опасн\w* груз|цистерн', value))
        city, station = choose('freight-station' if freight else 'rail-station', FREIGHT_STATIONS if freight else RAIL_STATIONS)
        if 'депо' in value:
            zone = 'территория железнодорожного депо у станции'
            return f'город {city}, {zone} {station}'
        if re.search(r'железнодорожные провода|контактн\w* сет', value):
            zone = 'станционные пути, у опор контактной сети'
        elif freight:
            zone = 'грузовые пути'
        elif re.search(r'сошел с рельсов|сход вагон|тормозн', value):
            zone = 'железнодорожные пути на подходе к станции'
        elif re.search(r'в зале|здание|трещин|конструкц|вокзал|крыша', value) and 'платформ' not in value:
            zone = 'здание вокзала, зал ожидания'
        elif re.search(r'внутри удерживают|горит поезд|состав|вагон|забрали.*поезд', value):
            zone = 'состав на станционных путях'
        else:
            zone = 'пассажирская платформа'
        return f'город {city}, железнодорожная станция {station}, {zone}'
    if kind == 'bus-station':
        return 'город Новосибирск, автовокзал Главный, ' + choose('bus-station-zone', ('зал ожидания', 'площадка посадки в автобусы', 'у главного входа'))
    if kind == 'port':
        city, port = choose('port', PORTS)
        if re.search(r'тонет|внутрь поступает вода|неуправляем|двигател', value):
            zone = 'акватория порта, на судне'
        elif re.search(r'судно|катер|корабл', value):
            zone = 'судно у причала'
        else:
            zone = choose('port-zone', ('на причале', 'у грузового причала', 'территория порта, возле причала'))
        return f'город {city}, порт {port}, {zone}'
    return None
