"""Admin-only live component health and backup freshness."""
import os,time,resource
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
import httpx,redis
from fastapi import Depends,HTTPException
from sqlalchemy import select,func
from app.models import User,SessionRun,Audit
from app.telephony import ami_action
started=time.monotonic()
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
        backup=Path('/backups/latest.dump');age=time.time()-backup.stat().st_mtime if backup.exists() else None
        components['postgresql']={'status':'ok'};components['backup']={'status':'missing' if age is None else 'stale' if age>90000 else 'ok','age_seconds':round(age) if age is not None else None,'size_bytes':backup.stat().st_size if backup.exists() else None}
        usage=resource.getrusage(resource.RUSAGE_SELF)
        return {'at':datetime.now(timezone.utc),'uptime_seconds':round(time.monotonic()-started),'process_cpu_seconds':round(usage.ru_utime+usage.ru_stime,2),'process_max_rss_kb':usage.ru_maxrss,'users':s.scalar(select(func.count(User.id))),'active_runs':s.scalar(select(func.count(SessionRun.id)).where(SessionRun.finished_at.is_(None))),'audit_events':s.scalar(select(func.count(Audit.id))),'components':components}
