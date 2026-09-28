"""DDS exercises: immutable received card, fact-based corrections and service handoff."""
from copy import deepcopy
from datetime import datetime, timezone
import re
import secrets
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, or_, and_
from app.models import Scenario, ScenarioSettings, SessionRun, RunContext, IncidentType, Lesson, CardEvent, Audit, ClassifierVersion, VoipCall
from app.schemas import CardIn
from app.classifier import SERVICE_NAMES
from app.workflows import get_run, ensure_writable, assert_teacher, assert_editable, now, events_for, elapsed_seconds

VARIANTS = {'clean':'Служба указана по назначению','services':'Карточка направлена не той службе'}
FIELDS = ('incident_type','address','description','caller_name','caller_phone','aon','on_site_phone','victims_count','classifier_ids','services')
LABELS = {'incident_type':'Тип происшествия','address':'Адрес','description':'Описание','caller_name':'Заявитель','caller_phone':'Обратный телефон','aon':'АОН','on_site_phone':'Телефон на месте','victims_count':'Пострадавшие','classifier_ids':'Классы ЕКП','services':'Службы'}

class BuildIn(BaseModel):
    title: str = Field(default='Реагирование службы ДДС',min_length=1,max_length=250)
    categories: list[str] = Field(default_factory=list,max_length=24)
    source: Literal['generated','students','mixed'] = 'generated'
    variants: list[Literal['clean','typos','data','services','mixed']] = Field(default_factory=lambda:['clean','services'],min_length=1,max_length=5)
    count: int = Field(default=10,ge=1,le=100)
    norm_seconds: int = Field(default=180,ge=1,le=86400)

class CorrectionIn(BaseModel):
    revision: int = Field(ge=0)
    card: CardIn
    verdict: Literal['correct','corrected','clarification']
    findings: str = Field(default='',max_length=5000)
    comment: str = Field(min_length=1,max_length=5000)

class ExerciseEditIn(BaseModel):
    card: CardIn
    title: str | None = Field(default=None,min_length=1,max_length=300)
    norm_seconds: int | None = Field(default=None,ge=1,le=86400)
    reference_text: str | None = Field(default=None,min_length=1,max_length=10000)
    reference_card: CardIn | None = None

class HandoffIn(BaseModel):
    service: str = Field(min_length=1,max_length=100)
    receiver: str = Field(min_length=1,max_length=200)
    message: str = Field(min_length=1,max_length=3000)
    outcome: Literal['accepted','unavailable','rejected'] = 'accepted'
    call_id: int | None = None


def directory(s):
    version=s.scalar(select(ClassifierVersion).order_by(ClassifierVersion.id.desc()))
    names=dict(SERVICE_NAMES)
    if version:
        for item in version.manifest['columns']:names.setdefault(item['code'],item.get('name',item['code']))
    return names


def normalize(value):
    return re.sub(r'\s+',' ',str('' if value is None else value)).strip().casefold().replace('ё','е')


def changed_fields(before,after):
    return [key for key in FIELDS if (set(before.get(key,[]) or [])!=set(after.get(key,[]) or []) if key in ('services','classifier_ids') else normalize(before.get(key))!=normalize(after.get(key)))]


def received_card(card,names):
    """Never auto-resolve: incorrect alert recipients must remain visible."""
    result=CardIn.model_validate(card).model_dump()
    result['service_phones']={code:phone.strip() for code,phone in result['service_phones'].items() if code in result['services'] and phone.strip()}
    result['dispatch']=[{'code':code,'name':names.get(code,code),'visible':True,'origin':'Карточка 112','mappings':[]} for code in result['services']]
    return result


def damage(gold,variant,names):
    card=deepcopy(gold)
    if variant in ('typos','mixed'):
        word=re.search(r'[А-Яа-яЁё]{5,}',card['description'])
        if word:
            text=word[0];wrong=text[:2]+text[3]+text[2]+text[4:]
            if wrong==text:wrong=text[:2]+text[3:]
            card['description']=card['description'][:word.start()]+wrong+card['description'][word.end():]
    if variant in ('data','mixed'):
        # Address change is objectively checkable against the supplied call facts.
        house=re.search(r'(\bдом\s+)(\d+)',card['address'],re.I)
        if house:
            card['address']=card['address'][:house.start(2)]+str(int(house[2])+secrets.choice((1,2,10)))+card['address'][house.end(2):]
        else:
            card['address']='Место происшествия не указано'
    if variant in ('services','mixed'):
        correct=list(card['services'])
        extra=[code for code in names if code not in correct and code!='112']
        if correct:card['services']=correct[1:]
        if extra:card['services'].append(secrets.choice(extra))
    return card


def teacher_sources(s,u,categories):
    rows=[]
    for run,context,lesson,scenario in s.execute(select(SessionRun,RunContext,Lesson,Scenario).select_from(SessionRun).join(RunContext,RunContext.run_id==SessionRun.id).outerjoin(Lesson,Lesson.id==RunContext.lesson_id).join(Scenario,Scenario.id==SessionRun.scenario_id).where(SessionRun.finished_at.is_not(None),or_(Lesson.teacher_id==u.id,and_(Lesson.id.is_(None),Scenario.created_by==u.id)))):
        if (run.report or {}).get('skipped') or context.scenario_snapshot.get('mode','call')!='call':continue
        if (lesson.teacher_id if lesson else scenario.created_by)!=u.id:continue
        snapshot=context.scenario_snapshot
        if categories and snapshot.get('category',scenario.category) not in categories:continue
        if run.answers.get('address') and run.answers.get('description'):
            rows.append((run,context,scenario))
    return rows


def confirmed_sip(s,run,item):
    call=s.get(VoipCall,item.get('call_id')) if item.get('call_id') else None
    prepared=next((e for e in events_for(s,run) if e.kind=='dds.call.prepared' and e.data.get('call_id')==item.get('call_id')),None)
    return bool(call and call.run_id==run.id and call.answered_at and call.ended_at and call.state in ('ended','cancelled') and prepared and prepared.data.get('service')==item.get('service'))

def pending_sip_services(s,run,context):
    gold=context.scenario_snapshot['expected']['dds_gold']
    delivered={e.data['service'] for e in events_for(s,run) if e.kind=='dds.handoff' and e.data.get('outcome')=='accepted' and confirmed_sip(s,run,e.data) and normalize(gold['address']) in normalize(e.data.get('message',''))}
    return sorted(set(gold['services'])-delivered)

def evaluate_dds(s,run,context,elapsed):
    snapshot=context.scenario_snapshot
    gold=snapshot['expected']['dds_gold'];original=snapshot['dds_original']
    corrections=[e for e in events_for(s,run) if e.kind=='dds.validation']
    validation=corrections[-1].data if corrections else {}
    card=run.answers
    differences=changed_fields(gold,card)
    injected=changed_fields(gold,original)
    actual_errors=bool(injected)
    verdict=validation.get('verdict')
    verdict_ok=verdict==('corrected' if actual_errors else 'correct')
    # Clarification is honest but incomplete until confirmed; never fabricate data.
    field_results=[{'field':key,'label':LABELS[key],'received':original.get(key),'expected':gold.get(key),'actual':card.get(key),'correct':key not in differences,'was_wrong':key in injected} for key in FIELDS]
    checked=['incident_type','address','description','caller_name','caller_phone','aon','on_site_phone','victims_count','classifier_ids']
    correct_count=sum(key not in differences for key in checked)
    calls={c.id:c for c in s.scalars(select(VoipCall).where(VoipCall.run_id==run.id))}
    handoffs=[e.data for e in events_for(s,run) if e.kind=='dds.handoff']
    delivered=set()
    for item in handoffs:
        call=calls.get(item.get('call_id'))
        connected=confirmed_sip(s,run,item) if snapshot.get('require_sip') else (not item.get('call_id') or bool(call and call.answered_at))
        if item['outcome']=='accepted' and connected and normalize(gold['address']) in normalize(item['message']):delivered.add(item['service'])
    required=set(gold['services'])
    transmitted=required&delivered
    norm=int(snapshot['expected'].get('norm_seconds',180))
    parts={'Проверка карточки':10 if corrections and verdict_ok and (not actual_errors or validation.get('findings','').strip()) else 0,
           'Данные и опечатки':round(35*correct_count/len(checked)) if corrections else 0,
           'Службы реагирования':20 if corrections and set(card['services'])==required else 0,
           'Передача информации':round(25*len(transmitted)/len(required|delivered)) if required else (25 if corrections else 0),
           'Время обработки':10 if elapsed<=norm and corrections else 0}
    errors=[]
    if not corrections:errors.append('Проверка карточки не сохранена')
    elif not verdict_ok:errors.append('Вывод о корректности исходной карточки не совпадает с эталоном')
    errors += ['Не исправлено или изменено ошибочно: '+LABELS[key] for key in differences]
    missing_calls=sorted(required-{e.data.get('service') for e in events_for(s,run) if e.kind=='dds.call.prepared' and confirmed_sip(s,run,e.data)}) if snapshot.get('require_sip') else []
    if missing_calls:errors.append('Нет подтверждённого завершённого SIP-звонка службам: '+', '.join(missing_calls))
    if required-delivered:errors.append('Не подтверждена передача точного адреса службам: '+', '.join(sorted(required-delivered)))
    if delivered-required:errors.append('Информация передана лишним службам: '+', '.join(sorted(delivered-required)))
    if elapsed>norm:errors.append('Превышен норматив обработки')
    from app.grammar import check_grammar
    grammar=check_grammar(card.get('description','')+'\n'+validation.get('comment',''))
    return {'score':sum(parts.values()),'parts':parts,'parts_max':{'Проверка карточки':10,'Данные и опечатки':35,'Службы реагирования':20,'Передача информации':25,'Время обработки':10},
            'elapsed_seconds':elapsed,'norm_seconds':norm,'time_deviation_seconds':elapsed-norm,'errors':errors,'grammar':grammar,
            'dds':{'require_sip':bool(snapshot.get('require_sip')), 'missing_call_services':missing_calls, 'pending_sip_services':pending_sip_services(s,run,context) if snapshot.get('require_sip') else [],'variant':snapshot['expected'].get('dds_variant'),'verdict':verdict,'findings':validation.get('findings',''),'comment':validation.get('comment',''),
                   'field_results':field_results,'required_services':sorted(required),'delivered_services':sorted(delivered),'handoffs':handoffs,'validation_count':len(corrections)},
            'note':'Учебная оценка ДДС: проверка исходной карточки, исправления, службы, подтверждённая передача адреса и время. Запись разговора оценивается преподавателем; автоматического распознавания речи нет.'}

def evaluate_legacy_status_dds(s,run,context,elapsed):
    """Keep scores of attempts started before the confirmed DDS timing rules."""
    from app.training import ACCEPTED, REJECTED, TERMINAL
    from app.workflows import history_for, aware
    snapshot=context.scenario_snapshot;service=snapshot['service_code']
    records=history_for(events_for(s,run)).get(service,[])
    actions=[item for item in records if item['status'] not in ('Добавлена','Получена службой')]
    first=actions[0] if actions else None
    expected=ACCEPTED if service in snapshot['expected']['dds_gold'].get('services',[]) else REJECTED
    decision_ok=bool(first and first['status']==expected)
    latest=actions[-1]['status'] if actions else None
    complete=bool(latest==REJECTED if expected==REJECTED else latest in TERMINAL and latest!='Отказ от выполнения работ')
    commented=bool(actions and all(item.get('comment','').strip() for item in actions))
    norm=int(snapshot['expected'].get('norm_seconds',180))
    reaction=max(0,int((datetime.fromisoformat(first['at'])-aware(context.registered_at)).total_seconds())) if first else elapsed
    parts={'Решение своей службы':35 if decision_ok else 0,'Статусы реагирования':25 if decision_ok and complete else 0,
           'Комментарии к статусам':20 if commented else 0,'Время первой реакции':20 if first and reaction<=norm else 0}
    errors=[]
    if not first:errors.append('Нет решения о приёме или отказе')
    elif not decision_ok:errors.append('Решение службы не соответствует учебному сценарию')
    if not complete:errors.append('Работа своей службы не доведена до завершающего статуса')
    if not commented:errors.append('Не ко всем действиям добавлены комментарии')
    if reaction>norm:errors.append('Превышен учебный норматив первой реакции')
    return {'score':sum(parts.values()),'parts':parts,'parts_max':{'Решение своей службы':35,'Статусы реагирования':25,'Комментарии к статусам':20,'Время первой реакции':20},
            'elapsed_seconds':elapsed,'reaction_seconds':reaction,'norm_seconds':norm,'time_deviation_seconds':reaction-norm,'errors':errors,
            'dds':{'workflow':'status','service_code':service,'expected_decision':expected,'decision':first['status'] if first else None,'latest_status':latest,'status_history':actions},
            'note':'Историческая учебная оценка ДДС по правилам, действовавшим при запуске попытки.'}

def evaluate_status_dds(s,run,context,elapsed):
    """Assess only the receiving service's decisions and status history."""
    if context.scenario_snapshot.get('dds_rules_version')!=2:
        return evaluate_legacy_status_dds(s,run,context,elapsed)
    from app.training import ACCEPTED, REJECTED, dds_status_complete
    from app.workflows import history_for, dds_timing
    snapshot=context.scenario_snapshot
    service=snapshot['service_code']
    records=history_for(events_for(s,run)).get(service,[])
    actions=[item for item in records if item['status'] not in ('Добавлена','Получена службой')]
    first=actions[0] if actions else None
    expected=ACCEPTED if service in snapshot['expected']['dds_gold'].get('services',[]) else REJECTED
    decision_ok=bool(first and first['status']==expected)
    latest=actions[-1]['status'] if actions else None
    statuses=[item['status'] for item in actions]
    complete=dds_status_complete(statuses,service)
    automatically_completed=complete and (expected==REJECTED or latest!='Отказ от выполнения работ')
    commented=bool(actions and all(item.get('comment','').strip() for item in actions))
    timing=dds_timing(run,context,history_for(events_for(s,run)))
    opening=timing['opening_seconds'];reaction=timing['first_entry_seconds']
    parts={'Решение своей службы':35 if decision_ok else 0,
           'Статусы реагирования':25 if decision_ok and automatically_completed else 0,
           'Комментарии к статусам':20 if commented else 0,
           'Открытие за 30 секунд':10 if opening<=30 else 0,
           'Первая запись за 3 минуты':10 if reaction is not None and reaction<=180 else 0}
    errors=[]
    if not first:errors.append('Нет решения о приёме или отказе')
    elif not decision_ok:errors.append('Решение службы не соответствует учебному сценарию')
    if not complete:errors.append('Работа своей службы не доведена до завершающего статуса')
    elif latest=='Отказ от выполнения работ':errors.append('Мотивированный отказ от работ требует экспертной оценки преподавателя')
    if not commented:errors.append('Не ко всем действиям добавлены комментарии')
    if opening>30:errors.append('Карточка открыта позже 30 секунд после поступления')
    if reaction is None or reaction>180:errors.append('Первая запись не внесена в течение 3 минут после поступления')
    return {'score':sum(parts.values()),'parts':parts,'parts_max':{'Решение своей службы':35,'Статусы реагирования':25,'Комментарии к статусам':20,'Открытие за 30 секунд':10,'Первая запись за 3 минуты':10},
            'elapsed_seconds':elapsed,'reaction_seconds':reaction,'opening_seconds':opening,'first_entry_seconds':reaction,'opening_norm_seconds':30,'norm_seconds':180,
            'time_deviation_seconds':(reaction if reaction is not None else elapsed)-180,'errors':errors,
            'dds':{'workflow':'status','service_code':service,'expected_decision':expected,'decision':first['status'] if first else None,'latest_status':latest,'status_history':actions},
            'note':'Оба срока ДДС отсчитываются от поступления карточки: 30 секунд на открытие, 3 минуты на первую запись статуса и текста. Последующие работы не ограничены временем. Контакты преподаватель оценивает по журналу; поля карточки 112 не проверяются.'}


def register_dds(app,db,current):
    @app.get('/api/dds/catalog')
    def catalog(u=Depends(current),s=Depends(db)):
        assert_teacher(u)
        version=s.scalar(select(ClassifierVersion).order_by(ClassifierVersion.id.desc()))
        types=list(s.scalars(select(IncidentType).where(IncidentType.version_id==version.id))) if version else []
        counts={}
        for t in types:counts[t.category]=counts.get(t.category,0)+1
        sources=teacher_sources(s,u,[])
        return {'categories':[{'name':name,'count':count,'student_cards':sum(c.scenario_snapshot.get('category',sc.category)==name for r,c,sc in sources)} for name,count in counts.items()],
                'student_cards':len(sources),'variants':VARIANTS,'services':directory(s)}

    @app.post('/api/dds/exercises')
    def build(x:BuildIn,u=Depends(current),s=Depends(db)):
        assert_teacher(u)
        if x.norm_seconds!=180:raise HTTPException(422,'Для новых карточек ДДС первая запись должна быть внесена за 3 минуты с поступления')
        from app.generation import generate_local
        version=s.scalar(select(ClassifierVersion).order_by(ClassifierVersion.id.desc()))
        types=list(s.scalars(select(IncidentType).where(IncidentType.version_id==version.id))) if version else []
        available={t.category for t in types}
        if set(x.categories)-available:raise HTTPException(422,'Неизвестная категория событий')
        types=[t for t in types if not x.categories or t.category in x.categories]
        students=teacher_sources(s,u,x.categories)
        if x.source in ('students','mixed') and not students:raise HTTPException(422,'В выбранных категориях нет завершённых карточек ваших студентов. Сначала проведите занятие 112 или выберите системные карточки.')
        if x.source in ('generated','mixed') and not types:raise HTTPException(422,'Нет типов ЕКП для выбранных категорий')
        names=directory(s);ids=[];variants=list(dict.fromkeys(x.variants))
        for index in range(x.count):
            origin='students' if x.source=='students' or (x.source=='mixed' and index%2) else 'generated'
            if origin=='students':
                run,context,base=secrets.choice(students)
                initial=CardIn.model_validate(run.answers).model_dump()
                expected=context.scenario_snapshot.get('expected',base.expected)
                gold=deepcopy(initial)
                for key in FIELDS:
                    reference=initial['description'] if key=='description' else expected.get(key)
                    if reference is not None:gold[key]=reference
                gold['classifier_ids']=expected.get('classifier_ids',initial['classifier_ids'])
                caller_text=context.scenario_snapshot.get('caller_text',base.caller_text)
                category=context.scenario_snapshot.get('category',base.category)
                source={'kind':'dds','origin':'student','origin_run_id':run.id,'origin_scenario_id':base.id}
            else:
                t=secrets.choice(types)
                generated,provenance=generate_local({**t.data,'code':t.code,'title':t.title},{},'basic')
                from app.workflows import resolved_card
                gold=resolved_card(s,CardIn(incident_type=t.title,classifier_ids=[t.id],address=generated.address,description=generated.expected_description,
                                           caller_name=generated.caller_name,caller_phone=generated.caller_phone,aon=generated.aon,on_site_phone=generated.on_site_phone,
                                           flags=generated.flags,victims=generated.flags.get('victims',False)))
                gold=CardIn.model_validate(gold).model_dump()
                initial=deepcopy(gold);caller_text=generated.caller_text;category=t.category
                source={'kind':'dds','origin':'generated','classifier_code':t.code,'generation':provenance}
            variant=variants[index%len(variants)]
            # New DDS lessons use the received card as read-only source. Only
            # routing variants affect the service's accept/reject decision.
            if variant=='clean':initial=deepcopy(gold)
            if variant in ('typos','data','mixed'):variant='clean'
            damaged=damage(initial,variant,names)
            title=f'{x.title.strip()} · {index+1}'
            scenario=Scenario(title=title,category=category,caller_text=caller_text,
                              expected={**gold,'operator_comment':gold['description'],'norm_seconds':x.norm_seconds,'dds_gold':gold,'dds_variant':variant},created_by=u.id)
            s.add(scenario);s.flush()
            s.add(ScenarioSettings(scenario_id=scenario.id,published=True,mode='dds',difficulty='basic' if variant=='clean' else 'complex' if variant=='mixed' else 'advanced',
                                   initial_card=received_card(damaged,names),source=source))
            ids.append(scenario.id)
        s.add(Audit(user_id=u.id,action='dds.exercises.create',details={'scenario_ids':ids,'source':x.source,'categories':x.categories,'variants':variants}))
        s.commit();return {'scenario_ids':ids,'count':len(ids)}

    @app.put('/api/dds/exercises/{scenario_id}')
    def edit_exercise(scenario_id:int,x:ExerciseEditIn,u=Depends(current),s=Depends(db)):
        scenario=s.get(Scenario,scenario_id)
        if not scenario:raise HTTPException(404)
        assert_editable(s,u,scenario)
        if x.norm_seconds is not None and x.norm_seconds!=180:raise HTTPException(422,'Норматив первой записи ДДС фиксирован: 3 минуты с поступления')
        setting=s.get(ScenarioSettings,scenario_id)
        if not setting or setting.mode!='dds':raise HTTPException(422,'Не карточка проверки ДДС')
        validate_services(s,x.card)
        setting.initial_card=received_card(x.card.model_dump(),directory(s))
        if x.title is not None:
            if not x.title.strip():raise HTTPException(422,'Введите название')
            scenario.title=x.title.strip()
        if x.reference_text is not None:
            if not x.reference_text.strip():raise HTTPException(422,'Введите сведения для сверки')
            scenario.caller_text=x.reference_text.strip()
        if x.reference_card is not None:
            validate_services(s,x.reference_card)
            gold=x.reference_card.model_dump()
            if not gold['description'].strip():raise HTTPException(422,'Заполните описание эталона')
            scenario.expected={**scenario.expected,**gold,'operator_comment':gold['description'],'dds_gold':gold}
        if x.norm_seconds is not None:scenario.expected={**scenario.expected,'norm_seconds':x.norm_seconds}
        s.add(Audit(user_id=u.id,action='dds.exercise.edit',details={'scenario_id':scenario_id}));s.commit();return {'id':scenario_id}

    @app.put('/api/dds/runs/{run_id}/validation')
    def validate(run_id:int,x:CorrectionIn,u=Depends(current),s=Depends(db)):
        if u.role!='student':raise HTTPException(403)
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        if not context or context.scenario_snapshot.get('mode')!='dds':raise HTTPException(422,'Не упражнение проверки ДДС')
        if context.scenario_snapshot.get('dds_workflow')=='status':raise HTTPException(409,'Поля карточки 112 диспетчер ДДС не изменяет')
        if x.revision!=context.revision:raise HTTPException(409,'Карточка изменена в другой вкладке. Обновите данные.')
        if not x.comment.strip():raise HTTPException(422,'Опишите принятые меры')
        if x.verdict in ('corrected','clarification') and not x.findings.strip():raise HTTPException(422,'Опишите ошибки или сведения, которые нужно уточнить')
        validate_services(s,x.card)
        original=context.scenario_snapshot['dds_original']
        data=deepcopy(run.answers)
        for key in FIELDS:data[key]=x.card.model_dump()[key]
        data['operator_comment']=x.comment.strip()
        run.answers=received_card(data,directory(s));context.revision+=1
        event={'verdict':x.verdict,'findings':x.findings.strip(),'comment':x.comment.strip(),'changes':[{'field':k,'label':LABELS[k],'before':original.get(k),'after':run.answers.get(k)} for k in changed_fields(original,run.answers)],'revision':context.revision}
        s.add(CardEvent(run_id=run.id,user_id=u.id,kind='dds.validation',data=event));s.add(Audit(user_id=u.id,action='dds.validation',details={'run_id':run.id,'revision':context.revision,'verdict':x.verdict}))
        s.commit()
        from app.workflows import run_payload
        return run_payload(s,run,context)

    @app.post('/api/dds/runs/{run_id}/handoff')
    def handoff(run_id:int,x:HandoffIn,u=Depends(current),s=Depends(db)):
        if u.role!='student':raise HTTPException(403)
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        if not context or context.scenario_snapshot.get('mode')!='dds':raise HTTPException(422,'Не упражнение ДДС')
        if context.scenario_snapshot.get('dds_workflow')=='status':raise HTTPException(409,'Диспетчер ДДС ведёт статусы своей службы; контакты с бригадой и заявителем фиксируются в журнале действий')
        if not any(e.kind=='dds.validation' for e in events_for(s,run)):raise HTTPException(409,'Сначала сохраните проверку карточки')
        if x.service not in run.answers['services']:raise HTTPException(422,'Служба отсутствует в проверенном списке оповещения')
        if not x.receiver.strip() or not x.message.strip():raise HTTPException(422,'Укажите получателя и переданную информацию')
        if context.scenario_snapshot.get('require_sip') and not x.call_id:raise HTTPException(409,'В этом занятии обязателен SIP-звонок; текстовая симуляция не заменяет вызов')
        transport='Текстовая симуляция'
        if x.call_id:
            call=s.get(VoipCall,x.call_id)
            prepared=next((e for e in events_for(s,run) if e.kind=='dds.call.prepared' and e.data.get('call_id')==x.call_id),None)
            if not call or call.run_id!=run.id or not prepared or prepared.data['service']!=x.service:raise HTTPException(422,'Звонок не относится к этой карточке и службе')
            if not call.answered_at:raise HTTPException(409,'SIP-звонок ещё не принят службой')
            if call.state in ('queued','ringing','answered'):raise HTTPException(409,'Сначала завершите SIP-разговор')
            transport='SIP'
        data={**x.model_dump(),'receiver':x.receiver.strip(),'message':x.message.strip(),'transport':transport}
        s.add(CardEvent(run_id=run.id,user_id=u.id,kind='dds.handoff',data=data));s.add(Audit(user_id=u.id,action='dds.handoff',details={'run_id':run.id,'service':x.service,'transport':transport,'outcome':x.outcome}))
        s.commit()
        from app.workflows import run_payload
        return run_payload(s,run,context)


def validate_services(s,card):
    if set(card.services)-set(directory(s)):raise HTTPException(422,'Выберите службы из справочника')
    for identifier in card.classifier_ids:
        if not s.get(IncidentType,identifier):raise HTTPException(422,'Неизвестный класс ЕКП')
