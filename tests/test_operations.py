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


def test_cpu_percentage_uses_elapsed_time_and_supports_multiple_cores():
    from app.operations import ProcessCpuMeter,process_metrics
    meter=ProcessCpuMeter()
    meter.sample(10,2)
    assert meter.percent is None
    meter.sample(12,3)
    assert meter.percent==50
    meter.sample(13,5)
    assert meter.percent==200
    meter.sample(13,5)
    assert meter.percent==200
    metrics=process_metrics()
    assert metrics['process_ram_mb']>0
    assert 'process_max_rss_kb' not in metrics
    assert 'process_cpu_seconds' not in metrics


def test_backup_history_download_and_path_protection(client,tmp_path,monkeypatch):
    monkeypatch.setenv('BACKUP_ROOT',str(tmp_path))
    bundle='arm112_20260917T185310Z_683'
    folder=tmp_path/bundle
    folder.mkdir()
    for name in ('database.dump','media.tar.gz','runtime.tar.gz','SHA256SUMS'):
        (folder/name).write_bytes(b'backup-content')
    (folder/'manifest.json').write_text(json.dumps({'created_at':'2026-09-17T18:53:10Z'}))
    (tmp_path/'arm112_20260916T154127Z.dump').write_bytes(b'legacy-dump')
    (tmp_path/'latest.dump').write_bytes(b'duplicate')
    (tmp_path/'.partial_unfinished').mkdir()
    (tmp_path/'arm112_secret.dump').symlink_to(tmp_path/'latest.dump')
    student=auth(client,'student');admin=auth(client,'admin')
    assert client.get('/api/operations/backups',headers=student).status_code==403
    assert client.get(f'/api/operations/backups/{bundle}/database',headers=student).status_code==403
    items=client.get('/api/operations/backups',headers=admin).json()
    assert len(items)==2
    full=next(item for item in items if item['id']==bundle)
    assert full['created_at']=='2026-09-17T18:53:10+00:00'
    assert full['scope']==['database','media','runtime']
    downloaded=client.get(f'/api/operations/backups/{bundle}/database',headers=admin)
    assert downloaded.content==b'backup-content'
    assert downloaded.headers['cache-control']=='no-store'
    assert bundle+'.dump' in downloaded.headers['content-disposition']
    assert client.get('/api/operations/backups/latest.dump/database',headers=admin).status_code==404
    assert client.get('/api/operations/backups/arm112_secret.dump/database',headers=admin).status_code==404
    (folder/'runtime.tar.gz').unlink()
    assert client.get(f'/api/operations/backups/{bundle}/database',headers=admin).status_code==404
    events=client.get('/api/audit',headers=admin).json()
    assert len([event for event in events if event['action']=='backup.download'])==1
