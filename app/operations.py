"""Admin-only live component health and backup freshness."""
import os,time,resource,json
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
import httpx,redis
from fastapi import Depends,HTTPException
from sqlalchemy import select,func
from app.models import User,SessionRun,Audit
from app.telephony import ami_action
started=time.monotonic()
def backup_snapshot(root=None):
    root=Path(root or os.getenv('BACKUP_ROOT','/backups'))
    def read_json(name):
        try:return json.loads((root/name).read_text())
        except (OSError,ValueError):return {}
    def age(name):
        try:return max(0,round(time.time()-(root/name).stat().st_mtime))
        except OSError:return None
    meta=read_json('latest.json')
    bundle=meta.get('bundle','')
    valid=isinstance(bundle,str) and bundle.startswith('arm112_') and all(c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' for c in bundle)
    full=valid and all((root/bundle/name).is_file() for name in ('database.dump','media.tar.gz','runtime.tar.gz','SHA256SUMS','manifest.json'))
    elapsed=age('latest.dump')
    running=(root/'.backup-running').exists()
    queued=(root/'.backup-pending').is_dir()
    heartbeat=age('.scheduler-heartbeat')
    try:size=meta.get('size_bytes') if full else (root/'latest.dump').stat().st_size
    except OSError:size=None
    return {'status':'missing' if elapsed is None else 'stale' if elapsed>=86400 else 'incomplete' if not full else 'ok',
            'age_seconds':elapsed,'size_bytes':size,'scope':['database','media','runtime'] if full else ['database'],
            'created_at':meta.get('created_at'),'running':running,'queued':queued,
            'scheduler_alive':running or (heartbeat is not None and heartbeat<30),
            'last_failed':(root/'last_error').exists(),'restore_check':read_json('last_restore_check.json'),
            'bundle':bundle if valid else None,'interval_hours':int(os.getenv('BACKUP_INTERVAL_HOURS','23'))}

def register_operations(app,db,current):
    @app.get('/api/operations')
    def operations(u=Depends(current),s=Depends(db)):
        if u.role!='admin':raise HTTPException(403)
        def probe(name):
            try:
                if name=='redis':redis.Redis.from_url(os.getenv('REDIS_URL','redis://redis:6379'),socket_connect_timeout=1,socket_timeout=1).ping()
                elif name=='asterisk':ami_action('Ping')
                else:
                    urls={'ml':os.getenv('ML_URL','http://ml:8091')+'/health','voice':os.getenv('VOICE_URL','http://voice:8092')+'/health','ollama':os.getenv('OLLAMA_URL','http://ollama:11434')+'/api/tags','grammar':os.getenv('GRAMMAR_URL','http://grammar:8093')+'/v2/check?language=ru-RU&text=Тест'}
                    with httpx.Client(timeout=1,trust_env=False) as c:c.get(urls[name]).raise_for_status()
                return name,{'status':'ok'}
            except Exception as exc:return name,{'status':'unavailable','reason':type(exc).__name__}
        with ThreadPoolExecutor(max_workers=6) as executor:components=dict(executor.map(probe,['redis','asterisk','ml','voice','grammar','ollama']))
        components['postgresql']={'status':'ok'};components['backup']=backup_snapshot()
        usage=resource.getrusage(resource.RUSAGE_SELF)
        return {'at':datetime.now(timezone.utc),'uptime_seconds':round(time.monotonic()-started),'process_cpu_seconds':round(usage.ru_utime+usage.ru_stime,2),'process_max_rss_kb':usage.ru_maxrss,'users':s.scalar(select(func.count(User.id))),'active_runs':s.scalar(select(func.count(SessionRun.id)).where(SessionRun.finished_at.is_(None))),'audit_events':s.scalar(select(func.count(Audit.id))),'components':components}

    @app.post('/api/operations/backup',status_code=202)
    def request_backup(u=Depends(current),s=Depends(db)):
        if u.role!='admin':raise HTTPException(403)
        snapshot=backup_snapshot()
        if snapshot['running'] or snapshot['queued']:raise HTTPException(409,'Резервная копия уже создаётся или ожидает запуска')
        if not snapshot['scheduler_alive']:raise HTTPException(503,'Сервис резервирования недоступен. Проверьте контейнер backup')
        root=Path(os.getenv('BACKUP_ROOT','/backups'))
        try:(root/'.backup-pending').mkdir(mode=0o700)
        except FileExistsError:raise HTTPException(409,'Резервная копия уже ожидает запуска')
        s.add(Audit(user_id=u.id,action='backup.request',details={'scope':['database','media','runtime']}))
        s.commit()
        return {'status':'queued'}
