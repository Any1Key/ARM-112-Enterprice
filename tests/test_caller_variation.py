import re
import random
import pytest
from app.caller_variation import fictional_plate, PLATE, REGIONS, complex_presentation, vehicle_number, INTRODUCTIONS, HESITATIONS, REORDERINGS
from app.generation import generate_local, varied_choice
from app.scenario_profiles import manifest
from app.speech_text import tts_text


@pytest.fixture(autouse=True)
def repeatable_randomness(monkeypatch):
    rng=random.Random(112)
    monkeypatch.setattr('secrets.choice',rng.choice)
    monkeypatch.setattr('secrets.randbelow',rng.randrange)


def test_random_plates_follow_ordinary_russian_car_format():
    plates={fictional_plate() for _ in range(500)}
    assert len(plates)>490
    for plate in plates:
        match=PLATE.fullmatch(plate)
        assert match and match[4] in REGIONS
        assert 1<=int(match[2])<=999
    assert any(len(PLATE.fullmatch(p)[4])==2 for p in plates)
    assert any(len(PLATE.fullmatch(p)[4])==3 for p in plates)


@pytest.mark.parametrize('mode', ['full','partial','unreadable','unmentioned'])
def test_each_plate_visibility_mode_has_consistent_facts(mode):
    line=vehicle_number('У дома стоит автомобиль.',lambda k,v:mode if k=='vehicle-number-visibility' else v[0])
    if mode=='full':
        assert PLATE.search(line)
        assert not re.search(r'не вид|не раз|не могу',line)
    elif mode=='partial':
        assert not PLATE.search(line)
        assert re.search(r'цифры \d{3}',line)
        assert 'не различаю' in line
    elif mode=='unreadable':
        assert 'не виден' in line and not PLATE.search(line)
    else:
        assert not line


@pytest.mark.parametrize('facts', ['Номерных знаков нет.', 'На машине нет номерных знаков.', 'С машины исчезли оба регистрационных номера.', 'У машины номер А123ВС 77.'])
def test_no_new_plate_when_missing_or_already_reported(facts):
    assert not vehicle_number(facts,lambda k,v:v[0],explicit_vehicle=True)


def test_complex_presentation_varies_without_changing_incident_facts(monkeypatch):
    monkeypatch.setattr('app.generation.recent_choices',{})
    original='Оборвался провод. Он лежит на земле.'
    texts=[complex_presentation(original,varied_choice) for _ in range(60)]
    assert original in texts
    assert len(set(texts))>=16
    assert any(t.startswith('Оборвался провод. ') and t!=original for t in texts)
    for text in texts:
        assert 'Оборвался провод.' in text and 'Он лежит на земле.' in text
        assert '[' not in text


def test_complex_scenarios_do_not_have_mandatory_repeated_padding(monkeypatch):
    monkeypatch.setattr('app.generation.recent_choices',{})
    generated=[generate_local(manifest()['14140100'],{},'complex')[0] for _ in range(45)]
    leads=[]
    for g in generated:
        assert 'Секунду, постараюсь объяснить.' not in g.caller_text
        assert 'Сейчас могу передать только эти сведения.' not in g.caller_text
        intro=next(template for template in INTRODUCTIONS if template.format(name=g.caller_name) in g.caller_text)
        assert intro not in leads[-4:]
        leads.append(intro)
    assert len(set(leads))==len(INTRODUCTIONS)


@pytest.mark.parametrize('code', ['16030000','2010000','2021700'])
def test_full_partial_and_unreadable_car_numbers_vary_and_match_summary(monkeypatch,code):
    monkeypatch.setattr('app.generation.recent_choices',{})
    generated=[generate_local(manifest()[code],{},'complex')[0] for _ in range(24)]
    assert any(PLATE.search(g.caller_text) for g in generated)
    assert any('Из номера' in g.caller_text or 'На номере' in g.caller_text or 'Вижу на номере' in g.caller_text for g in generated)
    assert any('номер' in g.caller_text.lower() and re.search(r'не вид|не раз|не могу',g.caller_text) for g in generated)
    for g in generated:
        for match in PLATE.finditer(g.caller_text):
            assert match[0] in g.expected_description
            assert 'не читается' not in g.caller_text
        assert g.caller_text.count('Номер автомобиля')<=1


def test_ticket_does_not_get_invented_car_number():
    g,_=generate_local(manifest()['2010000'],{'address':'Тула, улица Садовая, дом 2','situation':'Две машины столкнулись. Пострадавших нет.'},'complex')
    assert not PLATE.search(g.caller_text)


def test_plate_is_spelled_out_for_voice_without_reading_acting_directions():
    text=tts_text('Номер А007ВХ 177. [пауза] Телефон +7 999 123 45 67.')
    assert 'буква а, цифры ноль, ноль, семь, буквы вэ и ха, регион 177' in text
    assert 'пауза' not in text
    assert '+7 999 123 45 67' in text
