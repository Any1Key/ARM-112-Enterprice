"""Install the permanent practice catalog without overwriting teacher edits."""
import json
from pathlib import Path
from sqlalchemy import select
from app.models import User, Scenario, ScenarioSettings, IncidentType, ClassifierVersion

CATALOG=Path('data/scenarios/practice.json')


def install_practice_catalog(s):
    catalogs=[json.loads(CATALOG.read_text()),json.loads(Path('data/scenarios/workflows.json').read_text())]
    catalog={'cards':[item for c in catalogs for item in c['cards']]}
    teacher=s.scalar(select(User).where(User.username=='teacher',User.role=='teacher'))
    if not teacher:
        teacher=s.scalar(select(User).where(User.role=='teacher').order_by(User.id))
    if not teacher: return
    version=s.scalar(select(ClassifierVersion).order_by(ClassifierVersion.id.desc()))
    from app.questionnaires import expected_answers
    by_code={item.code:item for item in s.scalars(select(IncidentType).where(IncidentType.version_id==version.id))} if version else {}
    types={item.code:item.id for item in s.scalars(select(IncidentType).where(IncidentType.version_id==version.id))} if version else {}
    installed={settings.source.get('bundled_key'):settings for settings in s.scalars(select(ScenarioSettings)) if settings.source.get('bundled_key')}
    for item in catalog['cards']:
        if item['key'] in installed:
            existing=s.get(Scenario,installed[item['key']].scenario_id)
            if not existing.expected.get('questionnaire_answers') and item['classifier_codes']:
                answers={}
                for code in item['classifier_codes']:answers.update(expected_answers(by_code[code].data,existing.expected.get('flags',{})))
                existing.expected={**existing.expected,'questionnaire_answers':answers}
            continue
        # Adopt the original cards already present on the development server.
        scenario=s.scalar(select(Scenario).where(Scenario.title==item['title'],Scenario.caller_text==item['caller_text']))
        if scenario:
            settings=s.get(ScenarioSettings,scenario.id)
            if settings:
                settings.source={**settings.source,'bundled_key':item['key']}
                continue
        expected={**item['expected'],'classifier_ids':[types[code] for code in item['classifier_codes']]}
        if not expected.get('questionnaire_answers') and item['classifier_codes']:
            answers={}
            for code in item['classifier_codes']:answers.update(expected_answers(by_code[code].data,expected.get('flags',{})))
            expected['questionnaire_answers']=answers
        if not scenario:
            scenario=Scenario(title=item['title'],category=item['category'],caller_text=item['caller_text'],expected=expected,created_by=teacher.id)
            s.add(scenario);s.flush()
        s.add(ScenarioSettings(scenario_id=scenario.id,published=item['published'],difficulty=item['difficulty'],mode=item['mode'],
                               initial_card=item['initial_card'],source={'bundled_key':item['key'],'generation':item['generation']}))
    s.commit()
