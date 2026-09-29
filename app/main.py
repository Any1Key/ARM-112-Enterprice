from __future__ import annotations
import os, re, time, json, secrets, threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import jwt
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Integer, DateTime, ForeignKey, Text, JSON, select, update, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
from passlib.context import CryptContext

DATABASE_URL=os.getenv('DATABASE_URL','sqlite:///./data/runtime/arm112.db')
SECRET_KEY=os.getenv('SECRET_KEY','dev-secret-change-me')
engine=create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal=sessionmaker(engine, expire_on_commit=False)
pwd=CryptContext(schemes=['bcrypt'], deprecated='auto')

from app.models import Base, User, Scenario, SessionRun, Audit, RunContext, ScenarioSettings, ExpertReview, Lesson, Material, AccountState, CardAttachment, CardEvent, VoipCall, SipAccount, AiJob, IncomingContact
from app.workflows import (import_classifier, import_materials, register_routes, check_scenario_access, get_run,
                           begin_run, finalize_run, ensure_writable, assert_editable, aware, resolved_card)

class Login(BaseModel): username:str; password:str
class ExpectedIn(BaseModel):
    incident_type: str = Field(min_length=1, max_length=3000)
    address: str = Field(max_length=1000)
    services: list[str] = Field(max_length=50)
    operator_comment: str = Field(min_length=1, max_length=5000)
    norm_seconds: int = Field(default=30, ge=1, le=86400)
    classifier_ids: list[int] = Field(default_factory=list,max_length=10)
    service_status: str = Field(default="Принята",max_length=100)
    caller_name: str = Field(default='',max_length=200)
    caller_phone: str = Field(default='',max_length=100)
    aon: str = Field(default='',max_length=100)
    on_site_phone: str = Field(default='',max_length=100)
    empty_contact: bool = False
    questionnaire_answers: dict[str,dict] = Field(default_factory=dict,max_length=10)
    victims_count: int | None = Field(default=None,ge=0,le=100000)
    caller_status: str = Field(default='',max_length=100)
    victims: bool = False
    access_blocked: bool = False
    life_danger: bool = False
    flags: dict[str,bool] = Field(default_factory=dict,max_length=30)

class ScenarioIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=200)
    caller_text: str = Field(min_length=1, max_length=10000)
    expected: ExpectedIn
    source_material_id: int | None = None

from app.schemas import CardIn as FinishIn

def db():
    s=SessionLocal()
    try: yield s
    finally: s.close()
from app.restoration import auth_epoch,maintenance

def token_for(u:User): return jwt.encode({'sub':str(u.id),'role':u.role,'epoch':auth_epoch(),'exp':datetime.now(timezone.utc)+timedelta(hours=12)},SECRET_KEY,algorithm='HS256')
def current(request:Request,s:Session=Depends(db)):
    h=request.headers.get('authorization','')
    if not h.startswith('Bearer '): raise HTTPException(401,'Требуется авторизация')
    try:p=jwt.decode(h[7:],SECRET_KEY,algorithms=['HS256'])
    except Exception:raise HTTPException(401,'Недействительный токен')
    if p.get('epoch','')!=auth_epoch():raise HTTPException(401,'База данных восстановлена. Войдите заново.')
    try:u=s.get(User,int(p['sub']))
    except Exception:raise HTTPException(401,'Недействительный токен')
    if not u: raise HTTPException(401)
    state=s.get(AccountState,u.id)
    if state and state.blocked: raise HTTPException(403,"Учётная запись заблокирована")
    return u
def audit(s,u,action,details=None): s.add(Audit(user_id=u.id if u else None,action=action,details=details or {})); s.commit()
def words(x): return set(re.findall(r'[а-яёa-z0-9]+',x.lower()))
def evaluate(sc:Scenario,a:FinishIn,elapsed:int):
    e=sc.expected or {}; parts={};
    if e.get('empty_contact'):
        correct=(a.no_contact or a.call_lost) and not a.classifier_ids and not a.description.strip() and not a.services
        return {'score':100 if correct else 0,'parts':{'Обработка вызова без контакта':100 if correct else 0},'parts_max':{'Обработка вызова без контакта':100},'elapsed_seconds':elapsed,'norm_seconds':e.get('norm_seconds',30),'errors':[] if correct else ['Необходимо оформить пустую карточку без вымышленных сведений и отметить отсутствие контакта / срыв звонка.'],'note':'Учебная проверка оформления вызова без контакта.'}
    parts['Тип происшествия']=20 if a.incident_type.strip().lower()==str(e.get('incident_type','')).strip().lower() else 0
    if e.get('classifier_ids'): parts['Тип происшествия']=20 if set(e['classifier_ids'])==set(a.classifier_ids+([a.classifier_id] if a.classifier_id else [])) else 0
    exp_addr=words(str(e.get('address',''))); got_addr=words(a.address); parts['Адрес']=10 if (exp_addr and len(exp_addr&got_addr)/len(exp_addr)>=.6) or (not exp_addr and not got_addr) else 0
    exp_services={x.lower() for x in e.get('services',[])}; got={x.lower() for x in a.services}; parts['Службы']=20 if (exp_services and exp_services.issubset(got)) or (not exp_services and not got) else (10 if exp_services&got else 0)
    exp_text=words(str(e.get('operator_comment',''))); got_text=words(a.operator_comment+' '+a.description); sim=(len(exp_text&got_text)/max(1,len(exp_text)))
    parts['Смысл текста']=30 if sim>=.65 else 20 if sim>=.4 else 10 if sim>=.2 else 0
    norm=int(e.get('norm_seconds',30)); parts['Время']=20 if elapsed<=norm else 10 if elapsed<=norm*1.25 else 0
    errors=[]
    if parts['Тип происшествия']<20: errors.append('Тип происшествия не совпадает с эталоном сценария.')
    if parts['Адрес']<10: errors.append('Адрес неполный или не совпадает с местом происшествия.')
    if parts['Службы']<20: errors.append('Выбраны не все необходимые службы реагирования.')
    if parts['Смысл текста']<30: errors.append('В описании и комментарии не хватает ключевых сведений эталона.')
    if parts['Время']<20: errors.append('Превышен норматив обработки вызова.')
    score=sum(parts.values()); report={'score':score,'errors':errors,'parts':parts,'elapsed_seconds':elapsed,'norm_seconds':norm,'text_overlap':round(sim,2),'note':'Первичная оценка рассчитана по правилам и совпадению ключевых слов. Обсудите ошибки с преподавателем; оценка не заменяет экспертный разбор.'}
    from app.ml import apply_neural
    apply_neural(report,str(e.get('operator_comment','')),a.operator_comment+' '+a.description,'Смысл текста',30)
    from app.questionnaires import grade_answers
    return grade_answers(report,e,a)

def seed(s):
    if not s.scalar(select(User).limit(1)):
        for name,role,env in [('admin','admin','ADMIN_PASSWORD'),('teacher','teacher','TEACHER_PASSWORD'),('student','student','STUDENT_PASSWORD')]: s.add(User(username=name,password_hash=pwd.hash(os.getenv(env,name+'12345')),role=role))
        s.commit()
    if not s.scalar(select(Scenario).limit(1)):
        t=s.scalar(select(User).where(User.username=='teacher'))
        examples=[
            ('Пожар в квартире','Пожар','В квартире сильное задымление. Адрес: Москва, Учебная улица, дом 10. Возможно, внутри есть люди.','Москва Учебная улица дом 10',['101','112'],'Сообщение принято, пожарная служба направлена на место',300),
            ('Человеку стало плохо','Медицинская помощь','Мужчина потерял сознание на остановке. Он дышит, но не отвечает. Москва, Учебная улица, дом 5.','Москва Учебная улица дом 5',['103'],'Сообщение принято, скорая помощь направлена, мужчина без сознания дышит',240),
            ('Запах газа в подъезде','Утечка газа','В подъезде сильно пахнет газом. Москва, Учебная улица, дом 12. Запах идёт с первого этажа, огня нет.','Москва Учебная улица дом 12',['104','112'],'Сообщение принято, газовая служба направлена, запах газа в подъезде',300),
            ('Дорожное происшествие','ДТП','Столкнулись две машины у дома 7 на Учебной улице в Москве. Водитель одной машины ранен, дорога перекрыта.','Москва Учебная улица дом 7',['102','103'],'Сообщение принято, полиция и скорая помощь направлены, водитель ранен дорога перекрыта',300),
        ]
        for title,category,caller,address,services,comment,norm in examples:
            s.add(Scenario(title=title,category=category,caller_text=caller,expected={'incident_type':category,'address':address,'services':services,'operator_comment':comment,'norm_seconds':norm},created_by=t.id))
        s.commit()

app=FastAPI(title='АРМ-112 Учебный тренажёр',version='0.1.0')
from starlette.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware,minimum_size=1000)
app.mount('/static',StaticFiles(directory='app/static'),name='static'); templates=Jinja2Templates(directory='app/templates')
@app.on_event('startup')
def startup():
    Path('data/runtime').mkdir(parents=True,exist_ok=True); Base.metadata.create_all(engine)
    if 'require_sip' not in {c['name'] for c in inspect(engine).get_columns('lessons')}:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE lessons ADD COLUMN require_sip BOOLEAN NOT NULL DEFAULT FALSE'))
    if 'started_at' not in {c['name'] for c in inspect(engine).get_columns('lessons')}:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE lessons ADD COLUMN started_at TIMESTAMP'))
    # Additive migration for installations created before lesson incoming settings existed.
    if 'incoming_config' not in {column['name'] for column in inspect(engine).get_columns('lessons')}:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE lessons ADD COLUMN incoming_config JSON'))
    with SessionLocal() as s:
        import_classifier(s); import_materials(s); seed(s)
        from app.bundled_scenarios import install_practice_catalog
        install_practice_catalog(s)
        from app.telephony import recover_interrupted_calls
        recover_interrupted_calls(s)
        from app.workflows import archive_unassigned_runs
        archive_unassigned_runs(s)
    cleanup_expired()
    def retention_loop():
        while True:
            time.sleep(86400)
            try: cleanup_expired()
            except Exception: pass
    threading.Thread(target=retention_loop,daemon=True).start()
@app.get('/',response_class=HTMLResponse)
def root(request:Request): return templates.TemplateResponse('index.html',{'request':request})
@app.get('/health')
def health(s:Session=Depends(db)):
    try: s.execute(select(1))
    except Exception: raise HTTPException(503, 'База данных недоступна')
    return {'status':'ok','time':datetime.now(timezone.utc).isoformat()}

RETENTION_DAYS=180
def delete_run_data(s,run_id,remove_recording=True):
    for item in s.scalars(select(CardAttachment).where(CardAttachment.run_id==run_id)).all():
        try:(ATTACHMENT_ROOT/item.stored_name).unlink(missing_ok=True)
        except OSError:pass
    from app.models import TrainingMessage
    s.query(TrainingMessage).filter(TrainingMessage.run_id==run_id).delete(synchronize_session=False)
    for linked in s.scalars(select(RunContext)):
        if linked.scenario_snapshot.get('parent_run_id')==run_id:linked.scenario_snapshot={**linked.scenario_snapshot,'parent_run_id':None}
    s.query(CardAttachment).filter(CardAttachment.run_id==run_id).delete(synchronize_session=False)
    s.query(CardEvent).filter(CardEvent.run_id==run_id).delete(synchronize_session=False)
    s.query(ExpertReview).filter(ExpertReview.run_id==run_id).delete(synchronize_session=False)
    s.query(RunContext).filter(RunContext.run_id==run_id).delete(synchronize_session=False)
    s.query(VoipCall).filter(VoipCall.run_id==run_id).delete(synchronize_session=False)
    if remove_recording:
        from app.recordings import media_root
        for recording in [media_root()/f'recording-{run_id}.wav',*media_root().glob(f'recording-{run_id}-*.wav')]:
            try:recording.unlink(missing_ok=True)
            except OSError:pass
    s.query(SessionRun).filter(SessionRun.id==run_id).delete(synchronize_session=False)
def cleanup_expired():
    if maintenance():return 0
    from app.operations import backup_snapshot
    if backup_snapshot()['status']!='ok':
        try:(Path(os.getenv('BACKUP_ROOT','/backups'))/'.backup-pending').mkdir(mode=0o700)
        except FileExistsError:pass
        return 0
    from datetime import timedelta
    cutoff=datetime.now(timezone.utc)-timedelta(days=RETENTION_DAYS)
    with SessionLocal() as s:
        old=list(s.scalars(select(SessionRun.id).where((SessionRun.finished_at<cutoff)|(SessionRun.finished_at.is_(None)&(SessionRun.started_at<cutoff)))))
        for run_id in old:delete_run_data(s,run_id)
        # Issue reviews are durable support state, also included in DB backups.
        s.query(Audit).filter(Audit.at<cutoff,Audit.action!='issue.review').delete(synchronize_session=False)
        s.commit()
    return len(old)

ATTACHMENT_ROOT=Path('data/runtime/card_attachments')
ALLOWED_AUDIO={'.wav','.mp3','.ogg','.m4a','.webm','.flac'}
MAX_AUDIO_BYTES=50*1024*1024
@app.get('/api/runs/{run_id}/attachments')
def list_attachments(run_id:int,u=Depends(current),s:Session=Depends(db)):
    get_run(s,u,run_id)
    result=[{'id':x.id,'filename':x.filename,'content_type':x.content_type,'size':x.size,'created_at':x.created_at,'url':f'/api/attachments/{x.id}'} for x in s.scalars(select(CardAttachment).where(CardAttachment.run_id==run_id).order_by(CardAttachment.id.desc()))]
    from app.recordings import recording_files
    files=recording_files(s,run_id)
    for call_id,recording in sorted(files.items(),reverse=True):
        result.insert(0,{'id':f'sip-{call_id}','filename':f'Запись SIP-звонка №{call_id}.wav','content_type':'audio/wav','size':recording.stat().st_size,'created_at':None,'url':f'/api/telephony/runs/{run_id}/recording?call_id={call_id}'})
    return result
@app.post('/api/runs/{run_id}/attachments')
async def upload_attachment(run_id:int,file:UploadFile=File(...),u=Depends(current),s:Session=Depends(db)):
    get_run(s,u,run_id)
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in ALLOWED_AUDIO or not (file.content_type or '').startswith('audio/'):
        raise HTTPException(415,'Разрешены только аудиофайлы WAV, MP3, OGG, M4A, WEBM или FLAC')
    content=await file.read(MAX_AUDIO_BYTES+1)
    if len(content)>MAX_AUDIO_BYTES: raise HTTPException(413,'Размер аудиофайла не должен превышать 50 МБ')
    ATTACHMENT_ROOT.mkdir(parents=True,exist_ok=True)
    stored=f'{secrets.token_hex(16)}{suffix}';(ATTACHMENT_ROOT/stored).write_bytes(content)
    item=CardAttachment(run_id=run_id,user_id=u.id,filename=Path(file.filename or 'audio').name[:255],stored_name=stored,content_type=file.content_type,size=len(content));s.add(item);s.flush();audit(s,u,'card.attachment.upload',{'run_id':run_id,'attachment_id':item.id,'size':len(content)});return {'id':item.id,'filename':item.filename,'content_type':item.content_type,'size':item.size,'created_at':item.created_at,'url':f'/api/attachments/{item.id}'}
@app.get('/api/attachments/{attachment_id}')
def download_attachment(attachment_id:int,u=Depends(current),s:Session=Depends(db)):
    from fastapi.responses import FileResponse
    item=s.get(CardAttachment,attachment_id)
    if not item: raise HTTPException(404)
    get_run(s,u,item.run_id);path=ATTACHMENT_ROOT/item.stored_name
    if not path.exists(): raise HTTPException(404,'Файл вложения не найден')
    return FileResponse(path,media_type=item.content_type,filename=item.filename)
@app.delete('/api/cards/{run_id}')
def delete_card(run_id:int,u=Depends(current),s:Session=Depends(db)):
    if u.role!='admin': raise HTTPException(403)
    from app.operations import require_fresh_backup
    require_fresh_backup()
    run=s.get(SessionRun,run_id)
    if not run: raise HTTPException(404)
    delete_run_data(s,run_id);audit(s,u,'card.delete',{'run_id':run_id});return {'status':'deleted','run_id':run_id}
@app.post('/api/retention/cleanup')
def run_retention_cleanup(u=Depends(current)):
    if u.role!='admin': raise HTTPException(403)
    return {'deleted_cards':cleanup_expired(),'retention_days':RETENTION_DAYS}
@app.post('/api/login')
def login(x:Login,s:Session=Depends(db)):
    u=s.scalar(select(User).where(User.username==x.username))
    if not u or not pwd.verify(x.password,u.password_hash): raise HTTPException(401,'Неверный логин или пароль')
    account=s.get(AccountState,u.id)
    if account and account.blocked: raise HTTPException(403,'Учётная запись заблокирована')
    audit(s,u,'login'); return {'token':token_for(u),'role':u.role,'username':u.username}
@app.get('/api/scenarios')
def scenarios(u=Depends(current),s:Session=Depends(db)):
    result=[]
    for x in s.scalars(select(Scenario).order_by(Scenario.id.desc())):
        if u.role=='student':
            try: check_scenario_access(s,u,x,for_listing=True)
            except HTTPException: continue
        setting=s.get(ScenarioSettings,x.id)
        result.append({'id':x.id,'title':x.title,'category':x.category,'caller_text':x.caller_text,
                       'created_by':x.created_by,'editable':u.role=='admin' or x.created_by==u.id,
                       'expected':x.expected if u.role in ('teacher','admin') else None,
                       'published':setting.published if setting else True,
                       'difficulty':setting.difficulty if setting else 'basic','mode':setting.mode if setting else 'call',
                       'initial_card':setting.initial_card if setting and u.role!='student' else None,
                       'source':setting.source if setting and u.role!='student' else {}})
    return result
@app.post('/api/scenarios')
def create_scenario(x:ScenarioIn,u=Depends(current),s:Session=Depends(db)):
    if u.role not in ('teacher','admin'): raise HTTPException(403)
    if u.role=='admin' and s.scalar(select(Lesson.id).where(Lesson.status=='active').limit(1)):
        raise HTTPException(409,'Администратор не может менять сценарии во время занятия')
    source={}
    if x.source_material_id:
        material=s.get(Material,x.source_material_id)
        if not material: raise HTTPException(422,'Исходный билет не найден')
        source={**material.source,'material_id':material.id}
    q=Scenario(title=x.title.strip(),category=x.category.strip(),caller_text=x.caller_text.strip(),expected=x.expected.model_dump(),created_by=u.id)
    s.add(q);s.flush();s.add(ScenarioSettings(scenario_id=q.id,published=False,source=source))
    audit(s,u,'scenario.create',{'id':q.id});return {'id':q.id}

@app.put('/api/scenarios/{scenario_id}')
def edit_scenario(scenario_id:int,x:ScenarioIn,u=Depends(current),s:Session=Depends(db)):
    q=s.get(Scenario,scenario_id)
    if not q: raise HTTPException(404)
    assert_editable(s,u,q)
    setting=s.get(ScenarioSettings,q.id)
    if setting and setting.mode=='dds':raise HTTPException(422,'Редактируйте исходную карточку и эталон через кабинет ДДС')
    q.title=x.title.strip();q.category=x.category.strip();q.caller_text=x.caller_text.strip();q.expected=x.expected.model_dump()
    setting=s.get(ScenarioSettings,q.id)
    if not setting: setting=ScenarioSettings(scenario_id=q.id);s.add(setting)
    setting.published=False
    audit(s,u,'scenario.edit',{'id':q.id});return {'id':q.id}

@app.post('/api/runs/{scenario_id}/start')
def start_run(scenario_id:int,u=Depends(current),s:Session=Depends(db)):
    if u.role!='student': raise HTTPException(403,'Запуск учебной сессии доступен обучающемуся')
    from app.workflows import archive_unassigned_runs
    archive_unassigned_runs(s,u.id)
    s.scalar(select(User).where(User.id==u.id).with_for_update())
    sc=s.get(Scenario,scenario_id)
    if not sc: raise HTTPException(404)
    check_scenario_access(s,u,sc)
    active=s.scalar(select(SessionRun).where(SessionRun.student_id==u.id,SessionRun.finished_at.is_(None)))
    if active:
        if active.scenario_id!=scenario_id: raise HTTPException(409,'Сначала завершите активную тренировку')
        context=s.get(RunContext,active.id)
        from app.workflows import run_payload
        return run_payload(s,active,context)
    setting=s.get(ScenarioSettings,scenario_id)
    if setting and setting.mode in ('dispatch','dds'): raise HTTPException(409,'Задание ДДС запускается преподавателем через занятие')
    matching=[item for item in s.scalars(select(Lesson).where(Lesson.status=='active').order_by(Lesson.id.desc())) if u.id in item.student_ids and sc.id in item.scenario_ids]
    lesson=None
    for item in matching:
        completed=s.scalars(select(SessionRun).join(RunContext).where(SessionRun.student_id==u.id,SessionRun.scenario_id==sc.id,RunContext.lesson_id==item.id,SessionRun.finished_at.is_not(None)))
        if not any(not (run.report or {}).get('skipped') for run in completed):
            lesson=item;break
    if matching and not lesson:raise HTTPException(409,'Задание уже выполнено на назначенных занятиях. Откройте результат; повторный запуск заблокирован.')
    run,context=begin_run(s,u,sc,lesson)
    from app.workflows import run_payload
    return run_payload(s,run,context)

@app.post('/api/runs/{run_id}/finish')
def finish(run_id:int,x:FinishIn,u=Depends(current),s:Session=Depends(db)):
    if u.role!='student': raise HTTPException(403)
    r=s.scalar(select(SessionRun).where(SessionRun.id==run_id).with_for_update())
    if not r or r.student_id!=u.id: raise HTTPException(404)
    if r.finished_at is not None: return r.report
    context=s.get(RunContext,run_id);ensure_writable(r,context,s)
    if context and context.scenario_snapshot.get('dds_rules_version')==2:
        from app.workflows import history_for, events_for
        from app.training import dds_status_complete
        own=history_for(events_for(s,r)).get(context.scenario_snapshot['service_code'],[])
        statuses=[item['status'] for item in own if item['status'] not in ('Добавлена','Получена службой')]
        if not dds_status_complete(statuses,context.scenario_snapshot['service_code']):
            raise HTTPException(409,'Заполните все обязательные статусы своей службы либо оформите мотивированный отказ')
    if context and context.registered_at and context.scenario_snapshot.get('mode')!='dispatch':
        x=FinishIn.model_validate(r.answers)
    return finalize_run(s,u,r,context,x,evaluate)

@app.get('/api/reports')
def reports(u=Depends(current),s:Session=Depends(db),started_from:datetime|None=None,started_before:datetime|None=None,student_id:int|None=None):
    q=select(SessionRun).order_by(SessionRun.id.desc())
    if started_from and started_before and aware(started_from)>=aware(started_before):
        raise HTTPException(422,'Начало периода должно быть раньше конца')
    if started_from: q=q.where(SessionRun.started_at>=aware(started_from).astimezone(timezone.utc))
    if started_before: q=q.where(SessionRun.started_at<aware(started_before).astimezone(timezone.utc))
    if student_id is not None: q=q.where(SessionRun.student_id==student_id)
    if u.role=='student': q=q.where(SessionRun.student_id==u.id)
    names={x.id:x.username for x in s.scalars(select(User)).all()}
    titles={x.id:x.title for x in s.scalars(select(Scenario)).all()}
    rows=[]
    for r in s.scalars(q):
        context=s.get(RunContext,r.id)
        if u.role=='teacher':
            lesson=s.get(Lesson,context.lesson_id) if context and context.lesson_id else None
            scenario=s.get(Scenario,r.scenario_id)
            if (lesson and lesson.teacher_id!=u.id) or (not lesson and scenario.created_by!=u.id): continue
        review=s.scalar(select(ExpertReview).where(ExpertReview.run_id==r.id).order_by(ExpertReview.id.desc()))
        rows.append({'scenario_title':context.scenario_snapshot['title'] if context else titles.get(r.scenario_id),'student_name':names.get(r.student_id),
                     'id':r.id,'scenario_id':r.scenario_id,'student_id':r.student_id,'started_at':aware(r.started_at),'finished_at':aware(r.finished_at),
                     'score':r.score,'report':r.report,'lesson_id':context.lesson_id if context else None,
                     'expert_review':{'score':review.score,'comment':review.comment,'at':aware(review.at)} if review else None})
    return rows

@app.get('/api/reports/insights')
def report_insights(u=Depends(current),s:Session=Depends(db),started_from:datetime|None=None,started_before:datetime|None=None):
    """Return local, explainable group recommendations for teachers and admins."""
    if u.role not in ('teacher','admin'):
        raise HTTPException(403, 'Аналитические рекомендации доступны преподавателю')
    rows=reports(u,s,started_from,started_before)
    completed=[row for row in rows if row['finished_at'] and not (row['report'] or {}).get('skipped')]
    error_counts={}
    scenario_stats={}
    for row in completed:
        report=row['report'] or {}
        key=row['scenario_title'] or f"Сценарий №{row['scenario_id']}"
        bucket=scenario_stats.setdefault(key,{'attempts':0,'score_sum':0,'low_scores':0,'errors':{}})
        for error in report.get('errors',[]):
            error_counts[error]=error_counts.get(error,0)+1
            bucket['errors'][error]=bucket['errors'].get(error,0)+1
        bucket['attempts']+=1
        if row['score'] is not None:
            bucket['score_sum']+=row['score']
            bucket['low_scores']+=row['score']<60
    frequent=[{'error':error,'count':count} for error,count in sorted(error_counts.items(),key=lambda item:(-item[1],item[0]))[:5]]
    weak_scenarios=[]
    for title,bucket in scenario_stats.items():
        average=round(bucket['score_sum']/bucket['attempts'],1) if bucket['attempts'] else None
        if average is not None and (average<80 or bucket['low_scores']):
            weak_scenarios.append({'scenario':title,'attempts':bucket['attempts'],'average_score':average,'low_scores':bucket['low_scores']})
    weak_scenarios.sort(key=lambda item:(item['average_score'],item['scenario']))
    scenario_chart=[{'scenario':title,'attempts':bucket['attempts'],
                     'average_score':round(bucket['score_sum']/bucket['attempts'],1) if bucket['attempts'] else None,
                     'low_scores':bucket['low_scores'],'errors':bucket['errors']}
                    for title,bucket in scenario_stats.items()]
    scenario_chart.sort(key=lambda item:(item['average_score'] is None,item['average_score'] if item['average_score'] is not None else 0,item['scenario']))
    recommendations=[]
    for item in frequent[:3]:
        recommendations.append(f"Разберите с группой ошибку «{item['error']}» — повторилась {item['count']} раз.")
    for item in weak_scenarios[:3]:
        recommendations.append(f"Назначьте дополнительную тренировку «{item['scenario']}»: средний балл {item['average_score']}.")
    if not recommendations and completed:
        recommendations.append('Критичных повторяющихся ошибок не выявлено; продолжайте плановую практику.')
    return {'attempts':len(rows),'completed':len(completed),'frequent_errors':frequent,
            'scenario_chart':scenario_chart[:20],
            'weak_scenarios':weak_scenarios[:5],'recommendations':recommendations[:6],
            'method':'Локальная объяснимая аналитика по результатам и ошибкам; внешние данные не используются.'}

@app.get('/api/audit')
def audit_events(u=Depends(current),s:Session=Depends(db)):
    if u.role!='admin': raise HTTPException(403, 'Журнал доступен администратору')
    names={x.id:x.username for x in s.scalars(select(User)).all()}
    return [{'id':a.id,'at':a.at,'username':names.get(a.user_id),'action':a.action,'details':a.details} for a in s.scalars(select(Audit).order_by(Audit.id.desc()).limit(200)).all()]

register_routes(app,db,current,evaluate,pwd)

@app.get('/api/reports/export.csv')
def export_reports(u=Depends(current),s:Session=Depends(db),started_from:datetime|None=None,started_before:datetime|None=None,student_id:int|None=None):
    import csv
    import io
    from fastapi.responses import Response
    buffer=io.StringIO()
    writer=csv.writer(buffer,delimiter=';')
    writer.writerow(['Сессия','Сценарий','Обучающийся','Начало','Завершение','Первичная оценка','Экспертная оценка','Время обработки, сек','Время реакции, сек','Норматив, сек','Отклонение, сек','Ошибки','Комментарий преподавателя','Режим','Регистрация','Канал','Время после регистрации, сек','Пропуски','Тип студента','Тип эталона','Адрес студента','Адрес эталона','Заявитель','АОН','Обратный номер','Пострадавшие','Службы студента','Не выбранные службы','Описание студента','Комментарий студента','Вывод ДДС','Замечания ДДС','Передано службам ДДС','Способы передачи ДДС','Учёт SIP-звонков ДДС','Службы без завершённого звонка ДДС','Службы без успешной SIP-передачи ДДС','Своя служба ДДС','Решение ДДС','Последний статус ДДС'])
    rows=reports(u,s,started_from,started_before,student_id)
    def cell(value):
        value=str(value) if value is not None else ''
        return "'"+value if value.startswith(('=','+','-','@','\t','\r')) else value
    for row in rows:
        report=row['report'] or {};review=row['expert_review'] or {}
        run=s.get(SessionRun,row['id']);context=s.get(RunContext,row['id']);card=run.answers or {}
        snapshot=context.scenario_snapshot if context else {}
        expected=snapshot.get('expected',{}) if u.role in ('teacher','admin') else {}
        from app.workflows import events_for,postprocessing_seconds
        events=events_for(s,run)
        writer.writerow([cell(value) for value in [row['id'],row['scenario_title'],row['student_name'],row['started_at'],row['finished_at'],row['score'],review.get('score'),report.get('elapsed_seconds'),report.get('reaction_seconds'),report.get('norm_seconds'),report.get('time_deviation_seconds'),'; '.join(report.get('errors',[])),review.get('comment'),
            'ДДС' if snapshot.get('mode') in ('dispatch','dds') else '112',context.registered_at if context else None,card.get('channel'),postprocessing_seconds(run,context,events),sum(e.kind=='card.skip' for e in events),card.get('incident_type'),expected.get('incident_type'),card.get('address'),expected.get('address'),card.get('caller_name'),card.get('aon'),card.get('caller_phone'),card.get('victims_count'),', '.join(card.get('services',[])),', '.join(sorted(set(expected.get('services',[]))-set(card.get('services',[])))) if expected and snapshot.get('dds_workflow')!='status' else '',card.get('description'),card.get('operator_comment'),(report.get('dds') or {}).get('verdict'),(report.get('dds') or {}).get('findings'),', '.join((report.get('dds') or {}).get('delivered_services',[])),', '.join(sorted({h.get('transport','') for h in (report.get('dds') or {}).get('handoffs',[])})),('Да' if snapshot.get('require_sip') else 'Нет') if snapshot.get('mode')=='dds' and snapshot.get('dds_workflow')!='status' else '',', '.join((report.get('dds') or {}).get('missing_call_services',[])),', '.join((report.get('dds') or {}).get('pending_sip_services',[])),(report.get('dds') or {}).get('service_code'),(report.get('dds') or {}).get('decision'),(report.get('dds') or {}).get('latest_status')]])
    audit(s,u,'report.export',{'count':len(rows),'started_from':started_from.isoformat() if started_from else None,'started_before':started_before.isoformat() if started_before else None,'student_id':student_id})
    return Response(('\ufeff'+buffer.getvalue()).encode('utf-8'),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="arm112-results.csv"'})

from app.telephony import register_telephony
register_telephony(app,db,current,SessionLocal)

from app.generation import register_generation
register_generation(app,db,current,SessionLocal)
from app.operations import register_operations
register_operations(app,db,current)

from app.card_extensions import register_extensions
register_extensions(app,db,current)

from app.report_details import register_report_details
register_report_details(app,db,current)

from app.restoration import register_restoration
from app.operations import backup_catalog
register_restoration(app,db,current,SessionLocal,engine,pwd.verify,backup_catalog)

from app.dds import register_dds
register_dds(app,db,current)

from app.dds_telephony import register_dds_telephony
register_dds_telephony(app,db,current,SessionLocal)
