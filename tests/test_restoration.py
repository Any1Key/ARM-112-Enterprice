"""Restore authorization, import validation, maintenance and session invalidation."""
import json
import sys
import time
from pathlib import Path

import pytest
from test_health import client,auth

@pytest.fixture
def restore_root(tmp_path,monkeypatch):
    directory=tmp_path/'backups';directory.mkdir()
    monkeypatch.setenv('BACKUP_ROOT',str(directory))
    media=tmp_path/'media';media.mkdir()
    monkeypatch.setenv('CALL_MEDIA_ROOT',str(media))
    provision=tmp_path/'provision';provision.mkdir()
    monkeypatch.setenv('SIP_PROVISION_ROOT',str(provision))
    (directory/'.scheduler-heartbeat').touch()
    return directory

def upload(client,headers,data=b'PGDMP-test-archive'):
    return client.post('/api/operations/restore/uploads',headers={**headers,'Content-Type':'application/octet-stream'},content=data)

def payload(upload_id,**overrides):
    return {'upload_id':upload_id,'password':'admin12345','confirmation':'ВОССТАНОВИТЬ','trusted_source':True,**overrides}

def test_upload_rejects_nonadmin_invalid_and_oversized_files(restore_root,client):
    student=auth(client,'student');admin=auth(client,'admin')
    assert upload(client,student).status_code==403
    assert upload(client,admin,b'DROP DATABASE arm112;').status_code==422
    assert not list((restore_root/'uploads').iterdir())
    response=client.post('/api/operations/restore/uploads',headers={**admin,'Content-Length':str(101*1024*1024)},content=b'')
    assert response.status_code==413
    valid=upload(client,admin).json()
    saved=restore_root/'uploads'/valid['upload_id']/'database.dump'
    assert saved.read_bytes()==b'PGDMP-test-archive'
    assert saved.stat().st_mode&0o077==0


def test_restore_confirmation_maintenance_and_private_status(restore_root,client):
    admin=auth(client,'admin');student=auth(client,'student')
    item=upload(client,admin).json()
    path='/api/operations/restore/jobs'
    assert client.post(path,headers=student,json=payload(item['upload_id'])).status_code==403
    assert client.post(path,headers=admin,json=payload(item['upload_id'],password='wrong')).status_code==403
    assert client.post(path,headers=admin,json=payload(item['upload_id'],confirmation='ok')).status_code==422
    assert client.post(path,headers=admin,json=payload(item['upload_id'],trusted_source=False)).status_code==422
    assert client.post(path,headers=admin,json=payload('../latest.dump')).status_code==404
    response=client.post(path,headers=admin,json=payload(item['upload_id']))
    assert response.status_code==202
    job=response.json()
    assert (restore_root/'.restore-maintenance').read_text()==job['job_id']
    assert (restore_root/'.restore-inflight').read_text()=='0'
    import os
    assert (Path(os.environ['SIP_PROVISION_ROOT'])/'maintenance').exists()
    assert client.get('/api/scenarios',headers=student).status_code==503
    assert client.post('/api/operations/backup',headers=admin).status_code==503
    status_path=path+'/'+job['job_id']
    assert client.get(status_path,headers=admin).status_code==403
    status=client.get(status_path,headers={'X-Restore-Token':job['status_token']})
    assert status.status_code==200 and status.json()['status']=='queued'
    assert client.get('/health').status_code==200


def test_expired_import_and_backup_in_progress_are_rejected(restore_root,client):
    admin=auth(client,'admin');item=upload(client,admin).json()
    metadata_path=restore_root/'uploads'/item['upload_id']/'metadata.json'
    metadata=json.loads(metadata_path.read_text());metadata['uploaded_at']=time.time()-86401;metadata_path.write_text(json.dumps(metadata))
    path='/api/operations/restore/jobs'
    assert client.post(path,headers=admin,json=payload(item['upload_id'])).status_code==404
    expired_directory=metadata_path.parent
    item=upload(client,admin).json()
    assert not expired_directory.exists()
    (restore_root/'.backup-running').touch()
    assert client.post(path,headers=admin,json=payload(item['upload_id'])).status_code==409
    assert not (restore_root/'.restore-maintenance').exists()


def test_finalization_invalidates_sessions_and_records_restore(restore_root,client,monkeypatch):
    import app.telephony as telephony
    actions=[]
    monkeypatch.setattr(telephony,'write_accounts',lambda session:actions.append('write-accounts'))
    monkeypatch.setattr(telephony,'ami_action',lambda action,**kwargs:actions.append(kwargs.get('Command',action)))
    admin=auth(client,'admin');item=upload(client,admin).json()
    job=client.post('/api/operations/restore/jobs',headers=admin,json=payload(item['upload_id'])).json()
    status_path=restore_root/'restore_jobs'/job['job_id']/'status.json'
    status=json.loads(status_path.read_text());status['status']='finalizing';status['safety_backup']='arm112_safety';status_path.write_text(json.dumps(status))
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        result=client.get('/api/operations/restore/jobs/'+job['job_id'],headers={'X-Restore-Token':job['status_token']}).json()
        if result['status']=='succeeded':break
        time.sleep(.1)
    assert result['status']=='succeeded'
    assert not (restore_root/'.restore-maintenance').exists()
    import os
    assert not (Path(os.environ['SIP_PROVISION_ROOT'])/'maintenance').exists()
    assert (restore_root/'.backup-pending').exists()
    assert client.get('/api/users',headers=admin).status_code==401
    fresh=auth(client,'admin')
    assert client.get('/api/users',headers=fresh).status_code==200
    events=client.get('/api/audit',headers=fresh).json()
    assert len([event for event in events if event['action']=='database.restore.completed'])==1
    assert actions==['write-accounts','core restart now']
    (restore_root/'.backup-pending').rmdir()
    retry=client.post('/api/operations/restore/jobs',headers=fresh,json=payload(item['upload_id']))
    assert retry.status_code==409 and 'Предыдущее' in retry.json()['detail']


def test_active_calls_and_generation_prevent_restore(restore_root,client):
    admin=auth(client,'admin');student=auth(client,'student')
    item=upload(client,admin).json()
    scenario=client.get('/api/scenarios',headers=student).json()[0]
    run=client.post('/api/runs/'+str(scenario['id'])+'/start',headers=student).json()
    module=sys.modules['app.main']
    with module.SessionLocal() as session:
        call=module.VoipCall(run_id=run['run_id'],state='queued',sound_key='pending')
        session.add(call);session.commit();call_id=call.id
    path='/api/operations/restore/jobs'
    response=client.post(path,headers=admin,json=payload(item['upload_id']))
    assert response.status_code==409 and 'звонки' in response.json()['detail']
    assert not (restore_root/'.restore-maintenance').exists()
    with module.SessionLocal() as session:
        session.get(module.VoipCall,call_id).state='failed'
        teacher=session.query(module.User).filter_by(username='teacher').one()
        session.add(module.AiJob(teacher_id=teacher.id,status='queued',input={}));session.commit()
    response=client.post(path,headers=admin,json=payload(item['upload_id']))
    assert response.status_code==409 and 'генерации' in response.json()['detail']
    assert not (restore_root/'.restore-maintenance').exists()


def test_import_quarantines_old_recordings_to_prevent_call_id_collisions(tmp_path,monkeypatch):
    from app.restoration import quarantine_imported_recordings
    media=tmp_path/'media';media.mkdir()
    monkeypatch.setenv('CALL_MEDIA_ROOT',str(media))
    (media/'recording-1-2.wav').write_bytes(b'old server call')
    (media/'recording-1.wav').write_bytes(b'legacy call')
    (media/'speech-cache.wav').write_bytes(b'cached speech')
    location=Path(quarantine_imported_recordings('f'*32))
    assert not (media/'recording-1-2.wav').exists()
    assert (location/'recording-1-2.wav').read_bytes()==b'old server call'
    assert (location/'recording-1.wav').exists()
    assert (media/'speech-cache.wav').exists()
    assert quarantine_imported_recordings('f'*32)==str(location)
