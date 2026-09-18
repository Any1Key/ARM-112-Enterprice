from copy import deepcopy
import pytest
from sqlalchemy import select
from test_health import client,auth
from app.dds import damage, changed_fields, received_card
from app.models import Scenario,ScenarioSettings,SessionRun,RunContext,CardEvent,Lesson,User,VoipCall


def build(client,teacher,variants=None,source='generated',count=1):
    category=client.get('/api/classifier/types?q=1050102',headers=teacher).json()[0]['category']
    response=client.post('/api/dds/exercises',headers=teacher,json={'title':'Проверка ДДС','categories':[category],'variants':variants or ['mixed'],'source':source,'count':count,'norm_seconds':180})
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


@pytest.mark.parametrize('variant,fields',[('clean',set()),('typos',{'description'}),('data',{'address'}),('services',{'services'}),('mixed',{'description','address','services'})])
def test_damage_is_bounded_and_clean_reference_unchanged(variant,fields):
    gold={'description':'Обнаружено задымление квартиры.','address':'город Москва, улица Лесная, дом 26','services':['101'],'caller_name':'Иванов Алексей'}
    original=deepcopy(gold)
    result=damage(gold,variant,{'101':'Пожарная','102':'Полиция'})
    assert set(changed_fields(gold,result))==fields
    assert gold==original


def test_dds_full_cycle_preserves_errors_hides_key_and_reports_corrections(client):
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher)[0];gold=scenario['expected']['dds_gold']
    assert scenario['initial_card']['services']!=gold['services']
    assert client.get('/api/scenarios',headers=student).json()==[]
    assert client.post(f'/api/runs/{scenario["id"]}/start',headers=student).status_code==403
    lesson_id,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    assert run['mode']=='dds' and run['card']['services']==scenario['initial_card']['services']
    assert not run['timer_frozen'] and run['dds_original']==run['card']
    own=client.get('/api/scenarios',headers=student).json()[0]
    assert own['expected'] is None and own['initial_card'] is None and own['source']=={}
    evidence=client.get(f'/api/reports/{id}',headers=student).json()
    assert 'expected' not in evidence and 'dds_reference_card' not in evidence
    assert 'dds_gold' not in str(run)
    assert client.post(f'/api/dds/runs/{id}/handoff',headers=student,json={'service':'101','receiver':'Дежурный','message':'Адрес'}).status_code==409
    assert client.put(f'/api/runs/{id}/draft',headers=student,json={'revision':0,'card':gold}).status_code==403
    assert client.post(f'/api/runs/{id}/supplement/lock',headers=student).status_code==403
    response=validation(client,student,run,gold);assert response.status_code==200,response.text
    updated=response.json();assert updated['revision']==1 and updated['dds_original']==run['card']
    assert validation(client,student,run,gold).status_code==409
    for code in gold['services']:
        response=client.post(f'/api/dds/runs/{id}/handoff',headers=student,json={'service':code,'receiver':'Дежурный диспетчер','message':gold['address']+' '+gold['description']})
        assert response.status_code==200,response.text
    report=client.post(f'/api/runs/{id}/finish',headers=student,json={}).json()
    assert report['score']==100,report
    assert report['dds']['validation_count']==1 and not report['errors']
    assert set(f['field'] for f in report['dds']['field_results'] if f['was_wrong'])=={'address','description','services'}
    assert all(h['transport']=='Текстовая симуляция' for h in report['dds']['handoffs'])
    teacher_report=client.get(f'/api/reports/{id}',headers=teacher).json()
    assert teacher_report['dds_reference_card']==gold
    import csv,io
    exported=list(csv.DictReader(io.StringIO(client.get('/api/reports/export.csv',headers=teacher).content.decode('utf-8-sig')),delimiter=';'))
    row=next(row for row in exported if row['Сессия']==str(id))
    assert row['Режим']=='ДДС' and row['Вывод ДДС']=='corrected' and row['Способы передачи ДДС']=='Текстовая симуляция'
    assert client.post(f'/api/lessons/{lesson_id}/next',headers=student).json()['done']
    assert client.post(f'/api/lessons/{lesson_id}/tasks/{scenario["id"]}/start',headers=student).status_code==409
    assert client.post(f'/api/runs/{id}/finish',headers=student,json={}).json()==report


def test_clean_card_and_false_corrections_are_scored(client):
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['clean'])[0];gold=scenario['expected']['dds_gold']
    _,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    bad=deepcopy(gold);bad['address']='Выдуманный адрес'
    response=validation(client,student,run,bad);assert response.status_code==200,response.text
    report=client.post(f'/api/runs/{id}/finish',headers=student,json={}).json()
    assert report['score']<100 and report['dds']['field_results'][1]['correct'] is False
    assert report['parts']['Проверка карточки']==0


def test_dds_permissions_teacher_stop_and_skip_resume_timer(client):
    from app.main import SessionLocal
    from datetime import timedelta
    teacher,student,admin=auth(client,'teacher'),auth(client,'student'),auth(client,'admin')
    assert client.post('/api/dds/exercises',headers=student,json={}).status_code==403
    assert client.get('/api/dds/catalog',headers=student).status_code==403
    assert client.post('/api/dds/exercises',headers=teacher,json={'source':'students'}).status_code==422
    scenario=build(client,teacher,['clean'])[0];lesson_id,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    with SessionLocal() as s:
        r=s.get(SessionRun,id);r.started_at-=timedelta(seconds=45);s.commit()
    report=client.post(f'/api/runs/{id}/skip',headers=student).json();assert report['elapsed_seconds']>=45
    resumed=client.post(f'/api/lessons/{lesson_id}/next',headers=student).json()
    assert resumed['run_id']==id and resumed['elapsed_seconds']>=45 and resumed['dds_original']==run['dds_original']
    assert client.put(f'/api/dds/runs/{id}/validation',headers=teacher,json={'revision':0,'card':scenario['expected']['dds_gold'],'verdict':'correct','comment':'Проверено'}).status_code==403
    response=client.post(f'/api/lessons/{lesson_id}/stop',headers=teacher);assert response.status_code==200,response.text
    final=client.get(f'/api/runs/{id}',headers=student).json();assert final['finished_at'] and final['report']['parts']['Проверка карточки']==0
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


def test_sip_handoff_cannot_fake_connection_or_use_another_service(client):
    from app.main import SessionLocal
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['clean'])[0];_,run=lesson(client,teacher,student,[scenario]);id=run['run_id'];gold=scenario['expected']['dds_gold']
    assert validation(client,student,run,gold,'correct').status_code==200
    assert client.post(f'/api/dds/runs/{id}/sip-call',headers=student,json={'service':gold['services'][0] if gold['services'] else '101'}).status_code in (409,422)
    with SessionLocal() as s:
        call=VoipCall(run_id=id,state='failed',sound_key='a'*64);s.add(call);s.flush();cid=call.id
        s.add(CardEvent(run_id=id,user_id=3,kind='dds.call.prepared',data={'call_id':cid,'service':gold['services'][0] if gold['services'] else '101'}));s.commit()
    if gold['services']:
        response=client.post(f'/api/dds/runs/{id}/handoff',headers=student,json={'service':gold['services'][0],'receiver':'Дежурный','message':gold['address'],'call_id':cid})
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
    payload={'card':gold,'title':'Изменённый ДДС','norm_seconds':240,'reference_text':scenario['caller_text'],'reference_card':gold}
    response=client.put(f'/api/dds/exercises/{scenario["id"]}',headers=teacher,json=payload);assert response.status_code==200,response.text
    scenario=next(c for c in client.get('/api/scenarios',headers=teacher).json() if c['id']==created[0]);assert scenario['title']=='Изменённый ДДС' and scenario['expected']['norm_seconds']==240
    _,run=lesson(client,teacher,student,[scenario]);assert run['norm_seconds']==240
    assert client.put(f'/api/dds/exercises/{scenario["id"]}',headers=teacher,json=payload).status_code==409
    admin=auth(client,'admin');other=client.post('/api/users',headers=admin,json={'username':'other-dds-student','password':'otherdds12345','role':'student'}).json()
    response=client.post('/api/login',json={'username':'other-dds-student','password':'otherdds12345'});headers={'Authorization':'Bearer '+response.json()['token']}
    assert client.put(f'/api/dds/runs/{run["run_id"]}/validation',headers=headers,json={'revision':0,'card':gold,'verdict':'correct','comment':'Проверено'}).status_code==404
    assert client.post(f'/api/dds/runs/{run["run_id"]}/sip-call',headers=headers,json={'service':'101'}).status_code==404
    assert client.get('/api/scenarios',headers=headers).json()==[]


def test_unavailable_handoff_and_overdue_time_do_not_receive_maximum(client):
    from app.main import SessionLocal
    from datetime import timedelta
    teacher,student=auth(client,'teacher'),auth(client,'student')
    scenario=build(client,teacher,['clean'])[0];gold=scenario['expected']['dds_gold'];_,run=lesson(client,teacher,student,[scenario]);id=run['run_id']
    response=validation(client,student,run,gold,'correct');assert response.status_code==200,response.text
    for code in gold['services']:
        client.post(f'/api/dds/runs/{id}/handoff',headers=student,json={'service':code,'receiver':'Дежурный','message':gold['address'],'outcome':'unavailable'})
    with SessionLocal() as s:
        r=s.get(SessionRun,id);r.started_at-=timedelta(seconds=300);s.commit()
    report=client.post(f'/api/runs/{id}/finish',headers=student,json={}).json()
    assert report['parts']['Время обработки']==0 and report['time_deviation_seconds']>0
    if gold['services']:assert report['parts']['Передача информации']==0


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
