import pytest
from app.speech_text import spoken_text
from app.generation import ScenarioDraft, compose_generated

@pytest.mark.parametrize('cue', ['[пауза]', '[самопоправка]', '[внезапно останавливается]', '[вздыхает]', '(длинная пауза)'])
def test_acting_directions_are_not_spoken(cue):
    assert spoken_text(f'Здравствуйте. {cue} Оборвался провод.') == 'Здравствуйте. Оборвался провод.'

def test_useful_parenthetical_facts_are_preserved():
    text = 'Дом 26 (около входа), квартира [номер неизвестен].'
    assert spoken_text(text) == text

def test_generated_caller_text_has_no_acting_directions():
    draft = ScenarioDraft(title='Обрыв проводов', caller_text='[внезапно останавливается] Оборвался провод. [пауза] Он лежит на земле. [самопоправка]', expected_description='Провод на земле.')
    generated = compose_generated(draft, 'город Элиста, улица Интернациональная, дом 26', 'Архипова Валерия Максимовна', '', '+7 921 097 42 70')
    assert '[' not in generated.caller_text
    assert 'Оборвался провод. Он лежит на земле.' in generated.caller_text
