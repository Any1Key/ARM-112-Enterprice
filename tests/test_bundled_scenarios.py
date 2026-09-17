import json
from sqlalchemy import select, func
from test_health import client, auth


def test_permanent_catalog_visible_and_idempotent(client):
    from app import main
    from app.models import Scenario,ScenarioSettings,IncidentType
    from app.bundled_scenarios import install_practice_catalog,CATALOG
    catalog=json.loads(CATALOG.read_text())['cards']
    assert len(catalog)==24
    student=auth(client,'student')
    cards=client.get('/api/scenarios',headers=student).json()
    assert len(cards)==35
    assert {item['title'] for item in catalog}.issubset({card['title'] for card in cards})
    with main.SessionLocal() as s:
        installed={settings.source['bundled_key']:s.get(Scenario,settings.scenario_id) for settings in s.scalars(select(ScenarioSettings)) if settings.source.get('bundled_key')}
        for item in catalog:
            card=installed[item['key']]
            assert card.caller_text==item['caller_text']
            assert [s.get(IncidentType,id_).code for id_ in card.expected['classifier_ids']]==item['classifier_codes']
            assert 'classifier_ids' not in item['expected']
        first=installed[catalog[0]['key']]
        first.caller_text='Правка преподавателя';s.commit()
        install_practice_catalog(s);install_practice_catalog(s)
        assert s.scalar(select(func.count()).select_from(Scenario))==36
        assert s.get(Scenario,first.id).caller_text=='Правка преподавателя'
    run=client.post(f"/api/runs/{cards[0]['id']}/start",headers=student)
    assert run.status_code==200 and run.json()['norm_seconds']==180


def test_catalog_adopts_existing_original_card(client):
    from app import main
    from app.models import ScenarioSettings,Scenario
    from app.bundled_scenarios import install_practice_catalog
    with main.SessionLocal() as s:
        settings=next(settings for settings in s.scalars(select(ScenarioSettings)) if settings.source.get('bundled_key'))
        original_id=settings.scenario_id
        settings.source={key:value for key,value in settings.source.items() if key!='bundled_key'};s.commit()
        install_practice_catalog(s)
        assert s.get(ScenarioSettings,original_id).source.get('bundled_key')
        assert len(list(s.scalars(select(Scenario))))==36
