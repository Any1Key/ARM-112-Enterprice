import json
import re
import secrets

import httpx
import pytest

from app.generation import generate_local, scenario_address, scenario_location, ScenarioDraft, location_issues, draft_issues, fictional_name, varied_choice
from app import generation_catalog as catalog
from app.caller_variation import PLATE


@pytest.mark.parametrize('address', [
    'Санкт-Петербург, Невский проспект, 12',
    'г. Волжский, ул. Карла Маркса около молсыркомбината',
    'Москва, набережная Яузы, напротив Большого Нижнего пруда',
    'Московская область, трасса М-4, 35-й километр',
])
def test_source_locations_are_preserved(address):
    assert scenario_address({'address': address}) == address


def mock_model(monkeypatch, replies, tags_status=200):
    requests = []
    def handle(request):
        if request.url.path == '/api/tags':
            return httpx.Response(tags_status, json={'models': []})
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={'message': {'content': replies.pop(0)}})
    original = httpx.Client
    monkeypatch.setattr('app.generation.httpx.Client',
                        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    return requests


def draft(**updates):
    values=dict(title='Дым в квартире', caller_name='Сидоров Иван Сергеевич',
                          caller_text='Вижу дым из окна соседней квартиры. Началось пять минут назад. Людей не видно.',
                          expected_description='Дым из окна, пострадавшие неизвестны.')
    values.update(updates)
    return json.dumps(values, ensure_ascii=False)


@pytest.mark.parametrize('mode', ['missing', 'same', 'different'])
def test_generation_composes_consistent_contacts_in_one_request(monkeypatch, mode):
    original_choice = secrets.choice
    monkeypatch.setattr('app.generation.secrets.choice',
                        lambda values: mode if values == ('missing', 'same', 'different') else original_choice(values))
    requests = mock_model(monkeypatch, [draft()])
    text, provenance = generate_local({'code': '1050102', 'title': 'задымление'}, {}, 'basic')
    assert len(requests) == provenance['attempts'] == 1
    assert 'code' not in requests[0]['messages'][1]['content']
    if mode == 'missing':
        assert text.caller_phone == ''
        assert 'Для обратной связи' not in text.caller_text
    else:
        assert re.fullmatch(r'\+7 9\d{2} \d{3} \d{2} \d{2}', text.caller_phone)
        assert f'Для обратной связи мой телефон {text.caller_phone}.' in text.caller_text
    assert re.fullmatch(r'\+7 9\d{2} \d{3} \d{2} \d{2}', text.aon)
    if mode == 'same':
        assert text.caller_phone == text.aon
    elif mode == 'different':
        assert text.caller_phone != text.aon
    for value in (text.caller_name, text.caller_phone, text.aon, text.address):
        assert value in text.caller_text
    assert 'дом' in text.address
    assert provenance['needs_teacher_approval']


def test_generated_addresses_vary_and_are_complete(monkeypatch):
    choices = iter(['Тверь', 'Липовая', 'Тула', 'Сосновая'])
    monkeypatch.setattr('app.generation.secrets.choice', lambda values: next(choices))
    first, second = scenario_address({}), scenario_address({})
    assert first != second
    assert first.startswith('город Тверь, улица Липовая, дом ')
    assert second.startswith('город Тула, улица Сосновая, дом ')


def test_landmark_does_not_trigger_second_generation(monkeypatch):
    requests = mock_model(monkeypatch, [draft()])
    address = 'Москва, парк Сокольники, у главного входа'
    text, _ = generate_local({'title': 'задымление'}, {'address': address}, 'basic')
    assert text.address == address
    assert address in text.caller_text
    assert len(requests) == 1


def test_invalid_json_is_repaired_with_specific_feedback(monkeypatch):
    requests = mock_model(monkeypatch, ['{"title":', draft()])
    _, provenance = generate_local({'title': 'задымление'}, {}, 'basic')
    assert provenance['attempts'] == 2
    assert 'JSON не соответствует схеме' in requests[1]['messages'][-1]['content']


def test_metadata_failure_does_not_discard_draft(monkeypatch):
    mock_model(monkeypatch, [draft()], tags_status=503)
    text, provenance = generate_local({'title': 'задымление'}, {}, 'basic')
    assert text.title
    assert provenance['digest'] is None


def test_invalid_second_response_still_fails(monkeypatch):
    mock_model(monkeypatch, ['{}', '{}'])
    with pytest.raises(ValueError, match='JSON не соответствует схеме'):
        generate_local({'title': 'задымление'}, {}, 'basic')


@pytest.mark.parametrize('location',['квартира','гараж','помещение'])
def test_classifier_alternatives_choose_one_object(monkeypatch,location):
    original=secrets.choice
    monkeypatch.setattr('app.generation.secrets.choice',lambda values:
                        location if values==['квартира','гараж','помещение'] else
                        'магазин' if values==('магазин','офис','склад','помещение') else original(values))
    topic={'title':'Вскрыта квартира, помещение','features':['Вскрыта','Квартира помещение гараж']}
    assert scenario_location(topic,{})==('магазин' if location=='помещение' else location)
    assert scenario_location(topic,{'situation':'Вижу вскрытый гараж.'})=='гараж'
    assert scenario_location(topic,{'situation':'Вижу повреждённую дверь магазина.'})=='магазин'


@pytest.mark.parametrize('detail', ['full','apartment','floor','unknown'])
def test_apartment_address_detail_variants(monkeypatch,detail):
    original=secrets.choice
    monkeypatch.setattr('app.generation.secrets.choice',
                        lambda values: detail if values==('full','apartment','floor','unknown') else original(values))
    address=scenario_address({},'квартира')
    assert ('квартира ' in address)==(detail in ('full','apartment'))
    assert ('этаж ' in address)==(detail in ('full','floor'))
    assert ('подъезд ' in address)==(detail=='full')
    assert scenario_address({'address':'Тула, ул. Сосновая, 65'},'квартира')=='Тула, ул. Сосновая, 65'


def test_apartment_to_garage_contradiction_is_repaired(monkeypatch):
    bad=draft(caller_text='Вижу вскрытую квартиру, похоже, это гараж.')
    good=draft(caller_text='Вижу вскрытую квартиру. Дверь открыта, замок повреждён. Кто внутри, не вижу.')
    requests=mock_model(monkeypatch,[bad,good])
    text,provenance=generate_local({'title':'Вскрыта квартира, помещение',
                                  'features':['Вскрыта','Квартира помещение гараж']},
                                 {'situation':'Дверь квартиры открыта, замок повреждён.'},'basic')
    assert provenance['attempts']==2
    assert 'гараж' not in text.caller_text
    user=json.loads(requests[0]['messages'][1]['content'])
    assert user['объект_события']=='квартира'
    assert 'Квартира помещение гараж' not in user['событие']['features']
    assert 'подмену объекта' in requests[1]['messages'][-1]['content']


@pytest.mark.parametrize('line', ['Квартира 99 вскрыта.', 'Вижу вскрытую квартиру на 7-м этаже.',
                                'Вскрытая квартира, этаж 7.', 'Квартира в подъезде 9 вскрыта.'])
def test_invented_or_changed_access_details_are_rejected(line):
    response=ScenarioDraft.model_validate_json(draft(caller_text=line))
    assert location_issues(response,'квартира',{},'Тула, дом 65, квартира 12, этаж 3, подъезд 1')


def test_source_floor_and_apartment_are_preserved():
    response=ScenarioDraft.model_validate_json(draft(caller_text='Вскрыта квартира 12 на 3-м этаже. Замок сломан.'))
    assert not location_issues(response,'квартира',{'situation':'Квартира 12, этаж 3.'},'Тула, дом 65')


@pytest.mark.parametrize('title,object_word', [
    ('Вскрыта квартира, помещение','квартир'),
    ('Вскрывают квартиру сейчас','квартир'),
    ('Вскрыта автомашина','автомобил'),
    ('Автомашину вскрывают','автомобил'),
    ('Вскрыт гараж','гараж'),
])
def test_intrusion_without_source_is_grounded_without_model(monkeypatch,title,object_word):
    original=secrets.choice
    monkeypatch.setattr('app.generation.secrets.choice',lambda values:
                        'квартира' if isinstance(values,list) and 'квартира' in values and object_word=='квартир' else original(values))
    monkeypatch.setattr('app.generation.httpx.Client',lambda **kwargs: pytest.fail('Модель не нужна для этого сценария'))
    text,provenance=generate_local({'title':title,'features':[]},{},'basic')
    assert object_word in text.caller_text
    assert any(word in text.caller_text.lower() for word in ('замок','замке','замком','стекло'))
    if object_word=='квартир':
        assert 'гараж' not in text.caller_text
    assert provenance['engine']=='scenario-template'
    assert provenance['attempts']==0
    assert provenance['needs_teacher_approval']


def test_source_details_are_not_overwritten_with_random_apartment():
    address=scenario_address({'situation':'Квартира 12, этаж 3.'},'квартира')
    assert 'квартира' not in address and 'этаж' not in address


@pytest.mark.parametrize('location',['магазин','офис','склад','помещение'])
def test_commercial_premises_are_concrete_and_not_apartments(monkeypatch,location):
    original=secrets.choice
    monkeypatch.setattr('app.generation.secrets.choice',lambda values:
                        'помещение' if values==['квартира','гараж','помещение'] else
                        location if values==('магазин','офис','склад','помещение') else original(values))
    monkeypatch.setattr('app.generation.httpx.Client',lambda **kwargs: pytest.fail('Модель не нужна'))
    text,_=generate_local({'title':'Вскрыта квартира, помещение',
                          'features':['Квартира помещение гараж']},{},'basic')
    assert location in text.title
    assert LOCATION_STEMS[location] in text.caller_text
    assert 'квартир' not in text.caller_text
    assert 'гараж' not in text.caller_text
    assert 'квартира' not in text.address


LOCATION_STEMS={'магазин':'магазин','офис':'офис','склад':'склад','помещение':'помещени'}


@pytest.mark.parametrize('title,features',[
    ('Брошеная автомашина',['Автомашина','Брошенная','']),
    ('Брошеная автомашина (давно)',['Автомашина','Брошенная','Давно стоит автохлам']),
    ('Брошенная автомашина',['Автомашина брошенная','','']),
])
def test_abandoned_vehicle_is_observable_without_model(monkeypatch,title,features):
    monkeypatch.setattr('app.generation.httpx.Client',lambda **kwargs: pytest.fail('Модель не нужна'))
    monkeypatch.setattr('app.generation.fictional_name',lambda:'Соколова Елена Андреевна')
    text,meta=generate_local({'title':title,'features':features},{},'advanced')
    assert any(word in text.caller_text for word in ('автомобиль','седан','хэтчбек','универсал','внедорожник','минивэн'))
    assert 'Кто владелец, не знаю' in text.caller_text
    assert not any(word in text.caller_text for word in ('спят','вероятно','никто не живёт','заметил','новый'))
    assert all(match.group(0) in text.expected_description for match in PLATE.finditer(text.caller_text))
    assert meta['engine']=='scenario-template'
    assert meta['attempts']==0
    assert meta['needs_teacher_approval']
    if 'давно' in title:
        assert any(f'месте {duration}.' in text.caller_text for duration in catalog.LONG_VEHICLE_DURATIONS)


@pytest.mark.parametrize('transport',['трамвай','троллейбус'])
def test_vehicle_obstructs_only_one_selected_transport(monkeypatch,transport):
    original=secrets.choice
    def choose(values):
        if isinstance(values,list) and values and isinstance(values[0],tuple):
            return next(value for value in values if transport in value[1].lower())
        return original(values)
    monkeypatch.setattr('app.generation.secrets.choice',choose)
    text,meta=generate_local({'title':'Брошенная автомашина (помеха трамвай, троллейбус)',
                             'features':['Автомашина брошенная','Трамвайные пути, ход движения троллейбусов','']},{},'basic')
    assert transport in text.caller_text.lower()
    assert ('трамва' if transport=='трамвай' else 'троллейбус') in text.expected_description.lower()
    assert 'не может проехать' in text.caller_text
    other='троллейбус' if transport=='трамвай' else 'трамвай'
    assert other not in text.caller_text.lower()
    assert meta['attempts']==0


@pytest.mark.parametrize('name,bad,good',[
    ('Соколова Елена Андреевна','Я заметил автомобиль.','Я заметила автомобиль.'),
    ('Морозов Алексей Петрович','Я заметила автомобиль.','Я заметил автомобиль.'),
    ('Морозов Алексей Петрович','Я пришла к автомобилю.','Я пришёл к автомобилю.'),
])
def test_first_person_gender_agrees_with_name(name,bad,good):
    assert draft_issues(ScenarioDraft.model_validate_json(draft(caller_text=bad)),name)
    assert not draft_issues(ScenarioDraft.model_validate_json(draft(caller_text=good)),name)


def test_vehicle_source_keeps_model_path_with_speaker_gender(monkeypatch):
    monkeypatch.setattr('app.generation.fictional_name',lambda:'Соколова Елена Андреевна')
    requests=mock_model(monkeypatch,[draft(title='Брошенный автомобиль',caller_text='Я заметил автомобиль. Стоит без движения три дня.',
                                         expected_description='Автомобиль стоит три дня.'),
                                    draft(title='Брошенный автомобиль',caller_text='Я заметила автомобиль. Стоит без движения три дня.',
                                          expected_description='Автомобиль стоит три дня.')])
    text,meta=generate_local({'title':'Брошеная автомашина','features':['Автомашина','Брошенная','']},
                            {'situation':'Автомобиль три дня стоит без движения, владелец неизвестен.','address':'Тула, улица Сиреневая, дом 72'},'basic')
    assert 'Я заметила' in text.caller_text
    assert meta['attempts']==2
    assert json.loads(requests[0]['messages'][1]['content'])['пол_заявителя']=='женский'
    assert 'Согласуй глаголы' in requests[1]['messages'][-1]['content']


def test_recent_values_do_not_repeat_even_with_same_random_choice(monkeypatch):
    monkeypatch.setattr('app.generation.recent_choices',{})
    monkeypatch.setattr('app.generation.secrets.choice',lambda values:values[0])
    values=[varied_choice('example',['a','b','c','d','e','f']) for _ in range(10)]
    for index,value in enumerate(values):
        assert value not in values[max(0,index-4):index]


@pytest.mark.parametrize('female',[False,True])
def test_names_combine_gender_consistent_parts_without_recent_repetition(monkeypatch,female):
    monkeypatch.setattr('app.generation.recent_choices',{})
    monkeypatch.setattr('app.generation.secrets.choice',lambda values:female if values==(False,True) else values[0])
    names=[fictional_name() for _ in range(5)]
    assert len(set(names))==5
    for name in names:
        surname,first,patronymic=name.split()
        assert surname in (catalog.FEMALE_SURNAMES if female else catalog.MALE_SURNAMES)
        assert first in (catalog.FEMALE_NAMES if female else catalog.MALE_NAMES)
        assert patronymic in (catalog.FEMALE_PATRONYMICS if female else catalog.MALE_PATRONYMICS)


@pytest.mark.parametrize('difficulty',['basic','advanced','complex'])
def test_intrusion_difficulty_changes_information_limits(difficulty):
    text,_=generate_local({'title':'Вскрыта квартира','features':[]},{},difficulty)
    assert ('Есть ли кто-то внутри, не знаю.' in text.caller_text)==(difficulty!='basic')
    assert ('Как давно это произошло и кто здесь был' in text.caller_text)==(difficulty=='complex')
    assert 'Сейчас могу передать только эти сведения.' not in text.caller_text


def test_model_receives_explicit_difficulty_guidance(monkeypatch):
    requests=mock_model(monkeypatch,[draft()])
    generate_local({'title':'задымление'},{},'complex')
    request=json.loads(requests[0]['messages'][1]['content'])
    assert request['требования_сложности']==catalog.DIFFICULTY_GUIDANCE['complex']
    assert request['стиль_реплики']


@pytest.mark.parametrize('duration',['12 дней','две недели','21 день','три недели','полгода'])
def test_vehicle_duration_is_consistent_in_caller_text_and_summary(monkeypatch,duration):
    from app import generation
    original=generation.varied_choice
    monkeypatch.setattr(generation,'varied_choice',lambda key,values:
                        duration if key=='vehicle-duration' else original(key,values))
    text,_=generate_local({'title':'Брошеная автомашина','features':['Автомашина','Брошенная']},{},'basic')
    assert f'месте {duration}.' in text.caller_text
    assert f'месте {duration}.' in text.expected_description


@pytest.mark.parametrize('days,expected',[(2,'2 дня'),(11,'11 дней'),(12,'12 дней'),(21,'21 день'),(22,'22 дня'),(25,'25 дней')])
def test_russian_day_duration_agreement(days,expected):
    assert catalog.day_duration(days)==expected


@pytest.mark.parametrize('unit,expected',[
    ('days','37 дней'),('days','сорок два дня'),('weeks','одну неделю'),
    ('weeks','двадцать одну неделю'),('months','семнадцать месяцев'),('months','18 месяцев'),
])
def test_duration_ranges_include_values_beyond_examples(unit,expected):
    assert expected in catalog.vehicle_duration_options(unit)


def test_longstanding_duration_range_excludes_short_stays():
    assert '2 дня' not in catalog.LONG_VEHICLE_DURATIONS
    assert 'две недели' not in catalog.LONG_VEHICLE_DURATIONS
    assert 'сто семьдесят три дня' in catalog.LONG_VEHICLE_DURATIONS
    assert 'двадцать четыре месяца' in catalog.LONG_VEHICLE_DURATIONS
    assert '1 день' not in catalog.VEHICLE_DURATIONS


@pytest.mark.parametrize('profile',list(catalog.VEHICLE_CONDITIONS))
def test_vehicle_condition_profile_matches_caller_and_summary(monkeypatch,profile):
    from app import generation
    original=generation.varied_choice
    monkeypatch.setattr(generation,'varied_choice',lambda key,values:
                        profile if key=='vehicle-condition' else original(key,values))
    text,_=generate_local({'title':'Брошеная автомашина','features':['Автомашина','Брошенная']},{},'complex')
    primary,secondary=catalog.VEHICLE_CONDITIONS[profile]
    for phrases in (primary,secondary):
        selected=next(phrase for phrase in phrases if phrase in text.caller_text)
        assert selected in text.expected_description
    if profile=='wheels_missing':
        assert 'спущен' not in text.caller_text.lower()
        assert 'колёса на месте' not in text.caller_text.lower()
    if profile=='doors_missing':
        assert 'двери закрыты' not in text.caller_text.lower()
        assert 'через грязное стекло' not in text.caller_text.lower()
    if profile=='intact':
        assert not any(word in text.caller_text.lower() for word in ('разбит','вмятин','сломан','ржавчин'))


def test_longstanding_vehicle_does_not_choose_intact_profile(monkeypatch):
    from app import generation
    original=generation.varied_choice
    def choose(key,values):
        if key=='vehicle-condition':
            assert 'intact' not in values
        return original(key,values)
    monkeypatch.setattr(generation,'varied_choice',choose)
    generate_local({'title':'Брошеная автомашина (давно)','features':['Автохлам']},{},'basic')
