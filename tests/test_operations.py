import json
import os
import time
from app.operations import backup_snapshot
from test_health import client, auth


def test_full_backup_and_stale_detection(tmp_path):
    bundle='arm112_20260917_1'
    target=tmp_path/bundle
    target.mkdir()
    for name in ('database.dump','media.tar.gz','runtime.tar.gz','SHA256SUMS','manifest.json'):
        (target/name).write_text('data')
    (tmp_path/'latest.dump').write_text('dump')
    (tmp_path/'latest.json').write_text(json.dumps({'bundle':bundle,'size_bytes':12345}))
    (tmp_path/'.scheduler-heartbeat').touch()
    snapshot=backup_snapshot(tmp_path)
    assert snapshot['status']=='ok'
    assert snapshot['size_bytes']==12345
    assert snapshot['scope']==['database','media','runtime']
    assert snapshot['scheduler_alive']
    old=time.time()-86401
    os.utime(tmp_path/'latest.dump',(old,old))
    assert backup_snapshot(tmp_path)['status']=='stale'
    (target/'media.tar.gz').unlink()
    (tmp_path/'latest.dump').touch()
    assert backup_snapshot(tmp_path)['status']=='incomplete'


def test_backup_queue_access_and_availability(client,tmp_path,monkeypatch):
    monkeypatch.setenv('BACKUP_ROOT',str(tmp_path))
    student=auth(client,'student')
    admin=auth(client,'admin')
    assert client.post('/api/operations/backup',headers=student).status_code==403
    assert client.post('/api/operations/backup',headers=admin).status_code==503
    (tmp_path/'.scheduler-heartbeat').touch()
    assert client.post('/api/operations/backup',headers=admin).status_code==202
    assert (tmp_path/'.backup-pending').is_dir()
    assert client.post('/api/operations/backup',headers=admin).status_code==409
    events=client.get('/api/audit',headers=admin).json()
    assert len([event for event in events if event['action']=='backup.request'])==1
