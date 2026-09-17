from datetime import timedelta
from sqlalchemy import select
from test_health import client,auth
from test_workflows import create_scenario
from app import workflows


def start(client,student,teacher):
    scenario=create_scenario(client,teacher,assigned=True)
    return client.post(f'/api/runs/{scenario}/start',headers=student).json()


def draft(client,student,run,card):
    return client.put(f"/api/runs/{run['run_id']}/draft",headers=student,json={'revision':run['revision'],'card':card})


def test_active_classifier_is_explicit_and_legacy_adapter_preserves_conditions(client):
    from app.classifier import active_workbook,parse_workbook,resolve_rules
    from pathlib import Path
    assert '046_24' in active_workbook().name
    legacy=next(p for p in Path('source_materials').glob('*.xlsx') if '046_11' in p.name)
    parsed=parse_workbook(legacy);assert len(parsed['items'])==1281
    smoke=next(t for t in parsed['items'] if t['code']=='1050102')
    assert smoke['main_service']=='MCHS' and smoke['reaction_scenario']
    assert any(x['code']=='103' for x in resolve_rules(smoke['rules'],{'victims':True}))
    assert not any(x['code']=='103' for x in resolve_rules(smoke['rules'],{'victims':False}))
    assert any(m['column']==26 for x in resolve_rules(smoke['rules'],{'victims':True}) if x['code']=='103' for m in x['mappings'])


def test_questionnaire_catalog_and_tri_state_route_services(client):
    student,teacher=auth(client,'student'),auth(client,'teacher')
    cards=client.get('/api/questionnaires',headers=student).json()['cards'];assert len(cards)==24
    assert sum(len(c['types']) for c in cards)==1283
    fire=next(c for c in cards if c['id']=='1');assert any(q['type']=='multi' for q in fire['questions'])
    run=start(client,student,teacher)
    kind=client.get('/api/classifier/types?q=1050102',headers=student).json()[0]
    card={'classifier_ids':[kind['id']],'description':'Дым в квартире','address':'Тула дом 1','questionnaire_answers':{'1':{'victims':'yes','gasified':'unknown'}}}
    saved=draft(client,student,run,card);assert saved.status_code==200,saved.text
    assert '103' in saved.json()['card']['services']
    assert saved.json()['card']['questionnaire_answers']['1']['gasified']=='unknown'
    invalid=draft(client,student,{**run,'revision':1},{**card,'questionnaire_answers':{'1':{'victims':'maybe'}}})
    assert invalid.status_code==422


def test_timer_stops_at_registration_and_supplements_lock_and_immutability(client,monkeypatch):
    student,teacher=auth(client,'student'),auth(client,'teacher');run=start(client,student,teacher)
    original=workflows.now();monkeypatch.setattr(workflows,'now',lambda:original+timedelta(seconds=20))
    response=draft(client,student,run,{'description':'Дым','address':'Тула дом 1','caller_name':'Иван','services':['102']});assert response.status_code==200
    registered=client.post(f"/api/runs/{run['run_id']}/register",headers=student).json();assert registered['timer_frozen']
    elapsed=registered['elapsed_seconds'];monkeypatch.setattr(workflows,'now',lambda:original+timedelta(seconds=80))
    reread=client.get(f"/api/runs/{run['run_id']}",headers=student).json();assert reread['elapsed_seconds']==elapsed
    assert reread['postprocessing_seconds']>=60
    endpoint=f"/api/runs/{run['run_id']}/supplement"
    lock=client.post(endpoint+'/lock',headers=student).json()
    assert client.post(endpoint+'/lock',headers=student).status_code==409
    assert client.put(endpoint+'?token='+lock['token'],headers=student,json={'revision':lock['revision'],'fields':{'caller_name':'Другой'}}).status_code==422
    update=client.put(endpoint+'?token='+lock['token'],headers=student,json={'revision':lock['revision'],'fields':{'description':'Дым усилился','victims':True,'victims_count':2}})
    assert update.status_code==200,update.text
    assert update.json()['card']['victims_count']==2
    assert update.json()['elapsed_seconds']==elapsed
    assert any(x['kind']=='card.supplement' for x in update.json()['events'])


def test_empty_contact_and_nonempty_work_calls(client):
    student,teacher=auth(client,'student'),auth(client,'teacher');run=start(client,student,teacher)
    assert draft(client,student,run,{'no_contact':True}).status_code==200
    registered=client.post(f"/api/runs/{run['run_id']}/register",headers=student).json()
    assert registered['status']=='Завершена' and registered['checked'] and registered['card']['services']==[]
    endpoint=f"/api/runs/{run['run_id']}/work-call"
    assert client.post(endpoint,headers=student,json={}).status_code==422
    assert client.post(endpoint,headers=student,json={'destination':'Дежурный учреждения'}).status_code==200
    assert client.post(endpoint,headers=student,json={'service':'Выдуманная служба'}).status_code==422


def test_sms_queue_acceptance_reply_read_history_and_presence(client):
    student,teacher=auth(client,'student'),auth(client,'teacher');scenario=create_scenario(client,teacher)
    message={'student_id':3,'scenario_id':scenario,'aon':'+7 921 555 18 42','text':'Не могу говорить. Дым в квартире.','latitude':55.75,'longitude':37.62}
    assert client.post('/api/sms/incoming',headers=student,json=message).status_code==403
    incoming=client.post('/api/sms/incoming',headers=teacher,json=message);assert incoming.status_code==200,incoming.text
    assert client.get('/api/sms/queue',headers=student).json()==[]
    assert client.put('/api/operator/presence',headers=student,json={'state':'available'}).status_code==200
    queue=client.get('/api/sms/queue',headers=student).json();assert len(queue)==1
    accepted=client.post(f"/api/sms/{queue[0]['id']}/accept",headers=student);assert accepted.status_code==200,accepted.text
    run=accepted.json();assert run['card']['aon']==message['aon'] and run['card']['channel']=='SMS'
    assert run['sms_unread']==1 and run['card']['latitude']==55.75
    assert client.get('/api/operator/presence',headers=student).json()['state']=='unavailable'
    assert client.post('/api/sms/incoming',headers=teacher,json={**message,'text':'Дым усилился'}).json()['run_id']==run['run_id']
    assert client.post(f"/api/runs/{run['run_id']}/sms",headers=student,json={'text':'Уточните этаж'}).status_code==200
    history=client.get(f"/api/runs/{run['run_id']}/sms",headers=student).json();assert len(history)==3
    assert client.get(f"/api/runs/{run['run_id']}",headers=student).json()['sms_unread']==0


def test_services_exception_needs_reason_and_is_preserved(client):
    student,teacher=auth(client,'student'),auth(client,'teacher');run=start(client,student,teacher)
    kind=client.get('/api/classifier/types?q=1050102',headers=student).json()[0]
    card={'classifier_ids':[kind['id']],'excluded_services':['101'],'description':'Дым','address':'Тула дом 1'}
    assert draft(client,student,run,card).status_code==422
    result=draft(client,student,run,{**card,'service_override_reason':'Повторная карточка, служба уже оповещена'})
    assert result.status_code==200 and '101' not in result.json()['card']['services']


def test_links_reject_cycles_and_reminders_preserve_owner(client):
    student,teacher=auth(client,'student'),auth(client,'teacher');first=start(client,student,teacher)
    draft(client,student,first,{'description':'Дым','address':'Тула дом 1'})
    client.post(f"/api/runs/{first['run_id']}/register",headers=student)
    client.post(f"/api/runs/{first['run_id']}/finish",headers=student,json={})
    second=start(client,student,teacher);draft(client,student,second,{'description':'Ещё дым','address':'Тула дом 1'})
    client.post(f"/api/runs/{second['run_id']}/register",headers=student)
    matches=client.get(f"/api/runs/{second['run_id']}/matches",headers=student).json();assert any(x['run_id']==first['run_id'] for x in matches)
    linked=client.post(f"/api/runs/{second['run_id']}/link",headers=student,json={'parent_id':first['run_id']});assert linked.status_code==200
    assert client.post(f"/api/runs/{second['run_id']}/link",headers=student,json={'parent_id':second['run_id']}).status_code==409
    reminder={'text':'Проверить ответ службы','at':(workflows.now()+timedelta(minutes=5)).isoformat()}
    assert client.post(f"/api/runs/{second['run_id']}/reminder",headers=student,json=reminder).status_code==200
    assert client.get('/api/reminders',headers=student).json()==[]


def test_existing_registered_card_freezes_without_new_snapshot_field(client,monkeypatch):
    from app import main
    from app.models import SessionRun,RunContext
    student,teacher=auth(client,'student'),auth(client,'teacher');run=start(client,student,teacher)
    with main.SessionLocal() as s:
        row=s.get(SessionRun,run['run_id']);context=s.get(RunContext,row.id)
        context.registered_at=workflows.aware(row.started_at)+timedelta(seconds=15);s.commit()
    current=workflows.now();monkeypatch.setattr(workflows,'now',lambda:current+timedelta(hours=2))
    restored=client.get(f"/api/runs/{run['run_id']}",headers=student).json()
    assert restored['elapsed_seconds']==15 and restored['timer_frozen']


def test_questionnaire_unknown_fields_and_out_of_range_numbers_are_rejected(client):
    student,teacher=auth(client,'student'),auth(client,'teacher');run=start(client,student,teacher)
    for values in ({'building_floors':0},{'age':140},{'age':'сорок'},{'imaginary':'yes'},{'fire_objects':'квартира'}):
        assert draft(client,student,run,{'questionnaire_answers':{'1':values}}).status_code==422


def test_unanswered_multi_question_is_graded_without_server_error():
    from app.questionnaires import grade_answers
    from app.schemas import CardIn
    report={'score':100,'parts':{'Смысл текста':30},'errors':[]}
    result=grade_answers(report,{'questionnaire_answers':{'1':{'fire_objects':['квартира','подъезд']}}},CardIn(questionnaire_answers={'1':{'fire_objects':None}}))
    assert result['parts']['Опросная карта']==0
    assert result['questionnaire_checks'][0]['correct'] is False


def test_teacher_sms_assigns_scenario_despite_other_lesson_and_resumes(client):
    student,teacher=auth(client,'student'),auth(client,'teacher')
    other=create_scenario(client,teacher)
    lesson=client.post('/api/lessons',headers=teacher,json={'title':'Другое занятие','mode':'call','scenario_ids':[other],'student_ids':[3],'service_code':'112'})
    assert lesson.status_code==200,lesson.text
    scenario=create_scenario(client,teacher)
    assert client.post(f'/api/runs/{scenario}/start',headers=student).status_code==403
    message={'student_id':3,'scenario_id':scenario,'aon':'+7 921 555 18 42','text':'Не могу говорить, нужна помощь.'}
    incoming=client.post('/api/sms/incoming',headers=teacher,json=message)
    assert incoming.status_code==200,incoming.text
    assert client.put('/api/operator/presence',headers=student,json={'state':'available'}).status_code==200
    accepted=client.post(f"/api/sms/{incoming.json()['id']}/accept",headers=student)
    assert accepted.status_code==200,accepted.text
    run=accepted.json();assert run['card']['channel']=='SMS'
    assert client.post(f"/api/runs/{run['run_id']}/skip",headers=student).status_code==200
    resumed=client.post(f'/api/runs/{scenario}/start',headers=student)
    assert resumed.status_code==200 and resumed.json()['run_id']==run['run_id']
    assert client.post(f"/api/runs/{run['run_id']}/skip",headers=student).status_code==200
    unassigned=create_scenario(client,teacher)
    assert client.post(f'/api/runs/{unassigned}/start',headers=student).status_code==403


def test_sms_does_not_assign_unpublished_or_dispatch_scenarios(client):
    teacher=auth(client,'teacher')
    message={'student_id':3,'aon':'+7 921 555 18 42','text':'Нужна помощь.'}
    unpublished=create_scenario(client,teacher,published=False)
    assert client.post('/api/sms/incoming',headers=teacher,json={**message,'scenario_id':unpublished}).status_code==403
    dispatch=create_scenario(client,teacher,mode='dispatch')
    response=client.post('/api/sms/incoming',headers=teacher,json={**message,'scenario_id':dispatch})
    assert response.status_code==422
