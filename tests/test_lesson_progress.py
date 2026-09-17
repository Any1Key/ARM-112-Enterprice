from datetime import datetime, timedelta, timezone
from test_health import client, auth
from test_workflows import create_scenario


def lesson(client, teacher, scenarios, title='Прогресс занятия'):
    response=client.post('/api/lessons',headers=teacher,json={'title':title,'scenario_ids':scenarios,'student_ids':[3],'mode':'call','service_code':'112'})
    assert response.status_code==200,response.text
    identifier=response.json()['id']
    assert client.post(f'/api/lessons/{identifier}/start',headers=teacher).status_code==200
    return identifier


def test_progress_separates_skips_and_resumes_them_after_new_tasks(client,monkeypatch):
    from app import main,workflows
    from app.models import SessionRun
    student,teacher=auth(client,'student'),auth(client,'teacher')
    scenarios=[create_scenario(client,teacher) for _ in range(3)]
    identifier=lesson(client,teacher,scenarios)
    progress=lambda:next(item for item in client.get('/api/lessons',headers=student).json() if item['id']==identifier)
    assert progress()['counts']=={'total':3,'completed':0,'pending':3,'in_progress':0,'skipped':0}
    assert 'expected' not in str(progress()['tasks'])
    first=client.post(f'/api/lessons/{identifier}/tasks/{scenarios[0]}/start',headers=student).json()
    assert first['lesson_id']==identifier and progress()['counts']['in_progress']==1
    origin=datetime.now(timezone.utc)
    with main.SessionLocal() as s:
        s.get(SessionRun,first['run_id']).started_at=origin;s.commit()
    clock=[origin+timedelta(seconds=42)];monkeypatch.setattr(workflows,'now',lambda:clock[0])
    assert client.put(f"/api/runs/{first['run_id']}/draft",headers=student,json={'revision':0,'card':{'description':'Сохранённые сведения'}}).status_code==200
    skipped=client.post(f"/api/runs/{first['run_id']}/skip",headers=student).json()
    assert skipped['elapsed_seconds']==42 and progress()['counts']['skipped']==1
    second=client.post(f'/api/lessons/{identifier}/tasks/{scenarios[1]}/start',headers=student).json()
    assert client.post(f"/api/runs/{second['run_id']}/finish",headers=student,json={}).status_code==200
    assert progress()['counts']=={'total':3,'completed':1,'pending':1,'in_progress':0,'skipped':1}
    third=client.post(f'/api/lessons/{identifier}/next',headers=student).json()
    assert third['scenario_id']==scenarios[2], 'New tasks precede previously skipped tasks'
    third_skip=client.post(f"/api/runs/{third['run_id']}/skip",headers=student).json()
    assert not progress()['all_completed']
    clock[0]+=timedelta(hours=2)
    resumed=client.post(f'/api/lessons/{identifier}/next',headers=student).json()
    expected={first['run_id']:42,third['run_id']:third_skip['elapsed_seconds']}
    assert not resumed.get('done') and resumed['run_id'] in expected
    assert resumed['elapsed_seconds']==expected[resumed['run_id']]
    assert client.post(f"/api/runs/{resumed['run_id']}/finish",headers=student,json={}).status_code==200
    remaining=next(task for task in progress()['tasks'] if task['status']=='skipped')
    reopened=client.post(f"/api/lessons/{identifier}/tasks/{remaining['scenario_id']}/start",headers=student).json()
    assert reopened['run_id']==remaining['run_id']
    if reopened['run_id']==first['run_id']:assert reopened['card']['description']=='Сохранённые сведения'
    assert client.post(f"/api/runs/{reopened['run_id']}/finish",headers=student,json={}).status_code==200
    assert progress()['all_completed'] and progress()['counts']['completed']==3
    done=client.post(f'/api/lessons/{identifier}/next',headers=student).json()
    assert done['done'] and done['all_completed']
    assert client.get('/api/active-run',headers=student).json() is None
    assert client.post(f'/api/lessons/{identifier}/tasks/{scenarios[0]}/start',headers=student).status_code==409


def test_other_lesson_cannot_report_done_or_open_a_second_active_card(client):
    from app import main
    from app.models import SessionRun
    student,teacher,admin=auth(client,'student'),auth(client,'teacher'),auth(client,'admin')
    scenarios=[create_scenario(client,teacher) for _ in range(3)]
    first=lesson(client,teacher,[scenarios[0]],'Первое занятие')
    second=lesson(client,teacher,[scenarios[1]],'Второе занятие')
    run=client.post(f'/api/lessons/{first}/next',headers=student).json()
    assert client.post(f'/api/lessons/{second}/next',headers=student).status_code==409
    assert client.post(f'/api/lessons/{second}/tasks/{scenarios[1]}/start',headers=student).status_code==409
    with main.SessionLocal() as s:assert s.query(SessionRun).filter_by(student_id=3,finished_at=None).count()==1
    assert client.post(f'/api/lessons/{first}/tasks/{scenarios[0]}/start',headers=student).json()['run_id']==run['run_id']
    assert client.post(f'/api/lessons/{first}/tasks/{scenarios[2]}/start',headers=student).status_code==403
    assert client.post(f'/api/lessons/{first}/tasks/{scenarios[0]}/start',headers=teacher).status_code==403
    new=client.post('/api/users',headers=admin,json={'username':'other_student','password':'Other-password-12345','role':'student'})
    assert new.status_code==200
    other={'Authorization':'Bearer '+client.post('/api/login',json={'username':'other_student','password':'Other-password-12345'}).json()['token']}
    assert client.get('/api/lessons',headers=other).json()==[]
    assert client.post(f'/api/lessons/{first}/tasks/{scenarios[0]}/start',headers=other).status_code==403
    assert client.post(f"/api/runs/{run['run_id']}/finish",headers=student,json={}).status_code==200
    assert client.post(f'/api/lessons/{second}/next',headers=student).status_code==200
