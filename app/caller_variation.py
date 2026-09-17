"""Spoken presentation variation and fictional ordinary car registration marks."""
import re
import secrets

INTRODUCTIONS = (
    'Здравствуйте. Меня зовут {name}.', 'Добрый день. Я {name}.',
    'Алло, здравствуйте. Это {name}.', 'Служба 112? Меня зовут {name}.',
    'Здравствуйте, это {name}.', 'Алло. Меня зовут {name}.',
    'Добрый день, меня зовут {name}.', 'Здравствуйте. На связи {name}.',
    'Алло, это {name}.', 'Меня зовут {name}, здравствуйте.',
    'Это {name}. Здравствуйте.', 'Здравствуйте, вам звонит {name}.',
)
HESITATIONS = (
    'Так, попробую объяснить.', 'Подождите, начну по порядку.',
    'Секунду, соберусь с мыслями.', 'Мне трудно сразу всё описать.',
    'Сейчас расскажу, что вижу.', 'Попробую ничего не упустить.',
    'Давайте я объясню, что происходит.', 'Не знаю, с чего начать.',
    'Я немного сбиваюсь, но расскажу.', 'Постараюсь сказать точнее.',
)
REORDERINGS = (
    'Сначала о самом происшествии.', 'Начну с того, что происходит сейчас.',
    'Главное, что я могу сообщить.', 'Вот какие обстоятельства мне известны.',
    'Расскажу по порядку.', 'Лучше начну с того, что вижу.',
    'Объясню сначала, что случилось.', 'Сразу сообщу самое важное.',
)
LETTERS = 'АВЕКМНОРСТУХ'
REGIONS = ('16', '23', '24', '38', '42', '52', '54', '61', '63', '66', '70', '72', '74', '77', '78', '97', '98', '99', '116', '124', '154', '163', '177', '178', '199', '777', '797')
PLATE = re.compile(r'(?<![А-ЯЁ\d])([АВЕКМНОРСТУХ])(\d{3})([АВЕКМНОРСТУХ]{2})[ ](\d{2,3})(?!\d)')


def complex_presentation(text, choose):
    mode = choose('complex-presentation', ('direct', 'hesitant', 'reordered'))
    if mode == 'direct':
        return text
    if mode == 'reordered':
        return choose('complex-reordering', REORDERINGS) + ' ' + text
    cue = choose('complex-hesitation', HESITATIONS)
    before = choose('complex-hesitation-position', (True, False))
    if before or '. ' not in text:
        return cue + ' ' + text
    first, rest = text.split('. ', 1)
    return first + '. ' + cue + ' ' + rest


def fictional_plate():
    return (secrets.choice(LETTERS) + f'{secrets.randbelow(999)+1:03d}' +
            secrets.choice(LETTERS) + secrets.choice(LETTERS) + ' ' + secrets.choice(REGIONS))


def vehicle_number(facts, choose, explicit_vehicle=False):
    value = facts.lower().replace('ё', 'е')
    if PLATE.search(facts) or re.search(r'нет номерн|без номерн|номерн\w* знак\w* (?:нет|отсутств)|исчезли.*номер|регистрационн\w* номер\w*.*исчез', value):
        return ''
    if not explicit_vehicle and not re.search(r'\bавтомобил(?:ь|я|ю|ем|е|и|ей|ям|ями|ях)\b|\bавтомашин\w*|\bмашин(?:а|ы|е|у|ой)\b|\bгрузовик\w*|\bавтобус\w*', value):
        return ''
    mode = choose('vehicle-number-visibility', ('full', 'partial', 'unreadable', 'unmentioned'))
    if mode == 'unmentioned':
        return ''
    departed = bool(re.search(r'забрали|уехал|скрыл|угнал|угон', value))
    if mode == 'unreadable':
        variants = ('Госномер сейчас назвать не могу.', 'Номер автомобиля не запомнился.') if departed else (
            'Госномер с моего места не виден.', 'Номерной знак закрыт грязью, символы не разобрать.',
            'Машина стоит так, что номерного знака не видно.', 'Номер не могу разобрать с такого расстояния.')
        return choose('vehicle-number-unreadable', variants)
    plate = fictional_plate()
    letter, digits, pair, region = PLATE.fullmatch(plate).groups()
    if mode == 'partial':
        variants = (f'Из номера помню только цифры {digits}, буквы и регион не могу назвать.',) if departed else (
            f'На номере вижу только цифры {digits}, буквы и регион не различаю.',
            f'Вижу на номере первую букву {letter} и цифры {digits}, остальное не разобрать.',
            f'На номере различаю буквы {pair} и регион {region}, остальные символы не видны.')
        return choose('vehicle-number-partial', variants)
    target = 'У одной из машин номер' if re.search(r'две машины|столкновение автомобилей|столкнулись две', value) else 'Номер автомобиля'
    return f'{target} {plate}.'
