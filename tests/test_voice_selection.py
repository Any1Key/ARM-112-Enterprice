import pytest
from services.voice.selection import caller_gender, choose_voice


@pytest.mark.parametrize('name,gender', [
    ('Сидоров Иван Сергеевич', 'male'),
    ('Кузнецова Анна Викторовна', 'female'),
    ('Илья', 'male'),
    ('Любовь', 'female'),
    ('Никита', 'male'),
    ('Редкое Имя Петрович', 'male'),
    ('Редкое Имя Ильинична', 'female'),
    ('Саша', None),
    ('Не указано', None),
])
def test_name_gender(name, gender):
    assert caller_gender(name) == gender


def test_gender_constrains_random_voice_and_explicit_override(monkeypatch):
    voices = ['dmitri', 'ruslan', 'denis', 'irina']
    candidates=[]
    def choose(values):
        candidates.append(values)
        return values[0]
    monkeypatch.setattr('services.voice.selection.secrets.choice',choose)
    for _ in range(30):
        assert choose_voice(voices, 'Сидоров Иван Сергеевич', requested='irina') in ('denis','dmitri')
        assert choose_voice(voices, 'Орлова Мария Александровна', requested='denis') == 'irina'
    assert choose_voice(voices,'Иван',requested='ruslan') in ('denis','dmitri')
    assert choose_voice(voices,'Саша',requested='ruslan')!='ruslan'
    assert all('ruslan' not in values for values in candidates)


def test_old_scenario_without_name_uses_introduction():
    assert choose_voice(['irina', 'denis'], text='Здравствуйте. Меня зовут Анна Викторовна. Вижу дым.') == 'irina'


def test_missing_matching_model_uses_matching_fallback():
    assert choose_voice(['denis'], 'Анна') == 'espeak-female'
    assert choose_voice(['irina'], 'Иван') == 'espeak-male'


def test_unknown_name_keeps_available_voice_selection():
    assert choose_voice(['irina', 'denis'], 'Саша') in ('irina', 'denis')
