from copy import deepcopy
import pytest
from sqlalchemy import select
from test_health import client,auth
from app.dds import damage, changed_fields, received_card
from app.models import Scenario,ScenarioSettings,SessionRun,RunContext,CardEvent,Lesson,User,VoipCall
from app.training import dds_status_options,dds_status_complete


def build(client,teacher,variants=None,source='generated',count=1):
    category=client.get('/api/classifier/types?q=1050102',headers=teacher).json()[0]['category']
    response=client.post('/api/dds/exercises',headers=teacher,json={'title':'Реагирование ДДС','categories':[category],'variants':variants or ['clean'],'source':source,'count':count,'norm_seconds':180})
    assert response.status_code==200,response.text
    ids=response.json()['scenario_ids']
    scenarios=client.get('/api/scenarios',headers=teacher).json()
    return [next(s for s in scenarios if s['id']==id) for id in ids]


def lesson(client,teacher,student,scenarios):
    response=client.post('/api/lessons',headers=teacher,json={'title':'ДДС занятие','mode':'dds','scenario_ids':[s['id'] for s in scenarios],'student_ids':[3]})
    assert response.status_code==200,response.text
    id=response.json()['id'];assert client.post(f'/api/lessons/{id}/start',headers=teacher).status_code==200
    response=client.post(f'/api/lessons/{id}/next',headers=student)
    assert response.status_code==200,response.text
    return id,response.json()


def validation(client,student,run,card,verdict='corrected'):
    return client.put(f'/api/dds/runs/{run["run_id"]}/validation',headers=student,json={'revision':run['revision'],'card':card,'verdict':verdict,'findings':'Исправлены сведения по обращению заявителя' if verdict!='correct' else '', 'comment':'Сообщение принято, проверено, информация передана дежурным службам'})


def complete_dds(client,student,run):
    for name in ('Начало реагирования','Прибытие','Проведение работ','Работы завершены'):
        response=client.post(f'/api/runs/{run["run_id"]}/status',headers=student,json={'service_code':run['service_code'],'status':name,'comment':'Этап отработан, сведения внесены'})
        assert response.status_code==200,response.text


@pytest.mark.parametrize('variant,fields',[('clean',set()),('typos',{'description'}),('data',{'address'}),('services',{'services'}),('mixed',{'description','address','services'})])
def test_damage_is_bounded_and_clean_reference_unchanged(variant,fields):
    gold={'description':'Обнаружено задымление квартиры.','address':'город Москва, улица Лесная, дом 26','services':['101'],'caller_name':'Иванов Алексей'}
    original=deepcopy(gold)
    result=damage(gold,variant,{'101':'Пожарная','102':'Полиция'})
    assert set(changed_fields(gold,result))==fields
    assert gold==original


def test_school_fire_in_shchukino_routes_by_district_and_subordination(client):
    from app.main import SessionLocal
    from app.schemas import CardIn
    from app.workflows import resolved_card
    base={'city':'Москва','district':'Щукино','object_name':'Школа № 1','incident_type':'Пожар в школе',
          'address':'Москва, Щукино, школа № 1','description':'Горит школа, есть пострадавшие','victims_count':2}
    with SessionLocal() as s:
        result=resolved_card(s,CardIn(**base))
        assert {'101','102','103','DDS_EDUCATION_MOSCOW','DDS_SHCHUKINO','DDS_SZAO'}<=set(result['services'])
        other=resolved_card(s,CardIn(**{**base,'district':'Другой район'}))
        assert 'DDS_SHCHUKINO' not in other['services']


def test_dds_requires_each_status_and_has_explicit_terminal_exceptions():
    assert dds_status_options('Получена службой','101')==['Принята','Не принята']
    assert 'Прибытие' not in dds_status_options('Принята','101')
    assert dds_status_complete(['Не принята'],'101')
    assert not dds_status_complete(['Принята','Работы завершены'],'101')
    assert dds_status_complete(['Принята','Начало реагирования','Прибытие','Проведение работ','Работы завершены'],'101')
    assert dds_status_complete(['Принята','Работы завершены: Завершение работ без бригады'],'103')
    assert not dds_status_complete(['Принята','Работы завершены: Завершение работ без бригады'],'101')


def test_dds_attempt_started_before_rule_change_keeps_original_transitions(client):
    from app.main import SessionLocal
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher)[0];_,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    with SessionLocal() as s:
        context=s.get(RunContext,id);snapshot=dict(context.scenario_snapshot);snapshot.pop('dds_rules_version',None)
        context.scenario_snapshot=snapshot;s.commit()
    for status in ('Принята','Работы завершены'):
        response=client.post(f'/api/runs/{id}/status',headers=student,json={'service_code':run['service_code'],'status':status,'comment':'Этап выполнен'})
        assert response.status_code==200,response.text
    response=client.post(f'/api/runs/{id}/finish',headers=student,json={})
    assert response.status_code==200,response.text
    assert 'Время первой реакции' in response.json()['parts']


def test_dds_full_cycle_uses_own_service_statuses_and_read_only_card(client):
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher)[0];gold=scenario['expected']['dds_gold']
    assert scenario['initial_card']['services']==gold['services']
    assert client.get('/api/scenarios',headers=student).json()==[]
    assert client.post(f'/api/runs/{scenario["id"]}/start',headers=student).status_code==403
    lesson_id,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    assert run['mode']=='dds' and run['dds_workflow']=='status' and run['card']['services']==scenario['initial_card']['services']
    assert not run['timer_frozen'] and run['dds_original']==run['card']
    own=client.get('/api/scenarios',headers=student).json()[0]
    assert own['expected'] is None and own['initial_card'] is None and own['source']=={}
    evidence=client.get(f'/api/reports/{id}',headers=student).json()
    assert 'expected' not in evidence and 'dds_reference_card' not in evidence
    assert 'dds_gold' not in str(run)
    assert client.post(f'/api/dds/runs/{id}/handoff',headers=student,json={'service':run['service_code'],'receiver':'Дежурный','message':'Адрес'}).status_code==409
    assert client.put(f'/api/runs/{id}/draft',headers=student,json={'revision':0,'card':gold}).status_code==403
    assert client.post(f'/api/runs/{id}/supplement/lock',headers=student).status_code==403
    assert validation(client,student,run,gold).status_code==409
    assert client.post(f'/api/runs/{id}/status',headers=student,json={'service_code':'не своя','status':'Принята','comment':'Нет'}).status_code==403
    status=lambda name,comment:client.post(f'/api/runs/{id}/status',headers=student,json={'service_code':run['service_code'],'status':name,'comment':comment})
    assert status('Принята','Сообщение принято').status_code==200
    assert status('Прибытие','Бригада прибыла').status_code==409
    assert client.post(f'/api/runs/{id}/finish',headers=student,json={}).status_code==409
    contact=client.post(f'/api/runs/{id}/work-call',headers=student,json={'destination':'Заявитель','phone':gold['caller_phone'],'message':'Уточнены обстоятельства обращения'})
    assert contact.status_code==200,contact.text
    others=[code for code in run['card']['services'] if code!=run['service_code']]
    if others:
        contact={'destination':'Другая служба','service':others[0],'message':'Согласованы действия'}
        assert client.post(f'/api/runs/{id}/work-call',headers=student,json=contact).status_code==422
        assert client.post(f'/api/runs/{id}/work-call',headers=student,json={**contact,'phone':'+7 495 000 00 00'}).status_code==422
    complete_dds(client,student,run)
    report=client.post(f'/api/runs/{id}/finish',headers=student,json={}).json()
    assert report['score']==100,report
    assert report['dds']['workflow']=='status' and report['dds']['decision']=='Принята'
    assert report['dds']['latest_status']=='Работы завершены' and not report['errors']
    teacher_report=client.get(f'/api/reports/{id}',headers=teacher).json()
    assert 'comparisons' not in teacher_report
    assert any(e['kind']=='work.call' for e in teacher_report['events'])
    import csv,io
    exported=list(csv.DictReader(io.StringIO(client.get('/api/reports/export.csv',headers=teacher).content.decode('utf-8-sig')),delimiter=';'))
    row=next(row for row in exported if row['Сессия']==str(id))
    assert row['Режим']=='ДДС' and row['Решение ДДС']=='Принята' and row['Последний статус ДДС']=='Работы завершены'
    assert client.post(f'/api/lessons/{lesson_id}/next',headers=student).json()['done']
    assert client.post(f'/api/lessons/{lesson_id}/tasks/{scenario["id"]}/start',headers=student).status_code==409
    assert client.post(f'/api/runs/{id}/finish',headers=student,json={}).json()==report


def test_dds_card_cannot_be_corrected(client):
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['clean'])[0];gold=scenario['expected']['dds_gold']
    _,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    bad=deepcopy(gold);bad['address']='Выдуманный адрес'
    response=validation(client,student,run,bad);assert response.status_code==409,response.text
    assert client.post(f'/api/runs/{id}/finish',headers=student,json={}).status_code==409
    assert client.get(f'/api/runs/{id}',headers=student).json()['card']['address']==gold['address']
    assert client.post(f'/api/runs/{id}/status',headers=student,json={'service_code':run['service_code'],'status':'Не принята','comment':'Не относится к нашей службе'}).status_code==200
    report=client.post(f'/api/runs/{id}/finish',headers=student,json={}).json()
    assert report['parts']['Решение своей службы']==0
    assert client.get(f'/api/runs/{id}',headers=student).json()['card']['address']==gold['address']


def test_dds_error_report_notifies_112_without_changing_received_card(client):
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher)[0];_,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    original=deepcopy(run['card'])
    payload={'field':'Адрес','corrected_information':'Дом 12 вместо дома 21','source':'Доклад руководителя бригады',
             'reported_to':'Оператор 112 Иванов','report_channel':'Обычный телефон'}
    assert client.post(f'/api/runs/{id}/dds-error',headers=student,json={**payload,'reported_to':''}).status_code==422
    response=client.post(f'/api/runs/{id}/dds-error',headers=student,json=payload)
    assert response.status_code==200,response.text
    assert response.json()['card']==original
    report=client.get(f'/api/reports/{id}',headers=teacher).json()
    assert any(event['kind']=='dds.error_report' and event['data']['corrected_information']==payload['corrected_information'] for event in report['events'])
    assert client.post(f'/api/dds/runs/{id}/sip-call',headers=student,json={}).status_code==422
    assert client.post(f'/api/dds/runs/{id}/sip-call',headers=student,json={'role':'brigade'}).status_code==409


def test_dds_softphone_prepares_brigade_and_superior_roles(client,monkeypatch):
    from app.main import SessionLocal
    from app.models import SipAccount
    from app import dds_telephony
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher)[0];_,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    with SessionLocal() as s:
        s.add(SipAccount(user_id=3,username='arm3',password='test-secret'));s.commit()
    greetings=[]
    monkeypatch.setattr(dds_telephony,'phone_registered',lambda name:True)
    monkeypatch.setattr(dds_telephony,'prepare_speech',lambda text,name:greetings.append(text) or {'key':'a'*64,'voice':'test','engine':'test','cached':True})
    monkeypatch.setattr(dds_telephony,'ami_action',lambda *args,**kwargs:{'Response':'Success'})
    monkeypatch.setattr(dds_telephony,'launch_worker',lambda target,args:args[-1].set())
    first=client.post(f'/api/dds/runs/{id}/sip-call',headers=student,json={'role':'brigade'})
    assert first.status_code==200,first.text
    assert first.json()['role']=='brigade' and 'Руководитель' in greetings[-1]
    calls=client.get(f'/api/reports/{id}',headers=teacher).json()['calls']
    assert calls[0]['role']=='brigade' and calls[0]['service']==run['service_code']
    assert client.post(f'/api/dds/runs/{id}/sip-call',headers=student,json={'role':'superior'}).status_code==409
    assert client.post(f'/api/telephony/runs/{id}/call/cancel',headers=student).status_code==200
    second=client.post(f'/api/dds/runs/{id}/sip-call',headers=student,json={'role':'superior'})
    assert second.status_code==200,second.text
    assert second.json()['role']=='superior' and 'Вышестоящий начальник' in greetings[-1]


def test_dds_opening_and_first_entry_deadlines_are_independent(client):
    from app.main import SessionLocal
    from app.workflows import events_for
    from datetime import datetime,timedelta
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher)[0];_,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    with SessionLocal() as s:
        context=s.get(RunContext,id);received=s.get(SessionRun,id).started_at-timedelta(seconds=10)
        context.scenario_snapshot={**context.scenario_snapshot,'queue_started_at':received.isoformat()};s.commit()
    response=client.post(f'/api/runs/{id}/status',headers=student,json={'service_code':run['service_code'],'status':'Принята','comment':'Принято'})
    assert response.status_code==200,response.text
    with SessionLocal() as s:
        event=next(event for event in events_for(s,s.get(SessionRun,id)) if event.kind=='service.status' and event.data['status']=='Принята')
        event.at=received+timedelta(seconds=181);s.commit()
    complete_dds(client,student,run)
    report=client.post(f'/api/runs/{id}/finish',headers=student,json={}).json()
    assert report['opening_seconds']==10 and report['first_entry_seconds']==181
    assert report['parts']['Открытие за 30 секунд']==10
    assert report['parts']['Первая запись за 3 минуты']==0


def test_wrong_service_rejects_card_and_other_service_contact_needs_number(client):
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['services'])[0]
    wrong=next(code for code in scenario['initial_card']['services'] if code not in scenario['expected']['dds_gold']['services'])
    payload={'title':'Ошибочная маршрутизация','mode':'dds','service_code':wrong,'scenario_ids':[scenario['id']],'student_ids':[3]}
    created=client.post('/api/lessons',headers=teacher,json=payload);assert created.status_code==200,created.text
    lesson_id=created.json()['id'];client.post(f'/api/lessons/{lesson_id}/start',headers=teacher)
    run=client.post(f'/api/lessons/{lesson_id}/next',headers=student).json();id=run['run_id']
    assert run['service_code']==wrong
    assert client.post(f'/api/runs/{id}/status',headers=student,json={'service_code':wrong,'status':'Не принята'}).status_code==422
    assert client.post(f'/api/runs/{id}/status',headers=student,json={'service_code':wrong,'status':'Не принята','comment':'Не относится к нашей службе'}).status_code==200
    report=client.post(f'/api/runs/{id}/finish',headers=student,json={}).json()
    assert report['dds']['expected_decision']=='Не принята' and report['score']==100


def test_other_service_contact_uses_number_on_received_card(client):
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['clean'])[0]
    codes=scenario['initial_card']['services']
    if len(codes)<2:pytest.skip('Для сценария нужны две службы')
    phone='+7 495 000 00 00';card={**scenario['initial_card'],'service_phones':{codes[1]:phone}}
    response=client.put(f'/api/dds/exercises/{scenario["id"]}',headers=teacher,json={'card':card})
    assert response.status_code==200,response.text
    _,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    other=codes[1] if codes[1]!=run['service_code'] else codes[0]
    if other not in card['service_phones']:pytest.skip('Первая служба совпала со второй')
    payload={'destination':'Другая служба','service':other,'phone':phone,'message':'Согласованы действия'}
    assert client.post(f'/api/runs/{id}/work-call',headers=student,json=payload).status_code==200
    assert client.post(f'/api/runs/{id}/work-call',headers=student,json={**payload,'phone':'другой номер'}).status_code==422


def test_queued_dds_card_accrues_time_before_opening_and_after_skip(client):
    from app.main import SessionLocal
    from datetime import timedelta
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenarios=build(client,teacher,['clean'],count=2)
    created=client.post('/api/lessons',headers=teacher,json={'title':'Очередь ДДС','mode':'dds','scenario_ids':[s['id'] for s in scenarios],'student_ids':[3]})
    assert created.status_code==200,created.text
    id=created.json()['id'];client.post(f'/api/lessons/{id}/start',headers=teacher)
    with SessionLocal() as s:
        row=s.get(Lesson,id);row.started_at-=timedelta(seconds=120);s.commit()
    progress=next(item for item in client.get('/api/lessons',headers=student).json() if item['id']==id)
    assert all(task['elapsed_seconds']>=120 for task in progress['tasks'])
    run=client.post(f'/api/lessons/{id}/next',headers=student).json();assert run['elapsed_seconds']>=120
    skipped=client.post(f'/api/runs/{run["run_id"]}/skip',headers=student).json()
    assert skipped['elapsed_seconds']>=120
    progress=next(item for item in client.get('/api/lessons',headers=student).json() if item['id']==id)
    assert all(task['elapsed_seconds']>=120 for task in progress['tasks'])


def test_dds_permissions_teacher_stop_and_skip_resume_timer(client):
    from app.main import SessionLocal
    from datetime import timedelta
    teacher,student,admin=auth(client,'teacher'),auth(client,'student'),auth(client,'admin')
    assert client.post('/api/dds/exercises',headers=student,json={}).status_code==403
    assert client.get('/api/dds/catalog',headers=student).status_code==403
    assert client.post('/api/dds/exercises',headers=teacher,json={'source':'students'}).status_code==422
    scenario=build(client,teacher,['clean'])[0];lesson_id,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    with SessionLocal() as s:
        context=s.get(RunContext,id);snapshot=context.scenario_snapshot
        from datetime import datetime
        context.scenario_snapshot={**snapshot,'queue_started_at':(datetime.fromisoformat(snapshot['queue_started_at'])-timedelta(seconds=45)).isoformat()};s.commit()
    report=client.post(f'/api/runs/{id}/skip',headers=student).json();assert report['elapsed_seconds']>=45
    resumed=client.post(f'/api/lessons/{lesson_id}/next',headers=student).json()
    assert resumed['run_id']==id and resumed['elapsed_seconds']>=45 and resumed['dds_original']==run['dds_original']
    assert client.put(f'/api/dds/runs/{id}/validation',headers=teacher,json={'revision':0,'card':scenario['expected']['dds_gold'],'verdict':'correct','comment':'Проверено'}).status_code==403
    response=client.post(f'/api/lessons/{lesson_id}/stop',headers=teacher);assert response.status_code==200,response.text
    final=client.get(f'/api/runs/{id}',headers=student).json();assert final['finished_at'] and final['report']['parts']['Решение своей службы']==0
    assert validation(client,student,resumed,scenario['expected']['dds_gold'],'correct').status_code==409


def test_student_sources_are_copies_and_scoped_to_teacher(client):
    from app.main import SessionLocal
    from test_workflows import create_scenario,assign_scenario
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario_id=create_scenario(client,teacher,assigned=True)
    run=client.post(f'/api/runs/{scenario_id}/start',headers=student).json()
    response=client.post(f'/api/runs/{run["run_id"]}/finish',headers=student,json={'incident_type':'Учебный','address':'Москва дом 1','description':'Учебное сообщение','services':['102'],'operator_comment':'Принято'})
    assert response.status_code==200,response.text
    response=client.post('/api/dds/exercises',headers=teacher,json={'source':'students','count':2,'variants':['clean','mixed']});assert response.status_code==200,response.text
    with SessionLocal() as s:
        original=s.get(SessionRun,run['run_id']);assert original.answers['description']=='Учебное сообщение'
        copies=[s.get(ScenarioSettings,i) for i in response.json()['scenario_ids']]
        assert all(c.source['origin_run_id']==run['run_id'] for c in copies)
        assert copies[0].initial_card['services']==['102']
        other=User(username='teacher-other',password_hash='unused',role='teacher');s.add(other);s.flush();s.get(Scenario,scenario_id).created_by=other.id;s.commit()
    assert client.post('/api/dds/exercises',headers=teacher,json={'source':'students'}).status_code==422


def test_new_dds_handoff_is_replaced_by_service_status_and_contact_log(client):
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['clean'])[0];_,run=lesson(client,teacher,student,[scenario]);id=run['run_id'];gold=scenario['expected']['dds_gold']
    assert validation(client,student,run,gold,'correct').status_code==409
    response=client.post(f'/api/dds/runs/{id}/handoff',headers=student,json={'service':run['service_code'],'receiver':'Дежурный','message':gold['address']})
    assert response.status_code==409,response.text


def test_mixed_sources_editor_and_immutable_active_snapshot(client):
    from app.main import SessionLocal
    from test_workflows import create_scenario
    teacher,student=auth(client,'teacher'),auth(client,'student')
    source=create_scenario(client,teacher,assigned=True)
    original=client.post(f'/api/runs/{source}/start',headers=student).json()
    client.post(f'/api/runs/{original["run_id"]}/finish',headers=student,json={'incident_type':'Учебный','address':'Москва дом 1','description':'Учебное сообщение','services':['102']})
    created=client.post('/api/dds/exercises',headers=teacher,json={'source':'mixed','count':4,'variants':['clean']}).json()['scenario_ids']
    with SessionLocal() as s:assert [s.get(ScenarioSettings,i).source['origin'] for i in created]==['generated','student','generated','student']
    scenarios=client.get('/api/scenarios',headers=teacher).json();scenario=next(c for c in scenarios if c['id']==created[0]);gold=scenario['expected']['dds_gold']
    payload={'card':gold,'title':'Изменённый ДДС','norm_seconds':180,'reference_text':scenario['caller_text'],'reference_card':gold}
    response=client.put(f'/api/dds/exercises/{scenario["id"]}',headers=teacher,json=payload);assert response.status_code==200,response.text
    scenario=next(c for c in client.get('/api/scenarios',headers=teacher).json() if c['id']==created[0]);assert scenario['title']=='Изменённый ДДС' and scenario['expected']['norm_seconds']==180
    _,run=lesson(client,teacher,student,[scenario]);assert run['norm_seconds']==180
    assert client.put(f'/api/dds/exercises/{scenario["id"]}',headers=teacher,json=payload).status_code==409
    admin=auth(client,'admin');other=client.post('/api/users',headers=admin,json={'username':'other-dds-student','password':'otherdds12345','role':'student'}).json()
    response=client.post('/api/login',json={'username':'other-dds-student','password':'otherdds12345'});headers={'Authorization':'Bearer '+response.json()['token']}
    assert client.put(f'/api/dds/runs/{run["run_id"]}/validation',headers=headers,json={'revision':0,'card':gold,'verdict':'correct','comment':'Проверено'}).status_code==404
    assert client.post(f'/api/dds/runs/{run["run_id"]}/sip-call',headers=headers,json={'service':'101'}).status_code==404
    assert client.get('/api/scenarios',headers=headers).json()==[]


def test_overdue_first_reaction_does_not_receive_maximum(client):
    from app.main import SessionLocal
    from datetime import timedelta
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['clean'])[0];gold=scenario['expected']['dds_gold'];_,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    with SessionLocal() as s:
        context=s.get(RunContext,id);snapshot=context.scenario_snapshot
        from datetime import datetime
        started=datetime.fromisoformat(snapshot['queue_started_at'])-timedelta(seconds=300)
        context.scenario_snapshot={**snapshot,'queue_started_at':started.isoformat()}
        context.registered_at-=timedelta(seconds=300);s.commit()
    service=run['service_code']
    assert client.post(f'/api/runs/{id}/status',headers=student,json={'service_code':service,'status':'Принята','comment':'Принято'}).status_code==200
    complete_dds(client,student,run)
    report=client.post(f'/api/runs/{id}/finish',headers=student,json={}).json()
    assert report['parts']['Открытие за 30 секунд']==0 and report['parts']['Первая запись за 3 минуты']==0
    assert report['opening_seconds']>=300 and report['first_entry_seconds']>=300


def test_teacher_stop_closes_active_dds_sip_call(client,monkeypatch):
    from app.main import SessionLocal
    from datetime import datetime,timezone
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['clean'])[0];lesson_id,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    with SessionLocal() as s:
        call=VoipCall(run_id=id,state='answered',sound_key='a'*64,channel='PJSIP/arm3-test',answered_at=datetime.now(timezone.utc));s.add(call);s.commit();cid=call.id
    actions=[]
    monkeypatch.setattr('app.telephony.ami_action',lambda action,**kwargs:actions.append((action,kwargs)) or {})
    response=client.post(f'/api/lessons/{lesson_id}/stop',headers=teacher);assert response.status_code==200,response.text
    with SessionLocal() as s:
        call=s.get(VoipCall,cid);assert call.state=='cancelled' and call.ended_at
    assert ('Hangup',{'Channel':'PJSIP/arm3-test'}) in actions


def test_dds_template_sets_own_service_and_does_not_grade_legacy_sip_policy(client):
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['clean'])[0];gold=scenario['expected']['dds_gold']
    service=gold['services'][0]
    payload={'title':'Реагирование службы','mode':'dds','require_sip':True,'service_code':service,'scenario_ids':[scenario['id']],'student_ids':[]}
    response=client.post('/api/lesson-templates',headers=teacher,json=payload)
    assert response.status_code==200,response.text
    template=response.json()['id']
    lesson_id=client.post(f'/api/lesson-templates/{template}/assign',headers=teacher,json={'student_ids':[3]}).json()['id']
    assert client.post(f'/api/lessons/{lesson_id}/start',headers=teacher).status_code==200
    run=client.post(f'/api/lessons/{lesson_id}/next',headers=student).json();id=run['run_id']
    assert run['service_code']==service and run['require_sip'] is False
    assert client.post(f'/api/runs/{id}/status',headers=student,json={'service_code':service,'status':'Принята','comment':'Принято'}).status_code==200
    complete_dds(client,student,run)
    response=client.post(f'/api/runs/{id}/finish',headers=student,json={})
    assert response.status_code==200,response.text
    report=response.json();assert report['score']==100 and report['dds']['workflow']=='status'
    import csv,io
    rows=list(csv.DictReader(io.StringIO(client.get('/api/reports/export.csv',headers=teacher).content.decode('utf-8-sig')),delimiter=';'))
    exported=next(row for row in rows if row['Сессия']==str(id))
    assert exported['Своя служба ДДС']==service and exported['Решение ДДС']=='Принята'


def test_teacher_stop_records_incomplete_dds_statuses(client):
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['clean'])[0]
    payload={'title':'Остановка ДДС','mode':'dds','scenario_ids':[scenario['id']],'student_ids':[3]}
    id=client.post('/api/lessons',headers=teacher,json=payload).json()['id']
    client.post(f'/api/lessons/{id}/start',headers=teacher)
    run=client.post(f'/api/lessons/{id}/next',headers=student).json()
    assert client.post(f'/api/lessons/{id}/stop',headers=teacher).status_code==200
    report=client.get(f'/api/runs/{run["run_id"]}',headers=student).json()['report']
    assert report['dds']['workflow']=='status' and report['score']<=10
    assert report['parts']['Решение своей службы']==0


def test_dds_report_calls_show_service_recipient_recording_and_do_not_reuse_112_audio(client,monkeypatch,tmp_path):
    from app.main import SessionLocal
    from app.workflows import now
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['clean'])[0];_,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    monkeypatch.setenv('CALL_MEDIA_ROOT',str(tmp_path))
    service=scenario['expected']['dds_gold']['services'][0]
    with SessionLocal() as s:
        call=VoipCall(run_id=id,state='ended',sound_key='a'*64,answered_at=now(),ended_at=now());s.add(call);s.flush();call_id=call.id
        s.add(CardEvent(run_id=id,user_id=3,kind='dds.call.prepared',data={'call_id':call_id,'service':service,'name':'Учебная служба','extension':'80888'}))
        s.add(CardEvent(run_id=id,user_id=3,kind='dds.handoff',data={'call_id':call_id,'service':service,'receiver':'Дежурный Иванов','message':'Передан адрес','outcome':'accepted','transport':'SIP'}));s.commit()
    (tmp_path/f'recording-{id}.wav').write_bytes(b'old 112 recording')
    evidence=client.get(f'/api/reports/{id}',headers=teacher).json()['calls'][0]
    assert evidence['direction']=='outbound' and evidence['service']==service
    assert evidence['service_name']=='Учебная служба' and evidence['extension']=='80888'
    assert evidence['handoffs'][0]['receiver']=='Дежурный Иванов' and evidence['recording_available'] is False
    (tmp_path/f'recording-{id}-{call_id}.wav').write_bytes(b'student and service recording')
    evidence=client.get(f'/api/reports/{id}',headers=teacher).json()['calls'][0]
    assert evidence['recording_available'] is True
    response=client.get(f'/api/telephony/runs/{id}/recording?call_id={call_id}',headers=teacher)
    assert response.status_code==200 and response.content==b'student and service recording'


def test_dds_template_without_students_can_be_edited_and_assigned_to_different_groups(client):
    teacher,student,admin=auth(client,'teacher'),auth(client,'student'),auth(client,'admin')
    scenarios=build(client,teacher,['clean','mixed'],count=2)
    payload={'title':'Заготовка ДДС без группы','mode':'dds','require_sip':True,'scenario_ids':[scenarios[0]['id']]}
    response=client.post('/api/lesson-templates',headers=teacher,json=payload)
    assert response.status_code==200,response.text
    id=response.json()['id'];path=f'/api/lesson-templates/{id}'
    assert client.get('/api/lessons',headers=student).json()==[]
    assert client.get('/api/scenarios',headers=student).json()==[]
    saved=next(t for t in client.get('/api/lesson-templates',headers=teacher).json() if t['id']==id)
    assert saved['student_ids']==[]
    updated={**payload,'title':'ДДС: набор из двух билетов','scenario_ids':[x['id'] for x in scenarios]}
    assert client.put(path,headers=teacher,json=updated).status_code==200
    new_student=client.post('/api/users',headers=admin,json={'username':'dds_later_group','password':'laterGroup12345','role':'student'})
    assert new_student.status_code==200,new_student.text
    other_id=new_student.json()['id']
    assigned=[]
    for group in [[3],[other_id]]:
        response=client.post(path+'/assign',headers=teacher,json={'student_ids':group})
        assert response.status_code==200,response.text
        assigned.append(response.json()['id'])
    assert len(set(assigned))==2
    saved=next(t for t in client.get('/api/lesson-templates',headers=teacher).json() if t['id']==id)
    assert saved['student_ids']==[] and saved['require_sip'] is False
    assert client.put(path,headers=teacher,json={**updated,'scenario_ids':[scenarios[1]['id']]}).status_code==200
    lessons=client.get('/api/lessons',headers=teacher).json()
    for row in (x for x in lessons if x['id'] in assigned):
        assert row['scenario_ids']==updated['scenario_ids'] and row['require_sip'] is False
    assert {x['id'] for x in client.get('/api/scenarios',headers=student).json()}==set(updated['scenario_ids'])
