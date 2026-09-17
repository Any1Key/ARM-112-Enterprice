"""API integration checks with an isolated temporary SQLite database."""
import importlib
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "test.db"}')
    monkeypatch.setenv('GRAMMAR_URL', 'http://127.0.0.1:9')
    monkeypatch.setenv('ML_URL', 'http://127.0.0.1:9')
    monkeypatch.setenv('SECRET_KEY', 'isolated-test-secret-with-sufficient-length')
    for role in ('ADMIN', 'TEACHER', 'STUDENT'):
        monkeypatch.setenv(f'{role}_PASSWORD', role.lower() + '12345')
    sys.modules.pop('app.main', None)
    module = importlib.import_module('app.main')
    with TestClient(module.app) as test_client:
        yield test_client
    module.engine.dispose()


def auth(client, role):
    response = client.post('/api/login', json={'username': role, 'password': role + '12345'})
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


def test_health_and_authorization(client):
    assert client.get('/health').json()['status'] == 'ok'
    assert client.get('/').status_code == 200
    assert client.get('/api/scenarios').status_code == 401
    assert client.post('/api/login', json={'username': 'student', 'password': 'wrong'}).status_code == 401


def test_complete_training_cycle_and_idempotent_finish(client):
    student = auth(client, 'student')
    teacher = auth(client, 'teacher')
    from test_workflows import assign_scenario
    teacher = auth(client,'teacher')
    scenario = next(x for x in client.get('/api/scenarios',headers=teacher).json() if x['mode']=='call' and x['published'])
    assign_scenario(client,teacher,scenario['id'])
    scenario = client.get('/api/scenarios', headers=student).json()[0]
    assert scenario['expected'] is None
    expected = next(x['expected'] for x in client.get('/api/scenarios',headers=teacher).json() if x['id']==scenario['id'])
    run = client.post(f'/api/runs/{scenario["id"]}/start', headers=student).json()
    answers = {k: v for k, v in expected.items() if k != 'norm_seconds'}
    answers.update(description='Сильное задымление, внутри могут быть люди', caller_name='Иван', victims=True)
    response = client.post(f'/api/runs/{run["run_id"]}/finish', headers=student, json=answers)
    assert response.status_code == 200
    assert response.json()['score'] == 100
    assert response.json()['errors'] == []
    retry = client.post(f'/api/runs/{run["run_id"]}/finish', headers=student, json={})
    assert retry.json() == response.json()
    report = client.get('/api/reports', headers=student).json()[0]
    assert report['finished_at'] and report['score'] == 100
    assert report['student_name'] == 'student' and report['scenario_title'] == scenario['title']
    admin = auth(client, 'admin')
    events = client.get('/api/audit', headers=admin).json()
    assert len([e for e in events if e['action'] == 'run.finish']) == 1


def test_roles_and_scenario_validation(client):
    student, teacher, admin = (auth(client, role) for role in ('student', 'teacher', 'admin'))
    scenario = {'title': "Проверка ' <script> &", 'category': 'Тест', 'caller_text': 'Учебное сообщение',
                'expected': {'incident_type': 'Тест', 'address': 'Москва дом 1', 'services': ['112'],
                             'operator_comment': 'Сообщение принято', 'norm_seconds': 90}}
    assert client.post('/api/scenarios', headers=student, json=scenario).status_code == 403
    response = client.post('/api/scenarios', headers=teacher, json=scenario)
    assert response.status_code == 200
    assert client.post(f'/api/runs/{response.json()["id"]}/start', headers=teacher).status_code == 403
    assert client.get('/api/audit', headers=teacher).status_code == 403
    assert client.get('/api/audit', headers=student).status_code == 403
    assert client.get('/api/audit', headers=admin).status_code == 200
    scenario['expected']['norm_seconds'] = 0
    assert client.post('/api/scenarios', headers=teacher, json=scenario).status_code == 422


def test_student_cannot_finish_another_students_run(client):
    student = auth(client, 'student')
    from test_workflows import assign_scenario
    teacher = auth(client,'teacher')
    scenario = next(x for x in client.get('/api/scenarios',headers=teacher).json() if x['mode']=='call' and x['published'])
    assign_scenario(client,teacher,scenario['id'])
    scenario = client.get('/api/scenarios', headers=student).json()[0]
    run = client.post(f'/api/runs/{scenario["id"]}/start', headers=student).json()
    module = sys.modules['app.main']
    with module.SessionLocal() as session:
        user = module.User(username='other', password_hash=module.pwd.hash('other12345'), role='student')
        session.add(user)
        session.commit()
        token = module.token_for(user)
    other = {'Authorization': 'Bearer ' + token}
    assert client.post(f'/api/runs/{run["run_id"]}/finish', headers=other, json={}).status_code == 404
    assert client.get('/api/reports', headers=other).json() == []
