"""Local neural semantic assessment; service failures are explicit, rules remain available."""
import os, re
import httpx

def neural_similarity(expected,actual):
    try:
        with httpx.Client(timeout=8.0,trust_env=False) as client:
            response=client.post(os.getenv('ML_URL','http://ml:8091')+'/similarity',json={'expected':expected,'actual':actual});response.raise_for_status()
            result=response.json()
        value=float(result['similarity'])
        if not 0<=value<=1: raise ValueError('Invalid similarity')
        return {'status':'ok',**result}
    except (httpx.HTTPError,ValueError,KeyError) as exc:
        return {'status':'unavailable','reason':type(exc).__name__,'message':'Нейросетевая оценка недоступна; сохранена правиловая первичная оценка.'}

def apply_neural(report,expected,actual,criterion,maximum):
    result=neural_similarity(expected,actual);report['ml']=result
    report['rules_score']=report['score'];report['rules_parts']=dict(report['parts'])
    if result['status']!='ok': return report
    similarity=result['similarity']
    conflicts=critical_conflicts(expected,actual)
    result['critical_conflicts']=conflicts
    points=maximum if similarity>=.80 else round(maximum*.67) if similarity>=.60 else round(maximum*.33) if similarity>=.40 else 0
    if conflicts:points=0
    report['parts'][criterion]=points;report['score']=sum(report['parts'].values());report['ml']['thresholds']=[.4,.6,.8]
    report['errors']=[x for x in report['errors'] if not ('ключевых сведений' in x or x.startswith(criterion+':'))]
    report['errors'].extend(conflicts)
    if points<maximum:report['errors'].append('Нейросетевое сравнение: содержание '+criterion.lower()+' недостаточно близко к утверждённому эталону.')
    report['note']='Первичная оценка: проверка полей/регламента и локальное нейросетевое сравнение текста. Сходство не гарантирует фактическую верность; окончательный разбор выполняет преподаватель.'
    return report


def critical_conflicts(expected,actual):
    patterns={
        'дыхание':(r'не\s+дышит|дыхания\s+нет|нет\s+дыхания|дыхание\s+отсутствует',r'(?<!не )\bдышит\b|дыхание\s+сохранено|дыхание\s+есть'),
        'сознание':(r'без\s+сознания|потерял[аи]?\s+сознание|сознания\s+нет',r'\bв\s+сознании\b'),
        'открытый огонь':(r'огня\s+нет|нет\s+огня|пламени\s+нет',r'открытое\s+пламя|\bгорит\b'),
    }
    errors=[]
    for name,(negative,positive) in patterns.items():
        def values(text):
            text=text.lower();negative_match=bool(re.search(negative,text));clean=re.sub(negative,' ',text)
            return ({False} if negative_match else set())|({True} if re.search(positive,clean) else set())
        reference=values(expected);response=values(actual)
        if len(response)>1 or (len(reference)==1 and response and reference!=response):errors.append('Противоречие в ключевом факте: '+name+'. Требуется разбор преподавателем.')
    return errors
