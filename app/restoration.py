"""Administrator-only queued database restoration and maintenance gate."""
import hashlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from threading import Lock,Thread,Event

from fastapi import Depends,HTTPException,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from sqlalchemy import select

from app.models import Audit,User,VoipCall,AiJob,SipAccount

MAX_UPLOAD=100*1024*1024
JOB_ID=re.compile(r'[a-f0-9]{32}')

def root():return Path(os.getenv('BACKUP_ROOT','/backups'))

def atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False));temporary.chmod(0o600);temporary.replace(path)

def auth_epoch():
    try:return (root()/'.auth-epoch').read_text().strip()
    except OSError:return ''

def maintenance():return (root()/'.restore-maintenance').exists()

def sip_gate():return Path(os.getenv('SIP_PROVISION_ROOT','/provision'))/'maintenance'

def quarantine_imported_recordings(job_id):
    """Different servers reuse numeric call IDs; never attribute local WAVs to imports."""
    from app.recordings import media_root
    media=media_root();destination=media/'before_database_import'/job_id
    recordings=[path for path in media.glob('recording-*.wav') if path.is_file()]
    if recordings:
        destination.mkdir(parents=True,exist_ok=True)
        for path in recordings:path.replace(destination/path.name)
    return str(destination) if destination.exists() else None

class RestoreRequest(BaseModel):
    backup_id:str|None=Field(default=None,max_length=150)
    upload_id:str|None=Field(default=None,max_length=32)
    password:str=Field(min_length=1,max_length=200)
    confirmation:str
    trusted_source:bool=False

def register_restoration(app,db,current,factory,engine,verify_password,catalog):
    stop=Event()
    inflight=0
    guard=Lock()
    def write_inflight():
        if maintenance():(root()/'.restore-inflight').write_text(str(inflight))

    @app.middleware('http')
    async def maintenance_gate(request,call_next):
        nonlocal inflight
        api=request.url.path.startswith('/api/')
        status=request.url.path.startswith('/api/operations/restore/jobs/') and request.method=='GET'
        counted=api and not status
        with guard:
            if counted and maintenance():return JSONResponse({'detail':'Восстанавливается база данных. Дождитесь завершения и войдите заново.'},status_code=503)
            if counted:inflight+=1
        try:return await call_next(request)
        finally:
            if counted:
                with guard:
                    inflight-=1;write_inflight()

    @app.post('/api/operations/restore/uploads',status_code=201)
    async def upload_database(request:Request,u=Depends(current)):
        if u.role!='admin':raise HTTPException(403)
        limit=MAX_UPLOAD
        try:length=int(request.headers.get('content-length','0'))
        except ValueError:raise HTTPException(400)
        if length>limit:raise HTTPException(413,'Размер дампа превышает 100 МБ')
        uploads=root()/'uploads'
        if uploads.is_dir():
            for old in uploads.iterdir():
                if old.is_symlink() or not old.is_dir() or not JOB_ID.fullmatch(old.name):continue
                try:
                    metadata=json.loads((old/'metadata.json').read_text())
                    if time.time()-metadata.get('uploaded_at',time.time())<=86400:continue
                    for name in ('database.dump','metadata.json'):(old/name).unlink(missing_ok=True)
                    old.rmdir()
                except (OSError,ValueError,TypeError):continue
        upload_id=secrets.token_hex(16)
        directory=root()/'uploads'/upload_id;directory.mkdir(parents=True,mode=0o700)
        path=directory/'database.dump';size=0
        try:
            with path.open('xb') as stream:
                path.chmod(0o600)
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>limit:raise HTTPException(413,'Размер дампа превышает 100 МБ')
                    stream.write(chunk)
            with path.open('rb') as stream:magic=stream.read(5)
            if magic!=b'PGDMP':raise HTTPException(422,'Нужен PostgreSQL custom-format дамп (.dump), скачанный из этой системы. SQL и ZIP не подходят.')
            atomic_json(directory/'metadata.json',{'uploaded_at':time.time(),'size_bytes':size,'uploaded_by':u.id})
            return {'upload_id':upload_id,'size_bytes':size}
        except BaseException:
            path.unlink(missing_ok=True)
            if directory.exists():
                for child in directory.iterdir():child.unlink()
                directory.rmdir()
            raise

    @app.post('/api/operations/restore/jobs',status_code=202)
    def request_restore(payload:RestoreRequest,u=Depends(current),s=Depends(db)):
        if u.role!='admin':raise HTTPException(403)
        if not verify_password(payload.password,u.password_hash):raise HTTPException(403,'Неверный пароль администратора')
        if payload.confirmation!='ВОССТАНОВИТЬ' or not payload.trusted_source:raise HTTPException(422,'Подтвердите замену базы и доверенный источник дампа')
        if bool(payload.backup_id)==bool(payload.upload_id):raise HTTPException(422,'Выберите одну резервную копию или один загруженный дамп')
        from app.operations import backup_snapshot
        snapshot=backup_snapshot()
        if not snapshot['scheduler_alive']:raise HTTPException(503,'Сервис резервирования недоступен')
        if snapshot['running'] or snapshot['queued']:raise HTTPException(409,'Дождитесь завершения резервного копирования')
        if s.scalar(select(VoipCall.id).where(VoipCall.state.in_(['queued','ringing','answered'])).limit(1)):
            raise HTTPException(409,'Завершите активные звонки перед восстановлением базы')
        if s.scalar(select(AiJob.id).where(AiJob.status.in_(['queued','running'])).limit(1)):
            raise HTTPException(409,'Дождитесь завершения генерации сценариев')
        if payload.backup_id:
            selected=next((item for item in catalog() if item['id']==payload.backup_id and item['complete']),None)
            if selected is None:raise HTTPException(404,'Готовая резервная копия не найдена')
            source=payload.backup_id if payload.backup_id.endswith('.dump') else payload.backup_id+'/database.dump'
        else:
            if not JOB_ID.fullmatch(payload.upload_id):raise HTTPException(404)
            directory=root()/'uploads'/payload.upload_id
            try:metadata=json.loads((directory/'metadata.json').read_text())
            except (OSError,ValueError):raise HTTPException(404,'Загруженный дамп не найден')
            if metadata.get('uploaded_by')!=u.id or time.time()-metadata.get('uploaded_at',0)>86400:raise HTTPException(404,'Загрузите дамп заново')
            source='uploads/'+payload.upload_id+'/database.dump'
        path=root()/source
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root().resolve()):raise HTTPException(404)
        if (root()/'.restore-request').exists():raise HTTPException(409,'Предыдущее восстановление ещё завершается. Подождите несколько секунд.')
        lock=root()/'.restore-lock'
        try:lock.mkdir(mode=0o700)
        except FileExistsError:raise HTTPException(409,'Восстановление уже выполняется')
        job_id=secrets.token_hex(16);status_token=secrets.token_hex(32)
        directory=root()/'restore_jobs'/job_id
        try:
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            atomic_json(directory/'request.json',{'sha256':digest.hexdigest(),'job_id':job_id,'source':source,'requested_by':u.id,'requested_by_name':u.username,'status_token_hash':hashlib.sha256(status_token.encode()).hexdigest()})
            atomic_json(directory/'status.json',{'job_id':job_id,'status':'queued','message':'Ожидает запуска','updated_at':time.time()})
            s.add(Audit(user_id=u.id,action='database.restore.request',details={'job_id':job_id,'source':source}));s.commit()
            # Closing the gate and recording active requests use the same mutex.
            with guard:
                (root()/'.restore-maintenance').write_text(job_id);write_inflight()
            sip_gate().parent.mkdir(parents=True,exist_ok=True)
            sip_gate().write_text(job_id)
            atomic_json(root()/'.restore-request',{'job_id':job_id})
            return {'job_id':job_id,'status_token':status_token,'status':'queued'}
        except BaseException:
            (root()/'.restore-maintenance').unlink(missing_ok=True)
            sip_gate().unlink(missing_ok=True)
            lock.rmdir()
            raise

    @app.get('/api/operations/restore/jobs/{job_id}')
    def restore_status(job_id:str,request:Request):
        if not JOB_ID.fullmatch(job_id):raise HTTPException(404)
        directory=root()/'restore_jobs'/job_id
        try:spec=json.loads((directory/'request.json').read_text());status=json.loads((directory/'status.json').read_text())
        except (OSError,ValueError):raise HTTPException(404)
        token=request.headers.get('x-restore-token','')
        if not token or not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(),spec['status_token_hash']):raise HTTPException(403)
        return status

    def finalize_loop():
        while not stop.wait(1):
            try:
                if not maintenance():continue
                job_id=(root()/'.restore-maintenance').read_text().strip()
                if not JOB_ID.fullmatch(job_id):continue
                directory=root()/'restore_jobs'/job_id
                status=json.loads((directory/'status.json').read_text())
                if status.get('status')=='succeeded':
                    (root()/'.restore-maintenance').unlink(missing_ok=True)
                    sip_gate().unlink(missing_ok=True)
                    try:(root()/'.restore-lock').rmdir()
                    except FileNotFoundError:pass
                    continue
                if status.get('status')!='finalizing':continue
                spec=json.loads((directory/'request.json').read_text())
                if spec['source'].startswith('uploads/'):
                    quarantine_imported_recordings(job_id)
                engine.dispose()
                with factory() as session:
                    from app.telephony import recover_interrupted_calls,write_accounts,ami_action
                    recover_interrupted_calls(session)
                    for job in session.scalars(select(AiJob).where(AiJob.status.in_(['queued','running']))):
                        job.status='failed';job.error='Задача прервана восстановлением базы; запустите генерацию заново'
                    already=session.scalar(select(Audit.id).where(Audit.action=='database.restore.completed',Audit.details['job_id'].as_string()==job_id))
                    if not already:
                        for account in session.scalars(select(SipAccount)):account.password=secrets.token_hex(24)
                    if not already:session.add(Audit(action='database.restore.completed',details={'job_id':job_id,'requested_by_name':spec['requested_by_name'],'source':spec['source'],'safety_backup':status.get('safety_backup'),'scope':'database'}))
                    session.commit()
                    warning=None
                    try:
                        write_accounts(session)
                        ami_action('Command',Command='core restart now')
                    except (OSError,ConnectionError):warning='Переподключите SIP-телефоны; если Asterisk недоступен, перезапустите его.'
                (root()/'.auth-epoch').write_text(secrets.token_hex(16))
                (root()/'.backup-pending').mkdir(mode=0o700,exist_ok=True)
                atomic_json(directory/'status.json',{**status,'post_backup_queued':True,'status':'succeeded','message':'База восстановлена. Войдите заново с учётной записью из восстановленной базы.','warning':warning,'updated_at':time.time()})
                (root()/'.restore-maintenance').unlink(missing_ok=True)
                sip_gate().unlink(missing_ok=True)
                (root()/'.restore-lock').rmdir()
            except Exception:
                logging.getLogger(__name__).exception('Restore finalization will retry with the maintenance gate closed')
                # Keep the maintenance gate closed and retry a recoverable finalization.
                continue
    @app.on_event('startup')
    def start_finalizer():
        Thread(target=finalize_loop,daemon=True,name='restore-finalizer').start()
    @app.on_event('shutdown')
    def stop_finalizer():stop.set()
