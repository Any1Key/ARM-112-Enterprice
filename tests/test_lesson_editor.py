from test_health import client,auth
from test_workflows import create_scenario


def test_prepared_lesson_edit_is_owned_validated_and_locked_after_start(client):
    teacher=auth(client,'teacher');student=auth(client,'student');admin=auth(client,'admin')
    first=create_scenario(client,teacher);second=create_scenario(client,teacher)
    from app.main import SessionLocal
    from app.models import User
    from sqlalchemy import select
    with SessionLocal() as session:student_id=session.scalar(select(User.id).where(User.username=='student'))
    payload={'title':'Первое занятие','scenario_ids':[first],'student_ids':[student_id],'mode':'call'}
    lesson=client.post('/api/lessons',headers=teacher,json=payload).json()['id'];path=f'/api/lessons/{lesson}'
    changed={**payload,'title':'  Новое название  ','scenario_ids':[second,second],'student_ids':[student_id,student_id],'service_code':'102'}
    assert client.put(path,headers=student,json=changed).status_code==403
    assert client.put(path,headers=admin,json=changed).status_code==403
    assert client.put(path,headers=teacher,json={**changed,'student_ids':[]}).status_code==422
    assert client.put(path,headers=teacher,json={**changed,'title':'   '}).status_code==422
    assert client.put(path,headers=teacher,json={**changed,'scenario_ids':[999999]}).status_code==403
    assert client.put(path,headers=teacher,json=changed).status_code==200
    row=client.get('/api/lessons',headers=teacher).json()[0]
    assert row['title']=='Новое название' and row['scenario_ids']==[second] and row['student_ids']==[student_id] and row['service_code']=='TERRITORY'
    assert client.post(path+'/start',headers=teacher).status_code==200
    assert client.put(path,headers=teacher,json=payload).status_code==409
    assert client.get('/api/lessons',headers=student).json()[0]['scenario_ids']==[second]
    assert client.put('/api/lessons/999999',headers=teacher,json=payload).status_code==404
