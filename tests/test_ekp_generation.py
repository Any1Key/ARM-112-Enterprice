"""Check every workbook row and the distinctions that change classification/dispatch."""
import hashlib
import json
from pathlib import Path
import re
import time

import pytest

from app.classifier import active_workbook, FLAGS, parse_workbook, resolve_rules
from app.generation import generate_local, draft_issues, ScenarioDraft, material_facts
from app.scenario_profiles import manifest, build_profile, case_contexts, no_victims, object_name
from test_health import client, auth


def topic(code):
    return manifest()[code]


def test_catalog_matches_every_row_of_current_workbook():
    path=active_workbook()
    original=parse_workbook(path)
    stored=json.loads(Path('app/ekp_generation_manifest.json').read_text())
    assert stored['source_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(manifest())==1283
    assert {x['group_code'] for x in manifest().values()}=={str(i) for i in range(1,25)}
    assert manifest()=={x['code']:{k:x[k] for k in ('code','group_code','title','features')} for x in original['items']}


def test_all_types_all_difficulties_have_grounded_consistent_text(monkeypatch):
    # No model or metadata request is allowed, even when the model server is unavailable.
    monkeypatch.setattr('app.generation.httpx.Client',lambda **kwargs:pytest.fail('Unexpected model request'))
    phone=re.compile(r'\+7 9\d{2} \d{3} \d{2} \d{2}')
    for t in manifest().values():
        p=build_profile(t)
        assert len(set(p.cases))>=2, t['code']
        assert not set(p.flags)-set(FLAGS), t['code']
        for difficulty in ('basic','advanced','complex'):
            generated,meta=generate_local(t,{},difficulty)
            label=(t['code'],difficulty)
            assert meta['attempts']==0 and meta['engine']=='scenario-template', label
            assert meta['profile']==p.family and meta['template_version']==9, label
            assert meta['needs_teacher_approval'] and not meta['source_used'], label
            assert generated.caller_name in generated.caller_text, label
            assert generated.address in generated.caller_text, label
            assert phone.fullmatch(generated.aon), label
            assert phone.fullmatch(generated.caller_phone) if generated.caller_phone else 'Для обратной связи' not in generated.caller_text, label
            assert not re.search(r'ЕКП|классификатор|\bкод\s*[:№]?\s*\d',generated.caller_text,re.I), label
            assert not re.search(r'город учебный|улица тренировоч|вероятно.*спят',generated.caller_text,re.I), label
            assert not set(generated.flags)-set(FLAGS), label
            assert not draft_issues(ScenarioDraft(title=generated.title,caller_text=generated.caller_text,expected_description=generated.expected_description),generated.caller_name),label
            if p.family=='smoke': assert 'пламени' in generated.caller_text or 'дым' in generated.caller_text.lower(),label
            if p.setting in ('none','unknown'): assert generated.address=='',label
            if p.setting in ('forest','water','rail'): assert ', дом ' not in generated.address,label


@pytest.mark.parametrize('code,required,forbidden,injured',[
 ('2010000',r'травм нет',r'Есть пострадавший',False),
 ('2020000',r'Есть пострадавший',r'травм нет',True),
 ('2011300',r'воду|вода',r'топливо',False),
 ('2021900',r'воду|вода',r'топливо',True),
 ('2010905',r'открытым люком',r'светофором',False),
 ('2021700',r'зажат',r'травм нет',True),
 ('3030000',r'похож',r'Произошёл взрыв',False),
 ('15200300',r'похож',r'Вижу человека, который стреляет',False),
 ('6010100',r'трещин',r'(?<!не )падают|падающий|осыпаются',False),
 ('6010200',r'падают|осыпаются',r'старая трещина',False),
 ('9010000',r'не прорван|прорыва.*нет',r'вода хлынула|Плотину прорвало',False),
 ('9020000',r'прорвало|разрыв',r'прорыва.*нет',False),
 ('10030400',r'угроза|возможен',r'выброс подтверждён|зафиксирован выброс',False),
 ('10030500',r'дозиметр|радиационный контроль',r'вижу радиацию',False),
 ('11010100',r'возможен|угроза',r'зафиксирован выброс',False),
 ('10020400',r'разлив',r'зафиксирован выброс',False),
 ('15020300',r'газов',r'Это квартира',False),
 ('15020400',r'электрощит|подстанц',r'Это квартира',False),
 ('14080209',r'проезжей частью|над дорогой',r'Дерево упало',False),
 ('18090000',r'пятилетний|шестилетний',r'четырнадцатилетний',False),
 ('18100000',r'восьмилетний|семилетний',r'четырнадцатилетний',False),
 ('18110000',r'четырнадцатилетний|двенадцатилетний',r'семилетний',False),
 ('19010101',r'одиннадцать часов утра',r'двадцать три часа',True),
 ('19010102',r'двадцать три часа',r'одиннадцать часов утра',True),
 ('15210101',r'десять минут',r'больше часа',False),
 ('15210102',r'больше часа',r'десять минут',False),
 ('24070000',r'газопровод',r'Произошёл взрыв',False),
 ('11010400',r'атаки|распылить',r'защитного контура|ёмкость',False),
 ('2010300',r'скорой помощи',r'газовой аварийной службы',False),
 ('2020601',r'газовой аварийной службы',r'травм нет',True),
 ('2010700',r'топливо',r'негорюч',False),
 ('1020302',r'столкнулись',r'Горит автомобиль',False),
 ('14060302',r'больше двух',r'не больше двух',False),
 ('14060402',r'не больше двух',r'Размер провала больше двух',False),
 ('14110303',r'пострадавших нет',r'Есть пострадавший',False),
 ('14110304',r'Есть пострадавший',r'пострадавших нет',True),
 ('14120302',r'пострадавших нет',r'Есть пострадавший',False),
 ('14120303',r'Есть пострадавший',r'пострадавших нет',True),
 ('14080104',r'пострадавших нет',r'Есть пострадавший',False),
 ('14080105',r'жалуется на боль',r'пострадавших нет',True),
])
def test_critical_distinctions_in_every_authored_case(monkeypatch,code,required,forbidden,injured):
    t=topic(code); p=build_profile(t)
    from app.generation import varied_choice
    for case in p.cases:
        monkeypatch.setattr('app.generation.varied_choice',lambda key,values:case if key=='case-'+code else case_contexts(p)[0] if key=='context-'+code else varied_choice(key,values))
        generated,_=generate_local(t,{},'basic')
        assert re.search(required,generated.caller_text,re.I),(code,case,generated.caller_text)
        assert not re.search(forbidden,generated.caller_text,re.I),(code,case,generated.caller_text)
        assert generated.flags.get('victims',False)==injured


@pytest.mark.parametrize('code',['1010101','1050102','3010100','5010100'])
def test_optional_injuries_are_explicit_and_change_dispatch(monkeypatch,code):
    original=parse_workbook(active_workbook())
    row=next(x for x in original['items'] if x['code']==code)
    from app.generation import varied_choice
    for context in case_contexts(build_profile(row)):
        monkeypatch.setattr('app.generation.varied_choice',lambda key,values:context if key=='context-'+code else varied_choice(key,values))
        generated,_=generate_local(row,{},'complex')
        codes={x['code'] for x in resolve_rules(row['rules'],generated.flags)}
        injured=context[1].get('victims',False)
        assert generated.flags.get('victims',False)==injured
        assert '103' in codes if injured else True
        if code in ('1010101','1050102'): assert ('103' in codes)==injured
        if context[0]:
            assert context[0] in generated.caller_text
            assert context[0] in generated.expected_description


def test_unknown_address_not_fabricated():
    generated,_=generate_local(topic('4290000'),{},'advanced')
    assert not generated.address
    assert 'адрес мне неизвестен' in generated.caller_text
    assert 'Адрес такой:' not in generated.caller_text


def test_all_explicit_no_injury_types_really_have_no_injuries():
    for t in manifest().values():
        if not no_victims(' '.join((t['title'],*t['features'])).lower()): continue
        for difficulty in ('basic','advanced','complex'):
            generated,_=generate_local(t,{},difficulty)
            assert not generated.flags.get('victims',False),t['code']
            assert not generated.flags.get('medical_help',False),t['code']


@pytest.mark.parametrize('code,obj',[
 ('1020701','поезд'),('1021201','эстакада'),('1021401','пешеходный переход'),
 ('3010100','многоквартирный дом'),('3020501','автобус'),('3021002','причал'),
 ('5050200','частный дом'),('5060200','многоквартирный дом'),('5080200','школа'),
 ('6010200','аэропорт'),('6050200','частный дом'),('6090200','больница'),
])
def test_object_not_replaced_by_parent_feature_or_neighbor(code,obj):
    assert object_name(topic(code))==obj
    generated,_=generate_local(topic(code),{},'basic')
    assert obj in generated.caller_text
    if obj=='многоквартирный дом': assert ', квартира ' not in generated.address


def test_mck_and_metro_use_matching_cities_and_landmarks(monkeypatch):
    from app.generation import METRO_STATIONS, varied_choice
    for t in manifest().values():
        title=t['title'].lower()
        if not re.search(r'\bмцк\b|\bметро\b|метрополитен',title): continue
        generated,_=generate_local(t,{},'basic')
        if 'мцк' in title: assert generated.address.startswith('город Москва,')
        else: assert generated.address.split(',')[0][6:] in METRO_STATIONS,t['code']
        assert 'МЦК' in generated.address or 'метро' in generated.address
    # A nearby metro vibrating a building doesn't turn the airport into a station.
    generated,_=generate_local(topic('6010200'),{},'basic')
    assert 'станция метро' not in generated.address
    for code in ('12070300','12080300'):
        p=build_profile(topic(code));case=next(c for c in p.cases if 'в тоннеле' in c)
        monkeypatch.setattr('app.generation.varied_choice',lambda key,values:case if key=='case-'+code else case_contexts(p)[0] if key=='context-'+code else varied_choice(key,values))
        generated,_=generate_local(topic(code),{},'basic')
        assert 'тоннел' in generated.address
        assert 'пассажирская платформа' not in generated.address


def test_ticket_facts_numbers_and_negated_flags_survive_without_model(monkeypatch):
    monkeypatch.setattr('app.generation.httpx.Client',lambda **kwargs:pytest.fail('Model request'))
    source={'situation':'Горит крыша частного дома, пострадавших нет, дом не газифицирован, Иванова Инна Степановна, 916-126-34-71',
            'address':'Королёв, улица Станционная, дом 28'}
    generated,meta=generate_local(topic('1050901'),source,'complex')
    assert generated.address==source['address']
    assert generated.flags['victims'] is False and generated.flags['gasified'] is False
    assert 'крыша частного дома' in generated.caller_text
    assert 'Иванова Инна Степановна' not in generated.caller_text
    assert '916-126-34-71' not in generated.caller_text
    assert generated.caller_name.endswith(('овна','евна','ична'))
    assert meta['engine']=='material-template' and meta['source_used']
    floor_source={'situation':'Дым в мусоропроводе, дом 17 этажей, заявитель на 7-м этаже, открытого пламени не видит, пострадавших нет.',
                  'address':'Москва, ул. Берзарина, дом 21, подъезд 3'}
    generated,_=generate_local(topic('1050602'),floor_source,'basic')
    assert '17 этажей' in generated.caller_text and '7-м этаже' in generated.caller_text
    assert generated.address==floor_source['address']


def test_material_without_address_stays_unknown():
    generated,_=generate_local(topic('1050102'),{'situation':'Дым в квартире, открытого пламени не видно.'},'basic')
    assert not generated.address
    assert 'адрес' in generated.caller_text


def test_all_imported_ocr_tickets_keep_event_details_and_are_usable():
    tickets=json.loads(Path('data/imports/tickets.json').read_text())['tasks']
    assert len(tickets)==96
    for ticket in tickets:
        facts=material_facts(ticket)
        assert len(facts)>=10 and facts.endswith('.'),(ticket['page'],ticket['number'])
        assert not re.search(r'9(?:[\s()-]*\d){9}',facts),(ticket['page'],ticket['number'])


@pytest.mark.parametrize('code',['10030200','10010500','15030100'])
def test_female_reporter_roles_are_agreed(monkeypatch,code):
    monkeypatch.setattr('app.generation.fictional_name',lambda female=None:'Соколова Елена Андреевна')
    generated,_=generate_local(topic(code),{},'basic')
    assert not re.search(r'\b(?:я|как) (?:сотрудник|дежурный)\b',generated.caller_text,re.I)


def test_none_source_address_is_handled_as_unknown():
    generated,_=generate_local(topic('1050102'),{'situation':'В квартире дым, пламени не видно.','address':None},'basic')
    assert generated.address==''


@pytest.mark.parametrize('code,flag',[
 ('3010700','communications_object'),('24060000','communications_object'),
 ('1021301','tunnel'),('5160100','vehicle_structure'),('5210000','construction'),
])
def test_observed_special_objects_carry_dispatch_flags(code,flag):
    generated,_=generate_local(topic(code),{},'basic')
    assert generated.flags[flag] is True


def test_ticket_victims_away_and_negative_injury_claims():
    generated,_=generate_local(topic('1050102'),{'situation':'В квартире дым. Есть пострадавшие, пострадавшие уехали в больницу.','address':'Москва, дом 1'},'basic')
    assert generated.flags['victims'] and generated.flags['victims_away']
    generated,_=generate_local(topic('2020000'),{'situation':'ДТП, травм нет, угрозы жизни нет.','address':'Москва, дом 1'},'basic')
    assert generated.flags['victims'] is False and generated.flags['life_danger'] is False


def test_new_classifier_row_fails_explicitly_instead_of_guessing():
    with pytest.raises(ValueError,match='обновить каталог'):
        generate_local({'code':'unknown','group_code':'1','title':'Новый тип','features':[]},{},'basic')


def test_generation_job_save_edit_and_dispatch_preserve_contacts_and_flags(client):
    teacher=auth(client,'teacher')
    row=client.get('/api/classifier/types?q=2021700',headers=teacher).json()[0]
    response=client.post('/api/ai/scenarios',headers=teacher,json={'classifier_id':row['id'],'difficulty':'complex'})
    assert response.status_code==200
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        job=client.get('/api/ai/jobs/'+str(response.json()['id']),headers=teacher).json()
        if job['status'] in ('done','failed'): break
        time.sleep(.01)
    assert job['status']=='done',job
    result=job['result']
    assert result['expected']['victims'] is True
    assert '103' in result['expected']['services']
    assert result['provenance']['attempts']==0
    created=client.post('/api/scenarios',headers=teacher,json=result)
    assert created.status_code==200,created.text
    identifier=created.json()['id']
    scenario=next(x for x in client.get('/api/scenarios',headers=teacher).json() if x['id']==identifier)
    for key in ('caller_name','caller_phone','aon','flags','victims','access_blocked','life_danger'):
        assert scenario['expected'][key]==result['expected'][key]
    assert client.put('/api/scenarios/'+str(identifier),headers=teacher,json=result).status_code==200
    saved=client.put(f'/api/scenarios/{identifier}/settings',headers=teacher,json={'generation_job_id':job['id'],'mode':'dispatch','initial_card':result['expected']})
    assert saved.status_code==200,saved.text
    scenario=next(x for x in client.get('/api/scenarios',headers=teacher).json() if x['id']==identifier)
    assert all(scenario['initial_card']['flags'][key]==value for key,value in result['expected']['flags'].items())


def test_restart_recovers_unfinished_jobs_and_keeps_failed_history(client):
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app.models import AiJob,User
    module=sys.modules['app.main']
    teacher=auth(client,'teacher')
    row=client.get('/api/classifier/types?q=2021700',headers=teacher).json()[0]
    with module.SessionLocal() as s:
        teacher_id=s.scalar(select(User.id).where(User.username=='teacher'))
        jobs=[AiJob(teacher_id=teacher_id,input={'classifier_id':row['id'],'difficulty':'basic'},status=status,error='old error' if status=='failed' else None) for status in ('running','queued','failed')]
        s.add_all(jobs);s.commit();identifiers=[job.id for job in jobs]
    with TestClient(module.app) as restarted:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            results=[restarted.get(f'/api/ai/jobs/{identifier}',headers=teacher).json() for identifier in identifiers]
            if all(x['status']=='done' for x in results[:2]): break
            time.sleep(.01)
        assert [x['status'] for x in results]==['done','done','failed']
        assert all(x['result']['provenance']['template_version']==9 for x in results[:2])
        assert results[2]['error']=='old error'
