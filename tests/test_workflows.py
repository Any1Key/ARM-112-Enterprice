import pytest
from sqlalchemy import select
from test_health import client, auth
from app.training import available_statuses, COMPLETE, REFUSED, NO_CREW


def assign_scenario(client,teacher,scenario,student_id=3):
    response=client.post('/api/sms/incoming',headers=teacher,json={'student_id':student_id,'scenario_id':scenario,'aon':'+7 921 555 18 42','text':'Задание назначено преподавателем'})
    assert response.status_code==200,response.text

def create_scenario(client, teacher, mode='call', published=True, service='102', assigned=False):
    payload={'title':'Сценарий для интеграционной проверки','category':'Учебный','caller_text':'Учебное сообщение',
             'expected':{'incident_type':'Учебный','address':'Москва дом 1','services':[service],
                         'operator_comment':'Сообщение принято бригада направлена','norm_seconds':30}}
    result=client.post('/api/scenarios',headers=teacher,json=payload)
    assert result.status_code==200,result.text
    identifier=result.json()['id']
    response=client.put(f'/api/scenarios/{identifier}/settings',headers=teacher,
                        json={'mode':mode,'published':published,'initial_card':{'incident_type':'Учебный','address':'Москва дом 1',
                              'description':'Учебное сообщение','services':[service]}})
    assert response.status_code==200,response.text
    if assigned:assign_scenario(client,teacher,identifier)
    return identifier


def test_full_classifier_import_is_versioned_and_idempotent(client):
    admin=auth(client,'admin')
    manifest=client.get('/api/classifier',headers=admin).json()['version']
    assert manifest['count']==1283 and len(manifest['groups'])==24
    assert len(manifest['sha256'])==64
    result=client.post('/api/classifier/import',headers=admin).json()
    assert result['version_id']==manifest['id']
    items=client.get('/api/classifier/types?q=1050102',headers=admin).json()
    assert len(items)==1 and items[0]['title']=='задымление: квартира'
    assert items[0]['features']==['жилой дом','квартира','дым']
    default=client.post('/api/classifier/resolve',headers=admin,json={'classifier_ids':[items[0]['id']],'flags':{}}).json()
    victims=client.post('/api/classifier/resolve',headers=admin,json={'classifier_ids':[items[0]['id']],'flags':{'victims':True}}).json()
    assert any(service['code']=='101' for service in default['services'])
    assert any(service['code']=='103' for service in victims['services'])
    assert not any(service['code']=='103' for service in default['services'])
    assert any(mapping['column']==25 for service in victims['services'] if service['code']=='103' for mapping in service['mappings'])
    assert client.post('/api/classifier/resolve',headers=admin,json={'classifier_ids':[999999]}).status_code==422
    assert client.post('/api/classifier/resolve',headers=admin,json={'flags':{'made_up':True}}).status_code==422


def test_draft_restore_revision_and_required_dispatch(client):
    student,teacher=auth(client,'student'),auth(client,'teacher')
    scenario=create_scenario(client,teacher,assigned=True)
    type_=client.get('/api/classifier/types?q=1050102',headers=student).json()[0]
    start=client.post(f'/api/runs/{scenario}/start',headers=student).json()
    repeated=client.post(f'/api/runs/{scenario}/start',headers=student).json()
    assert start['run_id']==repeated['run_id']
    run_id=start['run_id']
    draft={'revision':0,'card':{'classifier_ids':[type_['id']],'address':'Москва дом 1','description':'Дым',
                              'services':[],'on_site_phone':'123','descriptive_address':'На чердаке'}}
    response=client.put(f'/api/runs/{run_id}/draft',headers=student,json=draft)
    assert response.status_code==200,response.text
    assert '101' in response.json()['card']['services']
    assert response.json()['revision']==1
    assert client.put(f'/api/runs/{run_id}/draft',headers=student,json=draft).status_code==409
    restored=client.get('/api/active-run',headers=student).json()
    assert restored['card']['on_site_phone']=='123' and restored['revision']==1
    registered=client.post(f'/api/runs/{run_id}/register',headers=student).json()
    assert registered['status']=='Зарегистрирована'
    assert registered['service_history']['101'][0]['status']=='Добавлена'
    assert client.put(f'/api/runs/{run_id}/draft',headers=student,json={**draft,'revision':1}).status_code==409
    assert client.post(f'/api/runs/{run_id}/status',headers=student,json={'service_code':'101','status':'Принята'}).status_code==403
    assert client.post(f'/api/runs/{run_id}/finish',headers=student,json={}).status_code==200
    assert client.get('/api/active-run',headers=student).json() is None


def test_teacher_led_dispatch_transitions_and_reviews(client):
    student,teacher,admin=auth(client,'student'),auth(client,'teacher'),auth(client,'admin')
    identifier=create_scenario(client,teacher,mode='dispatch')
    lesson=client.post('/api/lessons',headers=teacher,json={'title':'Практика ДДС','mode':'dispatch',
                       'scenario_ids':[identifier],'student_ids':[3],'service_code':'102'})
    assert lesson.status_code==200,lesson.text
    lesson_id=lesson.json()['id']
    assert client.post(f'/api/lessons/{lesson_id}/next',headers=student).status_code==409
    assert client.post(f'/api/lessons/{lesson_id}/start',headers=teacher).status_code==200
    run=client.post(f'/api/lessons/{lesson_id}/next',headers=student).json()
    assert identifier in [scenario['id'] for scenario in client.get('/api/scenarios',headers=student).json()]
    assert run['mode']=='dispatch' and run['registered_at']
    assert run['available_statuses']['102']==['Принята','Не принята']
    run_id=run['run_id']
    status=lambda status,comment='':client.post(f'/api/runs/{run_id}/status',headers=student,
                                        json={'service_code':'102','status':status,'comment':comment})
    assert status('Работы завершены').status_code==409
    assert status('Не принята').status_code==422
    assert status('Не принята','Дубль, реагирование по карточке 12').status_code==200
    assert status('Принята','Сообщение принято бригада направлена').status_code==200
    assert client.get(f'/api/lessons/{lesson_id}/monitor',headers=teacher).json()[0]['run_id']==run_id
    report=client.post(f'/api/runs/{run_id}/finish',headers=student,json={'address':'Подмена'}).json()
    assert report['score']==100 and report['reaction_seconds']<=30
    assert client.get(f'/api/runs/{run_id}',headers=student).json()['card']['address']=='Москва дом 1'
    assert client.post(f'/api/runs/{run_id}/review',headers=admin,json={'score':50,'comment':'Тест'}).status_code==403
    assert client.post(f'/api/runs/{run_id}/review',headers=teacher,json={'score':85,'comment':'Уточняйте ориентиры'}).status_code==200
    report_row=client.get('/api/reports',headers=student).json()[0]
    assert report_row['score']==100 and report_row['expert_review']['score']==85
    assert any(event['action']=='report.review' for event in client.get('/api/audit',headers=admin).json())
    assert client.post(f'/api/lessons/{lesson_id}/next',headers=student).json()['done'] is True


def test_stop_lesson_finishes_current_draft_and_blocks_changes(client):
    student,teacher,admin=auth(client,'student'),auth(client,'teacher'),auth(client,'admin')
    identifier=create_scenario(client,teacher)
    lesson_id=client.post('/api/lessons',headers=teacher,json={'title':'Карточки','scenario_ids':[identifier],
                         'student_ids':[3],'service_code':'112'}).json()['id']
    client.post(f'/api/lessons/{lesson_id}/start',headers=teacher)
    run=client.post(f'/api/lessons/{lesson_id}/next',headers=student).json()
    run_id=run['run_id']
    draft={'revision':0,'card':{'incident_type':'Учебный','address':'Москва дом 1','description':'Учебное сообщение',
                              'services':['102'],'operator_comment':'Сообщение принято бригада направлена'}}
    assert client.put(f'/api/runs/{run_id}/draft',headers=student,json=draft).status_code==200
    assert client.put(f'/api/scenarios/{identifier}/settings',headers=admin,json={'published':True}).status_code==409
    assert client.post(f'/api/lessons/{lesson_id}/stop',headers=teacher).status_code==200
    result=client.get(f'/api/runs/{run_id}',headers=student).json()
    assert result['finished_at'] and result['report']['score']==100
    assert client.put(f'/api/runs/{run_id}/draft',headers=student,json={**draft,'revision':1}).status_code==409


def test_medical_exception_terminal_and_no_backwards_status():
    assert available_statuses('Получена службой','103')==['Принята',NO_CREW]
    assert REFUSED not in available_statuses('Принята','103')
    assert available_statuses(COMPLETE,'102')==[]
    assert available_statuses(REFUSED,'102')==[]
    assert available_statuses('Не принята','102')==['Принята']
    assert 'Начало реагирования' not in available_statuses('Прибытие','102')


def test_account_blocking_and_role_changes_are_enforced(client):
    admin=auth(client,'admin')
    payload={'username':'new_student','password':'test-password-123','role':'student'}
    created=client.post('/api/users',headers=admin,json=payload)
    assert created.status_code==200,created.text
    identifier=created.json()['id']
    token=client.post('/api/login',json={'username':'new_student','password':payload['password']}).json()['token']
    student={'Authorization':'Bearer '+token}
    assert client.get('/api/users',headers=student).status_code==403
    assert client.put(f'/api/users/{identifier}',headers=admin,json={'role':'student','blocked':True}).status_code==200
    assert client.get('/api/reports',headers=student).status_code==403
    assert client.post('/api/login',json={'username':'new_student','password':payload['password']}).status_code==403
    assert client.put('/api/users/1',headers=admin,json={'role':'student','blocked':True}).status_code==409
    assert client.put(f'/api/users/{identifier}',headers=admin,json={'role':'teacher','blocked':False}).status_code==200
    assert client.get('/api/teaching/students',headers=student).status_code==200
    assert client.post('/api/users',headers=admin,json=payload).status_code==409


def test_card_indicators_follow_acknowledgement_and_completion_times():
    from app.training import card_indicator
    from datetime import datetime,timezone,timedelta
    from types import SimpleNamespace
    now=datetime.now(timezone.utc)
    context=SimpleNamespace(registered_at=now-timedelta(seconds=31),checked=False,processed=False)
    history={'102':[{'status':'Получена службой'}]}
    assert card_indicator(context,history,now)=='Не оповещено'
    context.checked=True;history['102'].append({'status':'Не принята'})
    assert card_indicator(context,history,now)=='Отказ'
    history['102'].append({'status':'Работы завершены'})
    assert card_indicator(context,history,now)=='Завершена'
    context.registered_at=now-timedelta(hours=49);history['102']=[{'status':'Принята'}]
    assert card_indicator(context,history,now)=='Не завершено'


def test_retry_draft_after_lost_acknowledgement_does_not_overwrite(client):
    student,teacher=auth(client,'student'),auth(client,'teacher')
    identifier=create_scenario(client,teacher,assigned=True)
    run=client.post(f'/api/runs/{identifier}/start',headers=student).json()['run_id']
    request={'revision':0,'request_id':'retry-key-123','card':{'address':'Москва дом 1','description':'Не терять'}}
    first=client.put(f'/api/runs/{run}/draft',headers=student,json=request)
    retry=client.put(f'/api/runs/{run}/draft',headers=student,json=request)
    assert first.status_code==200 and retry.json()==first.json()
    assert client.get(f'/api/runs/{run}',headers=student).json()['revision']==1


def test_imported_ticket_tasks_keep_provenance_and_require_validation(client):
    teacher,student=auth(client,'teacher'),auth(client,'student')
    materials=client.get('/api/materials',headers=teacher).json()
    assert len(materials)==96
    assert {(m['source']['ticket'],m['source']['number']) for m in materials}=={(ticket,number) for ticket in range(1,33) for number in (1,2,3)}
    assert all(m['content']['needs_review'] and len(m['source']['sha256'])==64 for m in materials)
    assert client.get('/api/materials',headers=student).status_code==403
    assert client.get('/api/materials/source/manual',headers=student).status_code==200
    assert client.get('/api/materials/source/tickets',headers=student).status_code==403


def test_csv_export_uses_the_same_report_visibility(client):
    student=auth(client,'student')
    result=client.get('/api/reports/export.csv',headers=student)
    assert result.status_code==200
    assert result.content.startswith(b'\xef\xbb\xbf')
    assert 'Первичная оценка' in result.text
    assert 'attachment' in result.headers['content-disposition']


def test_non_emergency_card_can_have_no_address_or_dispatch(client):
    teacher=auth(client,'teacher');student=auth(client,'student')
    expected={'incident_type':'Ошибочно набран номер','address':'','services':[],
              'operator_comment':'Ошибочно набран номер','norm_seconds':30}
    created=client.post('/api/scenarios',headers=teacher,json={'title':'Ошибочный вызов без адреса','category':'Справочные','caller_text':'Извините, ошибся номером','expected':expected})
    assert created.status_code==200
    identifier=created.json()['id']
    assert client.put(f'/api/scenarios/{identifier}/settings',headers=teacher,json={'published':True}).status_code==200
    assign_scenario(client,teacher,identifier)
    run=client.post(f'/api/runs/{identifier}/start',headers=student).json()
    card={**expected,'description':'Ошибочно набран номер'};card.pop('norm_seconds')
    assert client.put(f'/api/runs/{run["run_id"]}/draft',headers=student,json={'card':card,'revision':0}).status_code==200
    assert client.post(f'/api/runs/{run["run_id"]}/register',headers=student).status_code==200
    report=client.post(f'/api/runs/{run["run_id"]}/finish',headers=student,json=card).json()
    assert report['score']==100 and report['parts']['Службы']==20
    assert client.get('/api/cards?q=Ошибочный',headers=student).json()==[]
    matches=client.get('/api/cards?q=Ошибочно',headers=student).json()
    assert len(matches)==1 and matches[0]['run_id']==run['run_id']


def test_skip_preserves_draft_and_allows_next_card(client, monkeypatch):
    from app import main
    from app.models import VoipCall
    student,teacher=auth(client,'student'),auth(client,'teacher')
    first=create_scenario(client,teacher,assigned=True);second=create_scenario(client,teacher,assigned=True)
    run_id=client.post(f'/api/runs/{first}/start',headers=student).json()['run_id']
    assert client.put(f'/api/runs/{run_id}/draft',headers=student,json={'revision':0,'card':{'description':'Только часть сведений'}}).status_code==200
    with main.SessionLocal() as s:
        s.add(VoipCall(run_id=run_id,sound_key='pending',state='answered',channel='PJSIP/test'));s.commit()
    hangups=[]
    monkeypatch.setattr('app.telephony.ami_action',lambda action,**fields:hangups.append((action,fields)))
    assert client.post(f'/api/runs/{run_id}/skip',headers=teacher).status_code==403
    skipped=client.post(f'/api/runs/{run_id}/skip',headers=student)
    assert skipped.status_code==200 and skipped.json()['skipped']
    assert skipped.json()['skipped_by_name']=='student' and skipped.json()['skipped_at']
    assert client.get(f'/api/runs/{run_id}',headers=teacher).json()['report']==skipped.json()
    assert client.post(f'/api/runs/{run_id}/skip',headers=student).json()==skipped.json()
    assert hangups==[('Hangup',{'Channel':'PJSIP/test'})]
    assert client.get('/api/active-run',headers=student).json() is None
    saved=client.get(f'/api/runs/{run_id}',headers=student).json()
    assert saved['status']=='Пропущена' and saved['card']['description']=='Только часть сведений'
    assert not saved['registered_at']
    assert sum(e['kind']=='card.skip' for e in saved['events'])==1
    assert client.get('/api/reports',headers=student).json()[0]['score'] is None
    assert client.get(f'/api/telephony/runs/{run_id}',headers=student).json()[0]['state']=='cancelled'
    assert client.put(f'/api/runs/{run_id}/draft',headers=student,json={'revision':1,'card':{}}).status_code==409
    assert client.post(f'/api/runs/{second}/start',headers=student).status_code==200


def test_skip_cannot_change_completed_grade(client):
    student=auth(client,'student');teacher=auth(client,'teacher')
    scenario=next(x['id'] for x in client.get('/api/scenarios',headers=teacher).json() if x['mode']=='call' and x['published'])
    assign_scenario(client,teacher,scenario)
    run_id=client.post(f'/api/runs/{scenario}/start',headers=student).json()['run_id']
    report=client.post(f'/api/runs/{run_id}/finish',headers=student,json={}).json()
    assert client.post(f'/api/runs/{run_id}/skip',headers=student).status_code==409
    assert client.get(f'/api/runs/{run_id}',headers=student).json()['report']==report


def test_resume_skipped_card_excludes_pause_and_keeps_history(client, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from app import main, workflows
    from app.models import SessionRun
    student,teacher=auth(client,'student'),auth(client,'teacher')
    scenario=create_scenario(client,teacher,assigned=True)
    run_id=client.post(f'/api/runs/{scenario}/start',headers=student).json()['run_id']
    origin=datetime(2026,9,17,9,0,tzinfo=timezone.utc)
    with main.SessionLocal() as s:
        s.get(SessionRun,run_id).started_at=origin;s.commit()
    clock=[origin+timedelta(seconds=42)]
    monkeypatch.setattr(workflows,'now',lambda:clock[0])
    first=client.post(f'/api/runs/{run_id}/skip',headers=student).json()
    assert first['elapsed_seconds']==42
    clock[0]+=timedelta(hours=2)
    resumed=client.post(f'/api/runs/{scenario}/start',headers=student).json()
    assert resumed['run_id']==run_id and resumed['elapsed_seconds']==42
    assert resumed['finished_at'] is None and resumed['report'] is None
    assert resumed['started_at']==origin.isoformat()
    clock[0]+=timedelta(seconds=8)
    second=client.post(f'/api/runs/{run_id}/skip',headers=student).json()
    assert second['elapsed_seconds']==50
    clock[0]+=timedelta(days=1)
    resumed=client.post(f'/api/runs/{scenario}/start',headers=student).json()
    assert resumed['elapsed_seconds']==50
    clock[0]+=timedelta(seconds=5)
    report=client.post(f'/api/runs/{run_id}/finish',headers=student,json={}).json()
    assert report['elapsed_seconds']==55
    events=client.get(f'/api/runs/{run_id}',headers=teacher).json()['events']
    assert [e['data']['elapsed_seconds'] for e in events if e['kind']=='card.skip']==[42,50]
    assert sum(e['kind']=='card.resume' for e in events)==2
    fresh=client.post(f'/api/runs/{scenario}/start',headers=student).json()
    assert fresh['run_id']!=run_id
