from test_health import client,auth
from test_workflows import create_scenario


def test_student_needs_assignment_even_before_first_lesson(client):
    teacher=auth(client,'teacher');student=auth(client,'student')
    scenario=create_scenario(client,teacher)
    assert client.get('/api/scenarios',headers=student).json()==[]
    assert client.post(f'/api/runs/{scenario}/start',headers=student).status_code==403
    assert client.post(f'/api/telephony/scenarios/{scenario}/prepare',headers=student).status_code==403
    assert client.get('/api/materials',headers=student).status_code==403
    assert client.get('/api/materials/source/tickets',headers=student).status_code==403
    lesson=client.post('/api/lessons',headers=teacher,json={'title':'Назначено','scenario_ids':[scenario],'student_ids':[3]}).json()['id']
    assert [x['id'] for x in client.get('/api/scenarios',headers=student).json()]==[scenario]
    assert client.post(f'/api/runs/{scenario}/start',headers=student).status_code==403
    client.post(f'/api/lessons/{lesson}/start',headers=teacher)
    assert client.post(f'/api/runs/{scenario}/start',headers=student).status_code==200


def test_templates_are_private_editable_and_assignment_is_independent(client):
    teacher=auth(client,'teacher');student=auth(client,'student')
    first=create_scenario(client,teacher);second=create_scenario(client,teacher)
    body={'title':'Заготовка','scenario_ids':[first],'student_ids':[]}
    created=client.post('/api/lesson-templates',headers=teacher,json=body)
    assert created.status_code==200,created.text
    template=created.json()['id'];path=f'/api/lesson-templates/{template}'
    assert client.get('/api/lesson-templates',headers=student).status_code==403
    assert client.get('/api/lessons',headers=student).json()==[]
    assert client.get('/api/scenarios',headers=student).json()==[]
    assert client.put(path,headers=student,json=body).status_code==403
    admin=auth(client,'admin')
    created_teacher=client.post('/api/users',headers=admin,json={'username':'other_teacher','password':'other_teacher12345','role':'teacher'})
    assert created_teacher.status_code==200
    other_teacher=auth(client,'other_teacher')
    assert client.get('/api/lesson-templates',headers=other_teacher).json()==[]
    assert client.put(path,headers=other_teacher,json=body).status_code==403
    assert client.post(path+'/assign',headers=other_teacher,json={'student_ids':[3]}).status_code==403
    assert client.put(path,headers=teacher,json={**body,'student_ids':[3]}).status_code==200
    assert client.get('/api/lessons',headers=student).json()==[]
    assert client.get('/api/scenarios',headers=student).json()==[]
    assert client.post(path+'/assign',headers=teacher,json={'student_ids':[]}).status_code==422
    assigned=client.post(path+'/assign',headers=teacher,json={'student_ids':[3]})
    assert assigned.status_code==200,assigned.text
    lesson=assigned.json()['id'];assert lesson!=template
    client.post(f'/api/lessons/{lesson}/start',headers=teacher)
    run=client.post(f'/api/runs/{first}/start',headers=student)
    assert run.status_code==200,run.text
    assert client.put(path,headers=teacher,json={**body,'title':'Новая заготовка','scenario_ids':[second]}).status_code==200
    actual=client.get('/api/lessons',headers=student).json()[0]
    assert actual['scenario_ids']==[first] and actual['title']=='Заготовка'
    assert [x['id'] for x in client.get('/api/scenarios',headers=student).json()]==[first]
    assert client.post(f'/api/runs/{second}/start',headers=student).status_code==403
    copied=client.post(path+'/assign',headers=teacher,json={'student_ids':[3],'title':'Следующее занятие'})
    assert copied.status_code==200 and copied.json()['id']!=lesson


def test_unassigned_legacy_open_run_is_archived_without_grade(client):
    from app.main import SessionLocal
    from app.models import SessionRun,RunContext,Scenario
    from sqlalchemy import select
    student=auth(client,'student')
    with SessionLocal() as session:
        scenario=session.scalar(select(Scenario))
        run=SessionRun(scenario_id=scenario.id,student_id=3,answers={'description':'Сохранённый черновик'})
        session.add(run);session.flush();session.add(RunContext(run_id=run.id,scenario_snapshot={'expected':scenario.expected}));session.commit();identifier=run.id
    assert client.get('/api/active-run',headers=student).json() is None
    with SessionLocal() as session:
        run=session.get(SessionRun,identifier)
        assert run.finished_at and run.score is None and run.report['assignment_removed']
        assert run.answers['description']=='Сохранённый черновик'
