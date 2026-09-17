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
from sqlalchemy import create_engine, String, Integer, DateTime, ForeignKey, Text, JSON, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
from passlib.context import CryptContext

DATABASE_URL=os.getenv('DATABASE_URL','sqlite:///./data/runtime/arm112.db')
SECRET_KEY=os.getenv('SECRET_KEY','dev-secret-change-me')
engine=create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal=sessionmaker(engine, expire_on_commit=False)
pwd=CryptContext(schemes=['bcrypt'], deprecated='auto')

from app.models import Base, User, Scenario, SessionRun, Audit, RunContext, ScenarioSettings, ExpertReview, Lesson, Material, AccountState, CardAttachment, CardEvent, VoipCall, SipAccount, AiJob
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
def token_for(u:User): return jwt.encode({'sub':str(u.id),'role':u.role,'exp':datetime.now(timezone.utc)+timedelta(hours=12)},SECRET_KEY,algorithm='HS256')
def current(request:Request,s:Session=Depends(db)):
    h=request.headers.get('authorization','')
    if not h.startswith('Bearer '): raise HTTPException(401,'Требуется авторизация')
    try: p=jwt.decode(h[7:],SECRET_KEY,algorithms=['HS256']); u=s.get(User,int(p['sub']))
    except Exception: raise HTTPException(401,'Недействительный токен')
    if not u: raise HTTPException(401)
    state=s.get(AccountState,u.id)
    if state and state.blocked: raise HTTPException(403,"Учётная запись заблокирована")
    return u
def audit(s,u,action,details=None): s.add(Audit(user_id=u.id if u else None,action=action,details=details or {})); s.commit()
def words(x): return set(re.findall(r'[а-яёa-z0-9]+',x.lower()))
def evaluate(sc:Scenario,a:FinishIn,elapsed:int):
    e=sc.expected or {}; parts={};
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
    return apply_neural(report,str(e.get('operator_comment','')),a.operator_comment+' '+a.description,'Смысл текста',30)

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
app.mount('/static',StaticFiles(directory='app/static'),name='static'); templates=Jinja2Templates(directory='app/templates')
@app.on_event('startup')
def startup():
    Path('data/runtime').mkdir(parents=True,exist_ok=True); Base.metadata.create_all(engine)
    with SessionLocal() as s:
        import_classifier(s); import_materials(s); seed(s)
        from app.bundled_scenarios import install_practice_catalog
        install_practice_catalog(s)
        from app.telephony import recover_interrupted_calls
        recover_interrupted_calls(s)
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
    s.query(CardAttachment).filter(CardAttachment.run_id==run_id).delete(synchronize_session=False)
    s.query(CardEvent).filter(CardEvent.run_id==run_id).delete(synchronize_session=False)
    s.query(ExpertReview).filter(ExpertReview.run_id==run_id).delete(synchronize_session=False)
    s.query(RunContext).filter(RunContext.run_id==run_id).delete(synchronize_session=False)
    s.query(VoipCall).filter(VoipCall.run_id==run_id).delete(synchronize_session=False)
    if remove_recording:
        try:Path('/media') .joinpath(f'recording-{run_id}.wav').unlink(missing_ok=True)
        except OSError:pass
    s.query(SessionRun).filter(SessionRun.id==run_id).delete(synchronize_session=False)
def cleanup_expired():
    from datetime import timedelta
    cutoff=datetime.now(timezone.utc)-timedelta(days=RETENTION_DAYS)
    with SessionLocal() as s:
        old=list(s.scalars(select(SessionRun.id).where((SessionRun.finished_at<cutoff)|(SessionRun.finished_at.is_(None)&(SessionRun.started_at<cutoff)))))
        for run_id in old:delete_run_data(s,run_id)
        s.query(Audit).filter(Audit.at<cutoff).delete(synchronize_session=False)
        s.commit()
    return len(old)

ATTACHMENT_ROOT=Path('data/runtime/card_attachments')
ALLOWED_AUDIO={'.wav','.mp3','.ogg','.m4a','.webm','.flac'}
MAX_AUDIO_BYTES=50*1024*1024
@app.get('/api/runs/{run_id}/attachments')
def list_attachments(run_id:int,u=Depends(current),s:Session=Depends(db)):
    get_run(s,u,run_id)
    result=[{'id':x.id,'filename':x.filename,'content_type':x.content_type,'size':x.size,'created_at':x.created_at,'url':f'/api/attachments/{x.id}'} for x in s.scalars(select(CardAttachment).where(CardAttachment.run_id==run_id).order_by(CardAttachment.id.desc()))]
    recording=Path('/media')/f'recording-{run_id}.wav'
    if recording.exists(): result.insert(0,{'id':f'sip-{run_id}','filename':'Запись SIP-звонка.wav','content_type':'audio/wav','size':recording.stat().st_size,'created_at':None,'url':f'/api/telephony/runs/{run_id}/recording'})
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
            try: check_scenario_access(s,u,x)
            except HTTPException: continue
        setting=s.get(ScenarioSettings,x.id)
        result.append({'id':x.id,'title':x.title,'category':x.category,'caller_text':x.caller_text,
                       'created_by':x.created_by,'editable':u.role=='admin' or x.created_by==u.id,
                       'expected':x.expected if u.role in ('teacher','admin') else None,
                       'published':setting.published if setting else True,
                       'difficulty':setting.difficulty if setting else 'basic','mode':setting.mode if setting else 'call',
                       'initial_card':setting.initial_card if setting and u.role!='student' else None,
                       'source':setting.source if setting else {}})
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
    q.title=x.title.strip();q.category=x.category.strip();q.caller_text=x.caller_text.strip();q.expected=x.expected.model_dump()
    setting=s.get(ScenarioSettings,q.id)
    if not setting: setting=ScenarioSettings(scenario_id=q.id);s.add(setting)
    setting.published=False
    audit(s,u,'scenario.edit',{'id':q.id});return {'id':q.id}

@app.post('/api/runs/{scenario_id}/start')
def start_run(scenario_id:int,u=Depends(current),s:Session=Depends(db)):
    if u.role!='student': raise HTTPException(403,'Запуск учебной сессии доступен обучающемуся')
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
    if setting and setting.mode=='dispatch': raise HTTPException(409,'Задание ДДС запускается преподавателем через занятие')
    lesson=next((lesson for lesson in s.scalars(select(Lesson).where(Lesson.status=='active')) if u.id in lesson.student_ids and sc.id in lesson.scenario_ids),None)
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
    if context and context.registered_at and context.scenario_snapshot.get('mode')!='dispatch':
        x=FinishIn.model_validate(r.answers)
    return finalize_run(s,u,r,context,x,evaluate)

@app.get('/api/reports')
def reports(u=Depends(current),s:Session=Depends(db)):
    q=select(SessionRun).order_by(SessionRun.id.desc())
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

@app.get('/api/audit')
def audit_events(u=Depends(current),s:Session=Depends(db)):
    if u.role!='admin': raise HTTPException(403, 'Журнал доступен администратору')
    names={x.id:x.username for x in s.scalars(select(User)).all()}
    return [{'id':a.id,'at':a.at,'username':names.get(a.user_id),'action':a.action,'details':a.details} for a in s.scalars(select(Audit).order_by(Audit.id.desc()).limit(200)).all()]

register_routes(app,db,current,evaluate,pwd)

@app.get('/api/reports/export.csv')
def export_reports(u=Depends(current),s:Session=Depends(db)):
    import csv
    import io
    from fastapi.responses import Response
    buffer=io.StringIO()
    writer=csv.writer(buffer,delimiter=';')
    writer.writerow(['Сессия','Сценарий','Обучающийся','Начало','Завершение','Первичная оценка','Экспертная оценка','Время обработки, сек','Время реакции, сек','Норматив, сек','Отклонение, сек','Ошибки','Комментарий преподавателя'])
    rows=reports(u,s)
    def cell(value):
        value=str(value) if value is not None else ''
        return "'"+value if value.startswith(('=','+','-','@','\t','\r')) else value
    for row in rows:
        report=row['report'] or {};review=row['expert_review'] or {}
        writer.writerow([cell(value) for value in [row['id'],row['scenario_title'],row['student_name'],row['started_at'],row['finished_at'],row['score'],review.get('score'),report.get('elapsed_seconds'),report.get('reaction_seconds'),report.get('norm_seconds'),report.get('time_deviation_seconds'),'; '.join(report.get('errors',[])),review.get('comment')]])
    audit(s,u,'report.export',{'count':len(rows)})
    return Response(('\ufeff'+buffer.getvalue()).encode('utf-8'),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="arm112-results.csv"'})

from app.telephony import register_telephony
register_telephony(app,db,current,SessionLocal)

from app.generation import register_generation
register_generation(app,db,current,SessionLocal)
from app.operations import register_operations
register_operations(app,db,current)
