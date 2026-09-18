import httpx
from app.grammar import check_grammar


def test_sentence_capitalization_is_ignored_but_spelling_feedback_remains(monkeypatch):
    submitted={}
    def post(self,url,data):
        submitted.update(data)
        def match(rule,message):return {'message':message,'offset':0,'length':8,'rule':{'id':rule},'replacements':[{'value':'Передано'}]}
        return httpx.Response(200,request=httpx.Request('POST',url),json={'matches':[match('UPPERCASE_SENTENCE_START','Заглавная буква'),match('MORFOLOGIK_RULE_RU_RU','Орфографическая ошибка')]})
    monkeypatch.setattr(httpx.Client,'post',post)
    result=check_grammar('передано дежурному')
    assert submitted['language']=='ru-RU' and submitted['disabledRules']=='UPPERCASE_SENTENCE_START'
    assert [m['rule'] for m in result['matches']]==['MORFOLOGIK_RULE_RU_RU']
