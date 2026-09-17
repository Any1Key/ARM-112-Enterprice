"""Location realism regressions, including every authored case alternative."""
import re
import pytest
from app.generation import generate_local, varied_choice
from app.scenario_profiles import manifest, build_profile, case_contexts, object_name
from app.scenario_locations import METRO_STATIONS, MCK_STATIONS, AIRPORTS, RAIL_STATIONS, FREIGHT_STATIONS


def forced_generation(monkeypatch, code, core, difficulty='basic'):
    topic = manifest()[code]
    p = build_profile(topic)
    def choose(key, values):
        if key == 'case-' + code:
            return core
        if key == 'context-' + code:
            return case_contexts(p)[0]
        return varied_choice(key, values)
    monkeypatch.setattr('app.generation.varied_choice', choose)
    return generate_local(topic, {}, difficulty)[0]


@pytest.mark.parametrize('code', ['16110200', '14060302', '14060403'])
def test_measurement_in_title_does_not_create_metro(monkeypatch, code):
    # The actual regression is "метро" being a substring of "метров".
    if code not in manifest():
        pytest.fail('Unknown regression code: ' + code)
    t = manifest()[code]
    generated, _ = generate_local(t, {}, 'complex')
    assert 'метро' not in generated.address
    assert 'улица' in generated.address


@pytest.mark.parametrize('code', ['1020601', '1020602', '1990015', '3020902', '4020000', '5010100', '6010200', '12020100', '12020200', '12020300'])
def test_airport_is_named_and_belongs_to_its_city(code):
    for _ in range(8):
        generated, _ = generate_local(manifest()[code], {}, 'complex')
        assert any(generated.address.startswith(f'город {city}, аэропорт {airport}, ') for city, airport in AIRPORTS)
        assert not re.search(r'улица|\bдом\b|станция метро', generated.address)


@pytest.mark.parametrize('code', ['1020801', '3020802', '4030000', '5020100', '6020200', '12060100', '12060200', '19020201'])
def test_rail_station_has_matching_city_and_name(code):
    for _ in range(8):
        generated, _ = generate_local(manifest()[code], {}, 'advanced')
        assert any(generated.address.startswith(f'город {city}, железнодорожная станция {station}, ') for city, station in RAIL_STATIONS)
        assert not re.search(r'улица|\bдом\b|станция метро', generated.address)


def test_all_authored_metro_variants_have_matching_city_station_and_zone(monkeypatch):
    for t in manifest().values():
        if not re.search(r'\bметро\b|\bмцк\b', t['title'], re.I):
            continue
        p = build_profile(t)
        for core in p.cases:
            g = forced_generation(monkeypatch, t['code'], core, 'complex')
            assert not re.search(r'улица|\bдом\b|аэропорт', g.address), (t['code'], core, g.address)
            if 'МЦК' in g.address:
                assert g.address.startswith('город Москва,')
                assert any(name in g.address for name in MCK_STATIONS)
            else:
                city = g.address.split(',')[0][6:]
                assert city in METRO_STATIONS
                assert any(name in g.address for name in METRO_STATIONS[city])
            if 'в тоннеле' in core:
                assert 'тоннеле' in g.address and 'платформа' not in g.address
            if 'в переходе' in core.lower():
                assert 'переход' in g.address


@pytest.mark.parametrize('code,case_fragment,required,forbidden', [
    ('12020200', 'багажный транспортёр', 'зона выдачи багажа', 'полосы'),
    ('12010400', 'зоне посадки', 'выхода на посадку', 'стоянка воздушных'),
    ('12010300', 'Горит', 'стоянка воздушных судов', 'пассажирский терминал'),
    ('12050102', 'сошёл с рельсов', 'грузовые пути', 'пассажирская платформа'),
    ('12050101', 'сошёл с рельсов', 'пути на подходе', 'зал ожидания'),
    ('12050400', 'В зале', 'зал ожидания', 'пассажирская платформа'),
    ('12030100', 'тонет', 'акватория порта', 'берег реки'),
    ('12990100', 'судне', 'порт', 'станция'),
    ('12990100', 'грузовой поезд', 'грузовые пути', 'порт'),
    ('18060000', 'В парке', 'парк', 'лесной массив'),
    ('18060000', 'в лес', 'лесной массив', 'парк'),
    ('1990176', 'Горит', 'вентиляционная шахта', 'улица'),
    ('14020500', 'В автомобильном тоннеле', 'автомобильный тоннель', 'дом'),
    ('2021400', 'с моста', 'мост', 'дом'),
])
def test_location_zone_matches_observed_case(monkeypatch, code, case_fragment, required, forbidden):
    p = build_profile(manifest()[code])
    core = next(c for c in p.cases if case_fragment in c)
    g = forced_generation(monkeypatch, code, core)
    assert required in g.address, g.address
    assert forbidden not in g.address, g.address


@pytest.mark.parametrize('code', ['12010100', '1010501', '1010502', '1061201', '3011300', '5150100', '1990118'])
def test_outdoor_special_objects_do_not_receive_residential_house_address(code):
    g, _ = generate_local(manifest()[code], {}, 'basic')
    assert not re.search(r'улица|\bдом\b|квартира|метро', g.address)


def test_similar_word_does_not_replace_incident_object():
    assert object_name(manifest()['1990096']) == 'мотоцикл'
    assert object_name(manifest()['1990176']) == 'вентиляционная шахта метро'
    assert object_name(manifest()['17010300']) != 'лесной участок'


@pytest.mark.parametrize('code', ['12050102', '12050103', '12050202', '12050203'])
def test_freight_train_is_at_freight_station(code):
    g, _ = generate_local(manifest()[code], {}, 'complex')
    assert any(g.address.startswith(f'город {city}, железнодорожная станция {station}, грузовые пути') for city, station in FREIGHT_STATIONS)
    assert not any(station in g.address for _, station in RAIL_STATIONS)


def test_original_ticket_address_is_not_replaced_with_random_transport_object():
    g, _ = generate_local(manifest()['12060100'], {'address': 'станция Мытищи, платформа 2', 'situation': 'На платформе повреждено ограждение.'}, 'basic')
    assert g.address == 'станция Мытищи, платформа 2'
