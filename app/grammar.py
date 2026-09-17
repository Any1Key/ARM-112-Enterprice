"""Offline Russian grammar feedback from the internal LanguageTool server."""
import os
import httpx

def check_grammar(text):
    if not text.strip():return {'status':'ok','language':'ru-RU','matches':[]}
    try:
        with httpx.Client(timeout=12,trust_env=False) as client:
            response=client.post(os.getenv('GRAMMAR_URL','http://grammar:8093')+'/v2/check',data={'language':'ru-RU','text':text});response.raise_for_status();result=response.json()
        return {'status':'ok','language':'ru-RU','engine':result.get('software',{}),'matches':[{'message':x['message'],'offset':x['offset'],'length':x['length'],'rule':x['rule']['id'],'replacements':[y['value'] for y in x.get('replacements',[])[:5]],'context':x.get('context',{})} for x in result['matches']]}
    except (httpx.HTTPError,KeyError,ValueError):return {'status':'unavailable','message':'Локальная проверка грамматики недоступна.'}
