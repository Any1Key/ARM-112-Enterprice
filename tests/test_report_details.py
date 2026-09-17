from datetime import timedelta
from sqlalchemy import select
from test_health import client,auth
from test_workflows import create_scenario
from app.models import CardEvent,RunContext,SessionRun,User,VoipCall
from app.workflows import now,postprocessing_seconds


def test_teacher_report_keeps_snapshot_and_student_card_and_permissions(client):
    student,teacher,admin=auth(client,'student'),auth(client,'teacher'),auth(client,'admin')
    scenario=create_scenario(client,teacher,assigned=True)
    run=client.post(f'/api/runs/{scenario}/start',headers=student).json()
    draft={'description':'Дым у подъезда','address':'Тула, Советская 24','caller_name':'Анна','victims_count':2}
    assert client.put(f"/api/runs/{run['run_id']}/draft",headers=student,json={'revision':run['revision'],'card':draft}).status_code==200
    detail=client.get(f"/api/reports/{run['run_id']}",headers=teacher)
    assert detail.status_code==200,detail.text
    evidence=detail.json()
    assert evidence['card']['description']==draft['description']
    assert evidence['expected']['address']=='Москва дом 1'
    assert next(x for x in evidence['comparisons'] if x['field']=='address')['status']=='different'
    assert evidence['student_name']=='student' and evidence['caller_text']=='Учебное сообщение'
    assert client.get(f"/api/reports/{run['run_id']}",headers=admin).status_code==200
    own=client.get(f"/api/reports/{run['run_id']}",headers=student).json()
    assert 'expected' not in own and 'caller_text' not in own and 'comparisons' not in own
    from app import main
    with main.SessionLocal() as s:
        other=User(username='second_teacher',password_hash=main.pwd.hash('other12345'),role='teacher');s.add(other);s.commit()
        other_token=main.token_for(other)
        # Editing after the attempt must not rewrite its reference values.
        saved=s.get(main.Scenario,scenario);saved.expected={**saved.expected,'address':'Другой адрес'};s.commit()
    assert client.get(f"/api/reports/{run['run_id']}",headers={'Authorization':'Bearer '+other_token}).status_code==403
    assert client.get(f"/api/reports/{run['run_id']}",headers=teacher).json()['expected']['address']=='Москва дом 1'


def test_registered_postprocessing_excludes_skipped_interval():
    from types import SimpleNamespace
    start=now();run=SimpleNamespace(finished_at=start+timedelta(seconds=100))
    context=SimpleNamespace(registered_at=start)
    events=[SimpleNamespace(kind='card.skip',at=start+timedelta(seconds=10),id=1),SimpleNamespace(kind='card.resume',at=start+timedelta(seconds=90),id=2)]
    assert postprocessing_seconds(run,context,events)==20


def test_recordings_are_per_call_and_legacy_is_not_attributed_to_new_call(client,monkeypatch,tmp_path):
    student,teacher=auth(client,'student'),auth(client,'teacher');scenario=create_scenario(client,teacher,assigned=True)
    run=client.post(f'/api/runs/{scenario}/start',headers=student).json();identifier=run['run_id'];monkeypatch.setenv('CALL_MEDIA_ROOT',str(tmp_path))
    from app import main
    with main.SessionLocal() as s:
        a=VoipCall(run_id=identifier,state='ended',sound_key='a',answered_at=now(),ended_at=now())
        b=VoipCall(run_id=identifier,state='ended',sound_key='b',answered_at=now(),ended_at=now())
        s.add_all([a,b]);s.flush();s.add(CardEvent(run_id=identifier,user_id=3,kind='sip.queued',data={'call_id':b.id,'recording_per_call':True}));s.commit();a_id,b_id=a.id,b.id
    (tmp_path/f'recording-{identifier}.wav').write_bytes(b'legacy audio')
    assert client.get(f'/api/telephony/runs/{identifier}/recording?call_id={a_id}',headers=teacher).content==b'legacy audio'
    assert client.get(f'/api/telephony/runs/{identifier}/recording?call_id={b_id}',headers=teacher).status_code==404
    (tmp_path/f'recording-{identifier}-{b_id}.wav').write_bytes(b'new audio')
    assert client.get(f'/api/telephony/runs/{identifier}/recording?call_id={b_id}',headers=teacher).content==b'new audio'
    assert client.get(f'/api/telephony/runs/{identifier}/recording',headers=teacher).content==b'new audio'
    detail=client.get(f'/api/reports/{identifier}',headers=teacher).json()
    assert all(c['recording_available'] for c in detail['calls'])
    assert client.get(f'/api/telephony/runs/{identifier}/recording?call_id=999999',headers=student).status_code==404
    assert len(client.get(f'/api/runs/{identifier}/attachments',headers=student).json())==2


def test_report_period_filters_match_csv_and_keep_student_permissions(client):
    import csv,io
    from app import main
    student,teacher=auth(client,'student'),auth(client,'teacher')
    scenario=create_scenario(client,teacher,assigned=True)
    identifier=client.post(f'/api/runs/{scenario}/start',headers=student).json()['run_id']
    from datetime import datetime,timezone
    with main.SessionLocal() as session:
        run=session.get(SessionRun,identifier)
        run.started_at=datetime(2026,3,31,21,0,tzinfo=timezone.utc)
        session.commit()
    params={'started_from':'2026-03-31T21:00:00Z','started_before':'2026-04-01T21:00:00Z','student_id':3}
    rows=client.get('/api/reports',headers=teacher,params=params)
    assert rows.status_code==200 and [r['id'] for r in rows.json()]==[identifier]
    offset={**params,'started_from':'2026-04-01T00:00:00+03:00','started_before':'2026-04-02T00:00:00+03:00'}
    assert [r['id'] for r in client.get('/api/reports',headers=teacher,params=offset).json()]==[identifier]
    exported=client.get('/api/reports/export.csv',headers=teacher,params=params)
    parsed=list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig')),delimiter=';'))
    assert len(parsed)==2 and parsed[1][0]==str(identifier)
    outside={**params,'started_from':'2026-03-30T21:00:00Z','started_before':'2026-03-31T21:00:00Z'}
    assert client.get('/api/reports',headers=teacher,params=outside).json()==[]
    assert len(list(csv.reader(io.StringIO(client.get('/api/reports/export.csv',headers=teacher,params=outside).content.decode('utf-8-sig')),delimiter=';')))==1
    assert client.get('/api/reports',headers=student,params={**params,'student_id':1}).json()==[]
    assert len(list(csv.reader(io.StringIO(client.get('/api/reports/export.csv',headers=student,params={**params,'student_id':1}).content.decode('utf-8-sig')),delimiter=';')))==1
    invalid={**params,'started_from':params['started_before']}
    assert client.get('/api/reports',headers=teacher,params=invalid).status_code==422
    assert client.get('/api/reports/export.csv',headers=teacher,params=invalid).status_code==422
