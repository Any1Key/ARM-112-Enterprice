import httpx
from app import ml

def test_neural_assessment_keeps_rule_evidence_and_distinguishes_service_failure(monkeypatch):
    def report():return {'score':100,'parts':{'Смысл текста':30,'Регламент':70},'errors':[]}
    monkeypatch.setattr(ml,'neural_similarity',lambda expected,actual:{'status':'ok','similarity':.91,'model':'local-test-model','revision':'revision-1'})
    assessed=ml.apply_neural(report(),'пострадавший дышит','дыхание сохранено','Смысл текста',30)
    assert assessed['score']==100 and assessed['rules_score']==100
    assert assessed['ml']['revision']=='revision-1'
    monkeypatch.setattr(ml,'neural_similarity',lambda expected,actual:{'status':'ok','similarity':.15,'model':'local-test-model','revision':'revision-1'})
    wrong=ml.apply_neural(report(),'пострадавший дышит','не связано','Смысл текста',30)
    assert wrong['score']==70 and wrong['rules_parts']['Смысл текста']==30
    assert wrong['errors']
    monkeypatch.setattr(ml,'neural_similarity',lambda expected,actual:{'status':'unavailable','message':'offline'})
    offline=ml.apply_neural(report(),'ожидаемое','текст','Смысл текста',30)
    assert offline['score']==100 and offline['ml']['status']=='unavailable'


def test_high_embedding_similarity_does_not_override_explicit_breathing_conflict(monkeypatch):
    monkeypatch.setattr(ml,'neural_similarity',lambda *args:{'status':'ok','similarity':.99,'model':'test','revision':'1'})
    report={'score':100,'parts':{'Смысл текста':30,'Регламент':70},'errors':[]}
    assessed=ml.apply_neural(report,'Мужчина без сознания, но дышит','Мужчина без сознания, не дышит','Смысл текста',30)
    assert assessed['score']==70 and assessed['ml']['critical_conflicts']
    assert ml.critical_conflicts('Дыхание сохранено','Дыхание есть')==[]
