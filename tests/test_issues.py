from datetime import timedelta
from sqlalchemy import select
from test_health import client,auth


def test_issue_review_visibility_validation_and_history(client):
    admin=auth(client,'admin');student=auth(client,'student');teacher=auth(client,'teacher')
    issue=client.post('/api/issues',headers=student,data={'description':'Проблема со звуком'}).json()['id']
    other=client.post('/api/issues',headers=teacher,data={'description':'Другая проблема'}).json()['id']
    assert client.get('/api/issues',headers=student).status_code==403
    assert [row['id'] for row in client.get('/api/issues/mine',headers=student).json()]==[issue]
    row=next(x for x in client.get('/api/issues',headers=admin).json() if x['id']==issue)
    assert row['status']=='new' and row['comment']=='' and row['username']=='student'
    path=f'/api/issues/{issue}'
    assert client.put(path,headers=student,json={'status':'done','comment':'Подмена'}).status_code==403
    assert client.put(path,headers=teacher,json={'status':'done'}).status_code==403
    assert client.put(path,headers=admin,json={'status':'unknown'}).status_code==422
    assert client.put(path,headers=admin,json={'status':'done','comment':'x'*5001}).status_code==422
    assert client.put('/api/issues/999999',headers=admin,json={'status':'done'}).status_code==404
    for status in ['done','cancelled','false','new']:
        comment='  Проверено: '+status+'  '
        assert client.put(path,headers=admin,json={'status':status,'comment':comment}).status_code==200
        row=client.get('/api/issues/mine',headers=student).json()[0]
        assert row['status']==status and row['comment']==comment.strip()
        assert row['updated_by']=='admin' and row['updated_at']
    assert client.get('/api/issues/mine',headers=teacher).json()[0]['id']==other
    from app.main import SessionLocal
    from app.models import Audit
    with SessionLocal() as session:
        reviews=list(session.scalars(select(Audit).where(Audit.action=='issue.review')))
        assert [event.details['status'] for event in reviews]==['done','cancelled','false','new']


def test_review_state_survives_audit_retention(client):
    from app.main import SessionLocal,cleanup_expired
    from app.models import Audit,utcnow
    admin=auth(client,'admin');student=auth(client,'student')
    issue=client.post('/api/issues',headers=student,data={'description':'Старая заявка'}).json()['id']
    client.put(f'/api/issues/{issue}',headers=admin,json={'status':'done','comment':'Исправлено'})
    with SessionLocal() as session:
        review=session.scalar(select(Audit).where(Audit.action=='issue.review'))
        review.at=utcnow()-timedelta(days=200)
        session.add(Audit(action='ordinary.old',at=utcnow()-timedelta(days=200)));session.commit()
    cleanup_expired()
    assert client.get('/api/issues/mine',headers=student).json()[0]['status']=='done'
    with SessionLocal() as session:assert not session.scalar(select(Audit).where(Audit.action=='ordinary.old'))
