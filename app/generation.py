"""Local scenario generation: persistent jobs, server-bound classifier, teacher approval."""
import os,json,threading,re,secrets,time
import httpx
from sqlalchemy import select
from fastapi import Depends,HTTPException
from pydantic import BaseModel,Field,ValidationError
from app.models import AiJob,IncidentType,Material,Audit
from app.classifier import resolve_rules
from app import generation_catalog as catalog
from app.scenario_profiles import manifest, build_profile, case_contexts
from app.speech_text import spoken_text
from app.scenario_locations import METRO_STATIONS, location_kind, transport_address
from app.caller_variation import INTRODUCTIONS, complex_presentation, vehicle_number
recent_lock=threading.Lock()
recent_choices={}

def varied_choice(key,values):
    with recent_lock:
        recent=recent_choices.get(key,[])
        candidates=[value for value in values if value not in recent] or list(values)
        value=secrets.choice(candidates)
        recent_choices[key]=(recent+[value])[-min(4,len(values)-1):] if len(values)>1 else []
        return value
class GenerateIn(BaseModel):
    classifier_id:int=Field(gt=0)
    difficulty:str=Field(default='basic',pattern='^(basic|advanced|complex)$')
    material_id:int|None=None
class GeneratedText(BaseModel):
    title:str=Field(min_length=1,max_length=300)
    caller_text:str=Field(min_length=1,max_length=10000)
    address:str=Field(default='',max_length=1000)
    expected_description:str=Field(min_length=1,max_length=5000)
    caller_name:str=Field(min_length=3,max_length=200)
    caller_phone:str=Field(default='',max_length=100)
    aon:str=Field(min_length=6,max_length=100)
    on_site_phone:str=Field(default='',max_length=100)
    flags:dict[str,bool]=Field(default_factory=dict)

class ScenarioDraft(BaseModel):
    title:str=Field(min_length=1,max_length=300)
    caller_text:str=Field(min_length=10,max_length=6000)
    expected_description:str=Field(min_length=1,max_length=3000)


def fictional_phone(exclude=()):
    while True:
        digits='9'+''.join(str(secrets.randbelow(10)) for _ in range(9))
        phone=f'+7 {digits[:3]} {digits[3:6]} {digits[6:8]} {digits[8:]}'
        if phone not in exclude:
            return phone


def fictional_name(female=None):
    female=secrets.choice((False,True)) if female is None else female
    gender='female' if female else 'male'
    pools=(catalog.FEMALE_SURNAMES,catalog.FEMALE_NAMES,catalog.FEMALE_PATRONYMICS) if female else (
           catalog.MALE_SURNAMES,catalog.MALE_NAMES,catalog.MALE_PATRONYMICS)
    return ' '.join(varied_choice(gender+str(index),pool) for index,pool in enumerate(pools))


def scenario_contacts():
    aon=fictional_phone()
    mode=secrets.choice(('missing','same','different'))
    caller_phone='' if mode=='missing' else aon if mode=='same' else fictional_phone((aon,))
    return caller_phone,aon


LOCATION_WORDS={'квартира':r'\bквартир', 'гараж':r'гараж', 'автомобиль':r'автомашин|автомобил',
                'лифт':r'лифт', 'частный дом':r'частн\w* дом',
                'магазин':r'магазин', 'офис':r'офис', 'склад':r'склад', 'помещение':r'помещени'}


def scenario_location(topic,source):
    situation=source.get('situation','') if isinstance(source,dict) else ''
    for value in (situation,source.get('address','') if isinstance(source,dict) else ''):
        if not isinstance(value,str):
            continue
        for location,pattern in LOCATION_WORDS.items():
            if re.search(pattern,value,re.I):
                return location
    title=topic.get('title','')
    if re.search(r'вскрыт|вскрыва',title,re.I) and not re.search(LOCATION_WORDS['автомобиль'],title,re.I):
        features=' '.join(value for value in topic.get('features',[]) if isinstance(value,str))
        options=[location for location in ('квартира','гараж','помещение')
                 if re.search(LOCATION_WORDS[location],title+' '+features,re.I)]
        if options:
            location=secrets.choice(options)
            return secrets.choice(('магазин','офис','склад','помещение')) if location=='помещение' else location
    for location,pattern in LOCATION_WORDS.items():
        if re.search(pattern,title,re.I):
            return location
    return None


def scenario_address(source,location=None,city=None):
    # В исходных билетах есть парки, станции, трассы и ориентиры без номера дома.
    # Сохраняем место события; наличие буквальных «г.» и «дом» не требуется.
    address=source.get('address') if isinstance(source,dict) else None
    if isinstance(address,str) and address.strip():
        return re.sub(r'\s+',' ',address).strip()
    city=city or varied_choice('city',catalog.CITIES)
    street=varied_choice('street',catalog.STREETS)
    address=f'город {city}, улица {street}, дом {secrets.randbelow(90)+1}'
    if location=='квартира' and not (isinstance(source,dict) and source.get('situation')):
        detail=secrets.choice(('full','apartment','floor','unknown'))
        entrance=secrets.randbelow(4)+1
        floor=secrets.randbelow(9)+1
        apartment=(entrance-1)*36+(floor-1)*4+secrets.randbelow(4)+1
        if detail in ('full','apartment'):
            address+=f', квартира {apartment}'
        if detail=='full':
            address+=f', подъезд {entrance}'
        if detail in ('full','floor'):
            address+=f', этаж {floor}'
    return address


def location_issues(draft,location,source,address):
    if not location:
        return []
    issues=[]
    text=' '.join((draft.title,draft.caller_text,draft.expected_description)).lower()
    if not re.search(LOCATION_WORDS[location],draft.caller_text,re.I):
        issues.append(f'В caller_text явно назови объект события: {location}')
    situation=source.get('situation','') if isinstance(source,dict) else ''
    source_text=situation if isinstance(situation,str) else ''
    # ЕКП перечисляет альтернативы объектов. В одном сценарии они не смешиваются.
    conflicting={'квартира':('гараж','магазин','офис','склад'),
                 'гараж':('квартира','магазин','офис'),
                 'автомобиль':('квартира','гараж'),
                 'магазин':('квартира','гараж'),'офис':('квартира','гараж'),'склад':('квартира','гараж')}
    for other in conflicting.get(location,()):
        if re.search(LOCATION_WORDS[other],text,re.I) and not re.search(LOCATION_WORDS[other],source_text,re.I):
            issues.append(f'Объект события — {location}; убери подмену объекта на {other}')
    # Модель может повторить адрес, но не менять известные номера или придумывать неизвестные.
    number_patterns=((r'\bквартир\w*\s*(?:№\s*)?(\d+)',),
                     (r'\bэтаж\w*\s*(\d+)',r'\b(\d+)[-\s]*(?:й|ый|ой|ем|ом|м|му)?\s*этаж'),
                     (r'\bподъезд\w*\s*(\d+)',r'\b(\d+)[-\s]*(?:й|ый|ой|ом|м)?\s*подъезд'))
    for patterns in number_patterns:
        allowed={number for pattern in patterns for number in re.findall(pattern,address+' '+source_text,re.I)}
        actual={number for pattern in patterns for number in re.findall(pattern,text,re.I)}
        for number in actual:
            if number not in allowed:
                issues.append('Не придумывай и не меняй номер квартиры, этаж или подъезд; используй только место_события и исходный материал')
                break
    return list(dict.fromkeys(issues))


def draft_issues(draft,caller_name=''):
    issues=[]
    if re.search(r'\b(?:ЕКП|классификатор)\b|\bкод\s*[:№]?\s*\d',draft.caller_text,re.I):
        issues.append('убери внутренние коды и названия классификатора из caller_text')
    if any(word in draft.caller_text.lower() for word in ('дым и пух','мне кажется аварийн','я не уверен что это мошенник')):
        issues.append('перепиши неправдоподобные формулировки caller_text')
    if caller_name:
        female=caller_name.endswith(('овна','евна','ична'))
        verbs={'заметил':'заметила','увидел':'увидела','услышал':'услышала','пришёл':'пришла',
               'пришел':'пришла','обнаружил':'обнаружила','подошёл':'подошла','подошел':'подошла',
               'позвонил':'позвонила','решил':'решила','был':'была','стоял':'стояла'}
        wrong=list(verbs) if female else list(set(verbs.values()))
        if re.search(r'\bя\s+(?:(?:не|уже|сейчас)\s+|только что\s+)?(?:'+'|'.join(wrong)+r')\b',draft.caller_text,re.I):
            issues.append('Согласуй глаголы от первого лица с полом заявителя: '+('женский' if female else 'мужской'))
    return issues


def intrusion_draft(topic,source,location,difficulty='basic'):
    if isinstance(source,dict) and source.get('situation'):
        return None
    title=topic.get('title','')
    if not re.search(r'вскрыт|вскрыва',title,re.I) or location not in ('квартира','гараж','автомобиль','помещение','магазин','офис','склад'):
        return None
    ongoing=bool(re.search(r'вскрыва',title,re.I))
    observations={
        'квартира':(
            ('Вижу, что дверь квартиры приоткрыта, а замок повреждён.',
             'У двери квартиры вижу следы взлома на замке. Дверь открыта.'),
            ('Двое незнакомых мне людей пытаются вскрыть дверь квартиры. Один возится с замком, другой смотрит по сторонам.',
             'Слышу удары по двери квартиры. Вижу незнакомого человека, который пытается поддеть замок инструментом.')),
        'гараж':(
            ('У гаража вижу сломанный замок и приоткрытые ворота.',),
            ('Вижу, как незнакомый человек пытается вскрыть ворота гаража. Он поддевает замок инструментом.',)),
        'автомобиль':(
            ('У припаркованного автомобиля вижу повреждённый дверной замок и приоткрытую дверь.',
             'Вижу, что у припаркованного автомобиля разбито боковое стекло, а дверь открыта.'),
            ('Вижу, как незнакомый человек пытается вскрыть дверь припаркованного автомобиля. Он возится с замком и дёргает ручку.',)),
        'помещение':(
            ('Вижу, что дверь помещения открыта, а на замке есть следы взлома.',),
            ('Вижу, как незнакомый человек пытается вскрыть дверь помещения. Он поддевает замок инструментом.',)),
    }
    for specific,genitive in (('магазин','магазина'),('офис','офиса'),('склад','склада')):
        observations[specific]=tuple(tuple(line.replace('помещения',genitive) for line in variants)
                                     for variants in observations['помещение'])
    for specific,genitive in (('квартира','квартиры'),('магазин','магазина'),('офис','офиса'),
                              ('склад','склада'),('помещение','помещения')):
        completed,active=observations[specific]
        observations[specific]=(completed+(
            f'Замок на двери {genitive} сломан. Сама дверь неплотно закрыта.',
            f'У входа вижу повреждённый замок. Дверь {genitive} приоткрыта.',
            f'Дверь {genitive} открыта, на замке и дверной раме видны повреждения.'),active+(
            f'У входа в {specific if specific!="квартира" else "квартиру"} стоит незнакомый человек. Он несколько раз поддевает дверной замок металлическим инструментом.',
            f'Слышен скрежет у двери {genitive}. Незнакомый человек дёргает ручку и возится с замком.'))
    observed=varied_choice('intrusion-'+location+str(ongoing),observations[location][int(ongoing)])
    if ongoing:
        ending=secrets.choice(('Это происходит прямо сейчас. Я наблюдаю со стороны.',
                              'Попытка вскрытия продолжается прямо сейчас. В происходящее не вмешиваюсь.'))
        if difficulty!='basic':
            ending+=' Лиц не могу разглядеть, описать людей подробно не получится.'
        if difficulty=='complex':
            ending+=' Когда началась эта попытка, не знаю.'
    else:
        ending=secrets.choice(('Внутрь не захожу. Не знаю, что пропало.',
                              'Остаюсь снаружи, ничего не трогаю. Есть ли пропажа, пока неизвестно.'))
        if difficulty!='basic':
            ending+=' Есть ли кто-то внутри, не знаю.'
        if difficulty=='complex':
            ending+=' Как давно это произошло и кто здесь был, не могу сказать.'
    summary=observed
    if difficulty=='complex':
        observed=complex_presentation(observed,varied_choice)
    title=f'Попытка вскрытия: {location}' if ongoing else f'Обнаружены следы вскрытия: {location}'
    return ScenarioDraft(title=title,caller_text=observed+' '+ending,expected_description=summary)


def vehicle_observation(longstanding=False):
    profiles=tuple(key for key in catalog.VEHICLE_CONDITIONS if not longstanding or key!='intact')
    profile=varied_choice('vehicle-condition',profiles)
    main,extras=catalog.VEHICLE_CONDITIONS[profile]
    primary=varied_choice('vehicle-primary-'+profile,main)
    secondary=varied_choice('vehicle-secondary-'+profile,extras)
    return primary+' '+secondary


def abandoned_vehicle_draft(topic,source,difficulty):
    if isinstance(source,dict) and source.get('situation'):
        return None
    context=topic.get('title','')+' '+' '.join(value for value in topic.get('features',[]) if isinstance(value,str))
    if not re.search(r'брош\w*',context,re.I) or not re.search(r'автомашин|автомобил|автохлам',context,re.I):
        return None
    vehicle=varied_choice('vehicle',tuple(f'{color} {kind}' for color in ('белый','серый','синий','чёрный','красный','зелёный','серебристый','бежевый')
                                         for kind in ('седан','хэтчбек','универсал','внедорожник','минивэн')))
    if re.search(r'трамвай|троллейбус',context,re.I):
        modes=[]
        if re.search('трамвай',context,re.I):
            modes.append(('На трамвайных путях стоит автомобиль', 'Трамвай остановился перед ним и не может проехать.',
                          'Автомобиль препятствует движению трамвая.'))
        if re.search('троллейбус',context,re.I):
            modes.append(('На полосе движения троллейбуса стоит автомобиль', 'Троллейбус остановился перед ним и не может проехать.',
                          'Автомобиль препятствует движению троллейбуса.'))
        opening,observation,summary=secrets.choice(modes)
        caller_text=f'{opening}, {vehicle}. {observation} Водителя рядом не вижу. Как давно машина здесь, не знаю.'
        number=vehicle_number(caller_text,varied_choice,explicit_vehicle=True)
        caller_text+=' '+number if number else ''
        if difficulty=='complex':
            caller_text=complex_presentation(caller_text,varied_choice)+' Кто оставил машину, не знаю.'
        return ScenarioDraft(title='Брошенный автомобиль: помеха общественному транспорту',
                             caller_text=caller_text,expected_description=summary+' Водителя рядом не видно. '+number)
    longstanding=bool(re.search(r'давно|автохлам',context,re.I))
    unit=secrets.choice(('days','weeks','months'))
    duration=varied_choice('vehicle-duration',catalog.vehicle_duration_options(unit,longstanding))
    observation=vehicle_observation(longstanding)
    opening=secrets.choice((f'У дома стоит автомобиль, {vehicle}.',f'Хочу сообщить про автомобиль у дома — {vehicle}.',
                            f'Рядом с домом без движения стоит {vehicle}.',f'Здесь на парковке есть автомобиль, {vehicle}.'))
    caller_text=(f'{opening} Вижу его на одном и том же месте {duration}. '
                 f'{observation} Людей в салоне и рядом сейчас не вижу. Кто владелец, не знаю.')
    number=vehicle_number(caller_text,varied_choice,explicit_vehicle=True)
    caller_text+=' '+number if number else ''
    if difficulty=='complex':
        caller_text=complex_presentation(caller_text,varied_choice)+' Кто и когда оставил автомобиль здесь, не знаю.'
    return ScenarioDraft(title='Брошенный автомобиль: давно стоит' if longstanding else 'Брошенный автомобиль',
                         caller_text=caller_text,
                         expected_description=f'Автомобиль стоит на одном месте {duration}. {observation} Людей рядом не видно, владелец неизвестен. '+number)


def reporter_gender(text,caller_name):
    if not caller_name.endswith(('овна','евна','ична')):
        return text
    roles={'дежурный':'дежурная','сотрудник':'сотрудница','собственник':'собственница','родственник':'родственница'}
    return re.sub(r'\b(я|как) (дежурный|сотрудник|собственник|родственник)\b',
                  lambda match:match[1]+' '+roles[match[2].lower()],text,flags=re.I)


def compose_generated(draft,address,caller_name,caller_phone,aon):
    intro=varied_choice('caller-introduction',INTRODUCTIONS).format(name=caller_name)
    place=secrets.choice((f'Место происшествия: {address}.',f'Адрес такой: {address}.',f'Это по адресу: {address}.')) if address else ''
    body=spoken_text(reporter_gender(draft.caller_text.strip(),caller_name))
    caller_text=' '.join(part for part in secrets.choice(((intro,place,body),(intro,body,place))) if part)
    if caller_phone:
        caller_text+=f' Для обратной связи мой телефон {caller_phone}.'
    caller_text+=f' Номер, с которого звоню: {aon}.'
    return GeneratedText(title=draft.title.strip(),caller_text=caller_text,address=address,
                         expected_description=reporter_gender(draft.expected_description.strip(),caller_name),caller_name=caller_name,
                         caller_phone=caller_phone,aon=aon,on_site_phone='')


def profile_address(source,location,setting,topic,facts=''):
    if isinstance(source,dict) and isinstance(source.get('address'),str) and source['address'].strip():
        return scenario_address(source,location)
    kind=location_kind(topic,setting,facts)
    if kind in ('none','unknown'):
        return ''
    transport=transport_address(kind,facts,varied_choice)
    if transport:
        return transport
    if kind in ('forest','water','industrial','dam','open-ground','road-tunnel','bridge'):
        city=varied_choice('city',catalog.CITIES)
        landmarks={
            'forest':('лесной массив за городской чертой, у въезда со стороны дороги','лесной массив за городской чертой, развилка у информационного щита','лесной массив за городской чертой, тропа от подъездной дороги'),
            'water':('берег реки, у пешеходного моста','берег реки, рядом со спуском к воде','водоём, со стороны подъездной дороги'),
            'industrial':('промышленная площадка, у главной проходной','производственная территория, корпус 2','промышленная площадка, возле грузовых ворот'),
            'dam':('плотина на водоёме, у водосброса','плотина на водоёме, со стороны подъездной дороги','гидротехническое сооружение, у затвора'),
            'open-ground':('открытая местность за городской чертой, возле подъездной дороги','открытая местность за городской чертой, у края поля'),
            'road-tunnel':('автомобильный тоннель, у въезда','автомобильный тоннель, внутри со стороны въезда'),
            'bridge':('автомобильный мост через реку, со стороны подъездной дороги','автомобильный мост через реку, у начала моста'),
        }
        if kind=='bridge' and 'эстакада' in facts.lower():
            return f'город {city}, автомобильная эстакада, у съезда'
        if kind=='bridge' and 'пешеход' in facts.lower():
            return f'город {city}, пешеходный мост через реку, со стороны подхода'
        if kind=='forest' and 'в парке' in facts.lower():
            return f'город {city}, парк, '+varied_choice('park-place',('у входа со стороны парковки','развилка у информационного щита','дальняя тропа за входом'))
        return f'город {city}, '+varied_choice('landmark-'+kind,landmarks[kind])
    return scenario_address(source,location)


def material_facts(source):
    """Preserve ticket facts; replace embedded identities/contacts rather than inventing."""
    situation=re.sub(r'\s+',' ',source.get('situation','')).strip()
    # Imported OCR contacts can contain spaces inside the mobile prefix.
    situation=re.sub(r'(?<!\d)(?:\+?7[\s-]*|8[\s-]*)?9(?:[\s()-]*\d){9}(?!\d)','',situation)
    situation=re.sub(r'\b[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]*(?:ович|евич|ич|овна|евна|ична)\b','',situation)
    situation=re.sub(r'\b(?:ФИО|телефон|тел\.)\s*[:—-]?\s*(?=[,.;]|$)','',situation,flags=re.I)
    situation=re.sub(r'\s+([,.;])',r'\1',situation)
    situation=re.sub(r'[,;]\s*[,;.]', '.',situation).strip(' ,;.')
    if len(situation)<10:
        raise ValueError('В исходном билете недостаточно обстоятельств события')
    if len(situation)>2800:
        raise ValueError('Исходный билет слишком длинный: выделите обстоятельства события')
    return situation+'.'


def material_flags(facts,defaults):
    flags=dict(defaults)
    value=facts.lower().replace('ё','е')
    if re.search(r'пострадавш\w*(?:\s+людей)?\s+нет|нет\s+пострадавш|без пострадавш|травм\w*\s+нет|ранени\w*\s+нет|никто не пострадал',value):
        flags.update(victims=False,medical_help=False)
    elif re.search(r'пострадав|ранен|травм|кровотеч|без сознания|бессознатель|отек|не может дышать',value):
        flags.update(victims=True,medical_help=True)
    if re.search(r'(?:нужна|требуется|просит) медицинск\w* помощ',value): flags['medical_help']=True
    if re.search(r'пострадавш\w*\s+(?:не на месте|ушли|уехали|увезли)|отказ\w* от (?:скорой|медицинской)',value): flags['victims_away']=True
    if re.search(r'угроз\w*(?:\s+(?:людям|жизни|здоровью))?\s+нет|нет угроз|без угроз',value): flags['life_danger']=False
    if re.search(r'не\s+газифиц|без газа|газификац\w*\s+нет',value): flags['gasified']=False
    elif re.search(r'газифиц|газовая плита|газопровод|запах\w* газа',value): flags['gasified']=True
    if re.search(r'проезд\w* (?:полностью )?перекрыт|движение\w* перекрыт',value): flags['traffic_blocked']=True
    return flags


def observed_place_flags(facts):
    value=facts.lower().replace('ё','е')
    flags={}
    if re.search(r'узел связи|телекоммуникационн',value): flags['communications_object']=True
    if 'тоннел' in value: flags['tunnel']=True
    if 'пешеходный переход' in value and 'светофор' not in value: flags['pedestrian_structure']=True
    if re.search(r'\bмост\w*\b|\bэстакад\w*\b',value): flags['vehicle_structure']=True
    if re.search(r'строительн\w* площадк|строительные леса|\bкотлован',value): flags['construction']=True
    return flags


def generate_profile(topic,source,difficulty,started):
    canonical=manifest().get(topic.get('code'))
    if canonical is None or canonical['title']!=topic['title'] or canonical['features']!=topic.get('features',canonical['features']):
        raise ValueError('Тип ЕКП изменился: требуется обновить каталог сценариев')
    topic=canonical
    p=build_profile(canonical)
    source=source if isinstance(source,dict) else {}
    situation=source.get('situation')
    if situation is not None and not isinstance(situation,str):
        raise ValueError('Обстоятельства исходного билета должны быть текстом')
    if source.get('address') is not None and not isinstance(source['address'],str):
        raise ValueError('Адрес исходного билета должен быть текстом')
    location=scenario_location(topic,source)
    if not location and p.setting=='building' and topic['code'] in ('17080100','17080200','17080300','17080400','17080500','17080700','17080800','17080900','17081000','17081101','17081102','17081103','20010100','20010200','20010300'):
        location='квартира'
    female=True if situation and re.search(r'\bмама\b|[а-яё]+(?:овна|евна|ична)\b',situation,re.I) else False if situation and (re.search(r'[а-яё]+(?:ович|евич)\b',situation,re.I) or re.match(r'\s*(?:Поругался|Увидел|Обнаружил|Услышал|Заметил)\b',situation,re.I)) else None
    name=fictional_name(female)
    callback,aon=scenario_contacts()
    if situation and situation.strip():
        facts=material_facts(source)
        flags=material_flags(facts,p.flags)
        body='Передаю сведения об обстоятельствах: '+facts
        draft=ScenarioDraft(title=topic['title'],caller_text=body,expected_description=facts)
        engine='material-template'
    else:
        draft=intrusion_draft(topic,{},location,difficulty) or abandoned_vehicle_draft(topic,{},difficulty)
        specialized=draft is not None
        flags=dict(p.flags)
        if draft is None:
            core=varied_choice('case-'+topic['code'],p.cases)
            facts=' '.join((core,*p.details)).strip()
            detail,extra_flags=varied_choice('context-'+topic['code'],case_contexts(p))
            flags.update(extra_flags)
            if detail:
                facts+=' '+detail
            if p.family.startswith('medical-') and topic['code'] not in ('22020000','22210000','22290000','22550000'):
                ages=tuple(range(8,16)) if topic['code']=='22040000' else tuple(range(20,41)) if topic['code'][2:4] in ('03','14','22','44','46') else tuple(range(18,86))
                age=varied_choice('patient-age-'+topic['code'],ages)
                facts+=f' Человеку {age} '+('год.' if age%10==1 and age%100!=11 else 'года.' if age%10 in (2,3,4) and age%100 not in (12,13,14) else 'лет.')
            body=facts
            unknown=[]
            if difficulty!='basic':
                unknown.append(varied_choice('unknown-'+topic['code'],p.unknowns))
            if difficulty=='complex':
                remaining=[item for item in p.unknowns if item not in unknown]
                if remaining:
                    unknown.append(varied_choice('unknown2-'+topic['code'],remaining))
            frames=('Звоню сообщить о ситуации. ','','Вот что здесь происходит. ','Нужна помощь. ')
            if p.family.startswith('other-') or p.family.startswith('medical-21'):
                frames=('','Обращаюсь к вам. ','Здравствуйте, передаю обращение. ')
            if difficulty=='complex':
                body=complex_presentation(body,varied_choice)
            else:
                body=varied_choice('frame-'+topic['code'],frames)+body
            body+=' '+' '.join(unknown) if unknown else ''
            draft=ScenarioDraft(title=topic['title'],caller_text=body,expected_description=facts+' '+' '.join(unknown))
        if not specialized and topic['group_code'] in ('1','2','3','4','7','11','12','14','15','16','17','18'):
            number=vehicle_number(draft.expected_description,varied_choice)
            if number:
                draft.caller_text+=' '+number
                draft.expected_description+=' '+number
        engine='scenario-template'
    address=profile_address(source,location,p.setting,canonical,draft.expected_description)
    if situation and situation.strip() and not (source.get('address') or '').strip():
        address=''
    if p.setting=='unknown' and not address:
        draft.caller_text+=' Конкретное место в сообщении не указано, адрес мне неизвестен.'
        draft.expected_description+=' Конкретный адрес неизвестен.'
    elif situation and not address:
        draft.caller_text+=' Точный адрес в передаваемых сведениях отсутствует.'
        draft.expected_description+=' Точный адрес не указан.'
    result=compose_generated(draft,address,name,callback,aon)
    flags.update(observed_place_flags(draft.expected_description))
    result.flags=flags
    return result,{'model':None,'digest':None,'engine':engine,'template_version':9,
                   'profile':p.family,'classifier_code':topic['code'],'source_used':bool(situation),
                   'needs_teacher_approval':True,'attempts':0,
                   'duration_seconds':round(time.perf_counter()-started,3)}


def generate_local(topic,source,difficulty):
    started=time.perf_counter()
    canonical=manifest().get(topic.get('code'))
    if topic.get('group_code') or (canonical and canonical['title']==topic.get('title')):
        return generate_profile(topic,source,difficulty,started)
    model=os.getenv('OLLAMA_MODEL','qwen2.5:3b')
    location=scenario_location(topic,source)
    address=scenario_address(source,location)
    caller_name=fictional_name()
    caller_phone,aon=scenario_contacts()
    grounded=intrusion_draft(topic,source,location,difficulty) or abandoned_vehicle_draft(topic,source,difficulty)
    if grounded:
        text=compose_generated(grounded,address,caller_name,caller_phone,aon)
        return text,{'model':None,'digest':None,'engine':'scenario-template','template_version':6,
                     'needs_teacher_approval':True,'attempts':0,
                     'duration_seconds':round(time.perf_counter()-started,2)}
    system=(
        'Составь учебный сценарий звонка в службу 112. Ответ только по JSON-схеме, по-русски. '
        'title — короткое название события. '
        'caller_text — 3–5 естественных предложений от первого лица ТОЛЬКО об обстоятельствах: '
        'что вижу или слышу, когда началось, кто в опасности, существенные подробности. '
        'Не пиши приветствие, ФИО, адрес, телефоны: сервер добавит их сам. '
        'Согласуй реплику с полом_заявителя: женщина говорит «я заметила», мужчина — «я заметил». '
        'объект_события — один выбранный объект: не заменяй его другим и не смешивай с альтернативами. '
        'Номер квартиры, этаж и подъезд используй только из места_события или исходного материала. '
        'Если их нет, они неизвестны заявителю; не придумывай. '
        'expected_description — одно предложение с фактами события, без канцелярских фраз. '
        'Сообщай только наблюдаемые факты: не придумывай предположения вроде «вероятно, люди спят». '
        'Сохрани обстоятельства исходного материала, включая возраст, пострадавших, этаж и ориентиры. '
        'Не раскрывай коды, ЕКП, классификатор, внутренние поля. Не угадывай службу. '
        'Не добавляй инструкции по лечению и ликвидации аварий. Не используй бессмысленные фразы.'
    )
    # Числовой код модели не нужен и раньше попадал в реплику заявителя.
    event={key:value for key,value in topic.items() if key!='code'}
    if location:
        if re.search(r'вскрыт|вскрыва',event.get('title',''),re.I):
            event['title']=(f'Попытка вскрытия: {location}' if re.search(r'вскрыва',event['title'],re.I)
                            else f'Обнаружены следы вскрытия: {location}')
        # В строках ЕКП встречаются списки «квартира помещение гараж лифт».
        # Передаём только выбранный объект, а прочие признаки сохраняем.
        features=event.get('features',[])
        event['features']=[feature for feature in features if isinstance(feature,str) and
                           not any(re.search(pattern,feature,re.I) for pattern in LOCATION_WORDS.values())]
        event['features'].append(location)
    request={'событие':event,'объект_события':location,
             'пол_заявителя':'женский' if caller_name.endswith(('овна','евна','ична')) else 'мужской',
             'требования_сложности':catalog.DIFFICULTY_GUIDANCE[difficulty],
             'стиль_реплики':secrets.choice(('Короткие разговорные фразы.','Спокойный подробный рассказ.','Начни с наиболее заметного признака события.','Начни с того, когда заявитель обнаружил событие.')),
             'сложность':difficulty,'исходный_материал':source,'место_события':address}
    messages=[{'role':'system','content':system},
              {'role':'user','content':json.dumps(request,ensure_ascii=False)}]
    attempts=0
    with httpx.Client(timeout=httpx.Timeout(180,connect=10),trust_env=False) as client:
        for attempts in range(1,3):
            response=client.post(os.getenv('OLLAMA_URL','http://ollama:11434')+'/api/chat',json={
                'model':model,'stream':False,'format':ScenarioDraft.model_json_schema(),
                'options':{'temperature':.25,'num_predict':600},'messages':messages})
            response.raise_for_status()
            payload=response.json()
            content=payload.get('message',{}).get('content','')
            try:
                draft=ScenarioDraft.model_validate_json(content)
                issues=draft_issues(draft,caller_name)+location_issues(draft,location,source,address)
            except ValidationError as exc:
                issues=['JSON не соответствует схеме: '+str(exc)]
            if not issues:
                break
            if attempts==2:
                raise ValueError('некачественный черновик: '+'; '.join(issues))
            messages.extend([{'role':'assistant','content':content},
                             {'role':'user','content':'Исправь конкретные ошибки: '+'; '.join(issues)}])
        # Контакты и адрес собираются один раз: в карточке и реплике всегда одинаковые значения.
        text=compose_generated(draft,address,caller_name,caller_phone,aon)
        # Метаданные модели не должны превращать уже готовый черновик в ошибку.
        digest=None
        try:
            tags=client.get(os.getenv('OLLAMA_URL','http://ollama:11434')+'/api/tags',timeout=5)
            tags.raise_for_status()
            digest=next((x.get('digest') for x in tags.json().get('models',[]) if x.get('name')==model),None)
        except (httpx.HTTPError,ValueError,TypeError):
            pass
    return text,{'model':model,'digest':digest,'engine':'ollama','needs_teacher_approval':True,
                 'attempts':attempts,'duration_seconds':round(time.perf_counter()-started,2)}

def register_generation(app,db,current,factory):
    def worker(identifier,resumed=False):
        with factory() as s:
            job=s.get(AiJob,identifier)
            if not job or job.status not in ('queued','running'):
                return
            job.status='running';job.error=None
            if resumed:
                s.add(Audit(user_id=job.teacher_id,action='ai.scenario.resumed',details={'job_id':job.id}))
            s.commit()
            try:
                incident=s.get(IncidentType,job.input['classifier_id']);material=s.get(Material,job.input.get('material_id')) if job.input.get('material_id') else None
                generated,provenance=generate_local({**incident.data,'code':incident.code,'title':incident.title},material.content if material else {},job.input['difficulty'])
                services=list(dict.fromkeys(x['code'] for x in resolve_rules(incident.data['rules'],generated.flags)))
                job.result={'title':generated.title,'category':incident.data.get('category',incident.title),'caller_text':generated.caller_text,'expected':{'incident_type':incident.title,'classifier_ids':[incident.id],'address':generated.address,'services':services,'operator_comment':generated.expected_description,'norm_seconds':30,'caller_name':generated.caller_name,'caller_phone':generated.caller_phone,'aon':generated.aon,'on_site_phone':generated.on_site_phone,'questionnaire_answers':__import__('app.questionnaires',fromlist=['expected_answers']).expected_answers(incident.data,generated.flags,generated.caller_text),'flags':generated.flags,**{key:generated.flags.get(key,False) for key in ('victims','access_blocked','life_danger')}},'source_material_id':material.id if material else None,'provenance':provenance,'published':False,'difficulty':job.input['difficulty']};job.status='done'
                s.add(Audit(user_id=job.teacher_id,action='ai.scenario.generated',details={'job_id':job.id,**provenance}));s.commit()
            except Exception as exc:
                job.status='failed';job.error=type(exc).__name__+': '+str(exc)[:1000];s.add(Audit(user_id=job.teacher_id,action='ai.scenario.failed',details={'job_id':job.id,'reason':type(exc).__name__}));s.commit()
    @app.on_event('startup')
    def recover_generation():
        with factory() as s:
            identifiers=list(s.scalars(select(AiJob.id).where(AiJob.status.in_(('queued','running'))).order_by(AiJob.id)))
        if identifiers:
            def resume_pending():
                for identifier in identifiers:
                    worker(identifier,resumed=True)
            threading.Thread(target=resume_pending,daemon=True).start()
    @app.post('/api/ai/scenarios')
    def create(x:GenerateIn,u=Depends(current),s=Depends(db)):
        if u.role!='teacher':raise HTTPException(403)
        if not s.get(IncidentType,x.classifier_id):raise HTTPException(422,'Неизвестный тип ЕКП')
        if x.material_id and not s.get(Material,x.material_id):raise HTTPException(422,'Неизвестный исходный материал')
        job=AiJob(teacher_id=u.id,input=x.model_dump());s.add(job);s.commit();threading.Thread(target=worker,args=(job.id,),daemon=True).start();return {'id':job.id,'status':'queued'}
    @app.get('/api/ai/jobs/{identifier}')
    def read(identifier:int,u=Depends(current),s=Depends(db)):
        job=s.get(AiJob,identifier)
        if not job or u.role!='teacher' or job.teacher_id!=u.id:raise HTTPException(404)
        return {'id':job.id,'status':job.status,'result':job.result,'error':job.error}
