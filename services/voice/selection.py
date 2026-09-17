"""Select a voice using the caller's name, independently of synthesis."""
import re
import secrets

VOICE_GENDERS = {'dmitri': 'male', 'ruslan': 'male', 'denis': 'male', 'irina': 'female'}
DISABLED_VOICES = frozenset({'ruslan'})
MALE_NAMES = set('иван алексей александр андрей антон артем артём борис вадим валерий василий виктор виталий владимир вячеслав геннадий георгий григорий данил даниил денис дмитрий евгений егор игорь илья кирилл константин лев леонид максим михаил никита николай олег павел петр пётр роман руслан семен семён сергей станислав степан тимофей федор фёдор юрий ярослав'.split())
FEMALE_NAMES = set('александра алина анастасия анна валентина валерия вера виктория галина дарья евгения екатерина елена елизавета жанна зинаида инна ирина ксения лариса лидия любовь людмила маргарита марина мария надежда наталья наталия нина оксана ольга полина светлана софия софья тамара татьяна юлия яна'.split())


def caller_gender(caller_name='', text=''):
    if not caller_name.strip():
        match = re.search(r'(?:меня зовут|мое имя|моё имя)\s+([^.!?\n,]+)', text, re.I)
        caller_name = match.group(1) if match else ''
    parts = re.findall(r'[а-яё]+', caller_name.lower())
    # Отчество надёжнее фамилии и работает с редкими именами.
    for part in parts:
        if part.endswith(('овна', 'евна', 'ична')) or part == 'кызы':
            return 'female'
        if part.endswith(('ович', 'евич', 'ьич')) or part == 'оглы':
            return 'male'
    genders = set()
    for part in parts:
        if part in MALE_NAMES:
            genders.add('male')
        if part in FEMALE_NAMES:
            genders.add('female')
    return next(iter(genders)) if len(genders) == 1 else None


def choose_voice(available, caller_name='', text='', requested=None):
    gender = caller_gender(caller_name, text)
    candidates = sorted(name for name in available
                        if name not in DISABLED_VOICES
                        and (gender is None or VOICE_GENDERS.get(name) == gender))
    if requested in candidates:
        return requested
    if candidates:
        return secrets.choice(candidates)
    # Если подходящей нейромодели нет, резервный движок сохраняет пол голоса.
    return 'espeak-female' if gender == 'female' else 'espeak-male'
