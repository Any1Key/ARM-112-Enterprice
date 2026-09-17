"""Versioned training questionnaires grounded in EKP and the supplied ARM screenshots."""
import re
from fastapi import HTTPException

VERSION='2026-09-17.1'
LABELS={
 1:('Место пожара','Что горит','Признаки горения'),2:('Вид ДТП','Участники / место','Обстоятельства'),
 3:('Место взрыва','Объект','Признаки'),4:('Вид угрозы','Объект','Обстоятельства'),
 5:('Объект обрушения','Часть объекта','Обстоятельства'),6:('Объект угрозы','Часть объекта','Признаки'),
 7:('Явление','Место','Обстоятельства'),8:('Вид загрязнения','Место','Признаки'),
 9:('Сооружение','Вид аварии','Обстоятельства'),10:('Объект','Вид аварии','Признаки'),
 11:('Опасное вещество / объект','Вид угрозы','Признаки'),12:('Транспортный объект','Вид происшествия','Обстоятельства'),
 13:('Место запаха газа','Объект','Признаки'),14:('Объект городского хозяйства','Вид неисправности','Признаки'),
 15:('Вид правонарушения','Место / объект','Обстоятельства'),16:('Проблема на дороге','Объект','Обстоятельства'),
 17:('Вид опасности','Место','Обстоятельства'),18:('Вид опасности для ребёнка','Место','Обстоятельства'),
 19:('Обстоятельства','Место','Признаки'),20:('Вид помощи','Кому требуется','Обстоятельства'),
 21:('Происшествие с животным','Место / объект','Обстоятельства'),22:('Причина обращения','Состояние','Обстоятельства'),
 23:('Вид обращения','Уточнение','Обстоятельства'),24:('Вид наблюдения','Объект','Обстоятельства')}
ALIASES={'101':1,'пожар':1,'дым':1,'задымление':1,'дтп':2,'взрыв':3,'104':13,'газ':13,'102':15,'103':22,'скорая':22,'бпла':24,'дрон':24}
FLAG_LABELS={'victims':'Есть пострадавшие?','life_danger':'Есть угроза людям?','access_blocked':'Есть доступ к месту?',
 'gasified':'Есть газификация?','traffic_blocked':'Перекрыто движение?','offense':'Есть правонарушение?',
 'victims_away':'Пострадавшие не на месте / отказались от скорой?', 'medical_help':'Нужна медицинская помощь?',
 'evacuation':'Нужна эвакуация?','mass_event':'Более пяти человек / опасное действие?',
 'tunnel':'Событие в тоннеле?','pedestrian_structure':'Пешеходное сооружение?',
 'vehicle_structure':'Автомобильное сооружение?','communications_object':'Объект связи?',
 'construction':'Строительство?','culture_object':'Объект культуры из перечня?', 'polygon':'Событие на полигоне?'}

def group_number(code):return int(code)//1000000

def catalog(items,groups):
    result=[]
    for raw,title in groups.items():
        group=int(raw);subset=[x for x in items if group_number(x['code'])==group]
        labels=LABELS[group]
        questions=[{'id':f'feature_{i}','label':labels[i],'type':'choice','level':i,'options':sorted({x['features'][i] for x in subset if x['features'][i]}),'required':False} for i in range(3)]
        used={f for x in subset for rule in x.get('rules',[]) for f in rule.get('condition',('',[]))[1]}
        # Questions used by service routing; unknown is distinct from no.
        used|={'victims','life_danger','access_blocked'}
        for flag in sorted(used):
            if flag not in FLAG_LABELS:continue
            questions.append({'id':flag,'label':'Доступ заблокирован?' if flag=='access_blocked' else FLAG_LABELS[flag],'type':'tri_state','required':False,'flag':flag})
        if group==1:
            questions.extend([
                {'id':'building_floors','label':'Этажность здания','type':'number','min':1,'max':200,'visible_when':{'field':'feature_0','contains':'дом'}},
                {'id':'fire_floor','label':'Этаж пожара','type':'number','min':-10,'max':200,'visible_when':{'field':'feature_0','contains':'дом'}},
                {'id':'fire_objects','label':'Где замечено горение / дым? Можно выбрать несколько','type':'multi','options':['квартира','балкон','газовая колонка','газовая плита','лифт','мусоропровод','подъезд','электросчётчик','проводка','электрощит','лестница','подвал','крыша','другое']},
            ])
        if group==3:
            questions.extend([{'id':'ignition','label':'Есть возгорание?','type':'tri_state'}, {'id':'collapse_threat','label':'Есть угроза обрушения?','type':'tri_state'}, {'id':'damage','label':'Какие разрушения видны?','type':'text'}])
        if group in (2,12,16):questions.append({'id':'vehicle_plate','label':'Госномер автомобиля, если известен','type':'text'})
        if group in (17,18,22):questions.extend([{'id':'conscious','label':'Человек в сознании?','type':'tri_state'}, {'id':'breathing','label':'Человек дышит?','type':'tri_state'}, {'id':'age','label':'Возраст, если известен','type':'number','min':0,'max':120}])
        if group==21:questions.append({'id':'animal','label':'Какое животное и как себя ведёт?','type':'text'})
        questions.append({'id':'details','label':'Дополнительные сведения со слов заявителя','type':'text'})
        result.append({'id':str(group),'title':title,'version':VERSION,'source':'ЕКП: признаки и условия служб; учебная форма, примеры 101/104/взрыв сверены со скриншотами','questions':questions,'types':[{k:x[k] for k in ('id','code','features','title','category','extra_questions') if k in x} for x in subset]})
    return result

def validate_answers(answers):
    for key,values in answers.items():
        if key not in {str(x) for x in LABELS} or not isinstance(values,dict) or len(values)>40:raise HTTPException(422,'Неизвестная опросная карта')
        allowed=set(FLAG_LABELS)|{'feature_0','feature_1','feature_2','details','building_floors','fire_floor','fire_objects','ignition','collapse_threat','damage','vehicle_plate','conscious','breathing','age','animal'}
        for field,value in values.items():
            if field not in allowed:raise HTTPException(422,'Неизвестный вопрос опросной карты')
            if field=='fire_objects' and value is not None and not isinstance(value,list):raise HTTPException(422,'Множественный ответ должен быть списком')
            if field in ('building_floors','fire_floor','age') and value is not None:
                bounds={'building_floors':(1,200),'fire_floor':(-10,200),'age':(0,120)}
                if isinstance(value,bool) or not isinstance(value,int) or not bounds[field][0]<=value<=bounds[field][1]:raise HTTPException(422,'Некорректное числовое значение опросной карты')
            if not re.fullmatch(r'[a-z_0-9]{1,50}',field):raise HTTPException(422,'Некорректное поле опросной карты')
            if isinstance(value,list):
                if len(value)>30 or any(not isinstance(v,str) or len(v)>300 for v in value):raise HTTPException(422,'Некорректный список ответов')
            elif value is not None and not isinstance(value,(str,int,float,bool)):raise HTTPException(422,'Некорректный ответ')
            elif isinstance(value,str) and len(value)>2000:raise HTTPException(422,'Слишком длинный ответ')
            if field in FLAG_LABELS or field in ('ignition','collapse_threat','conscious','breathing'):
                if value not in ('yes','no','unknown',None,''):raise HTTPException(422,'Ответ должен быть да, нет или неизвестно')
    return answers

def answer_flags(answers):
    validate_answers(answers);result={}
    for values in answers.values():
        for field,value in values.items():
            if field in FLAG_LABELS and value in ('yes','no'):
                # A yes from any selected form must not be cancelled by a no in another.
                result[field]=result.get(field,False) or value=='yes'
    return result

def expected_answers(incident,flags,text=''):
    group=str(group_number(incident['code']));values={f'feature_{i}':v for i,v in enumerate(incident.get('features',[])) if v}
    for key,value in flags.items():
        if key in FLAG_LABELS and value:values[key]='yes'
    return {group:values}

def grade_answers(report,expected,card):
    expected_q=expected.get('questionnaire_answers') or {}
    checks=[]
    for group,values in expected_q.items():
        actual=card.questionnaire_answers.get(group,{})
        for field,value in values.items():
            if isinstance(value,list):
                submitted=actual.get(field)
                ok=isinstance(submitted,list) and set(value)==set(submitted)
            else:ok=str(actual.get(field,'')).strip().casefold()==str(value).strip().casefold()
            checks.append({'card':group,'field':field,'expected':value,'actual':actual.get(field),'correct':ok})
    for field in ('victims_count','caller_status'):
        value=expected.get(field)
        if value not in (None,''):checks.append({'field':field,'expected':value,'actual':getattr(card,field),'correct':str(value).casefold()==str(getattr(card,field)).casefold()})
    if not checks:return report
    report['parts']['Смысл текста']=round(report['parts']['Смысл текста']*20/30)
    report['parts']['Опросная карта']=round(sum(x['correct'] for x in checks)/len(checks)*10)
    report['parts_max']={'Тип происшествия':20,'Адрес':10,'Службы':20,'Смысл текста':20,'Время':20,'Опросная карта':10}
    report['questionnaire_checks']=checks
    report['score']=sum(report['parts'].values())
    if any(not x['correct'] for x in checks):report['errors'].append('Опросная карта: часть сведений не заполнена или не совпадает с сообщением заявителя.')
    return report
