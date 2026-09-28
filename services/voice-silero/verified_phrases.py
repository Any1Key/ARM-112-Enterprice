import re

_PHONE_GENITIVE = re.compile(r'(\bс\s+(?:того|этого)\s+же\s+)(номера)\b', re.I)
_ON_LINE = re.compile(r'\b(на)\s+(связи)\b', re.I)
_ATTACKERS = re.compile(r'\bнападавших\b', re.I)
_PUNCTUATED_ABBREVIATION = re.compile(
    r'(?<!\w)(ул|просп|пер|наб|пл|обл|корп|под|эт|кв|стр|ш|г|авт|ген|гос|км|мкр|пос|р|ст|тел|треб|внутр)\.', re.I)
_CAPITAL_ABBREVIATION = re.compile(r'(?<!\w)(МКАД|МО|ТЦ|ТК|АЗС|КПП|СНТ|ДТП|ДДС|МЧС|МВД|МИД|СК|СМС|ТТК|ТС|МЖД|ж/д|а/м)(?!\w)', re.I)
_SLASH_ABBREVIATION = re.compile(r'(?<!\w)(Б/П|Б/З)(?!\w)', re.I)
_ADDRESS_SLASH_ABBREVIATION = re.compile(r'(?<!\w)с/п(?!\w)', re.I)
_ABBREVIATIONS = {
    'ул': 'улица', 'просп': 'проспект', 'пер': 'переулок', 'наб': 'набережная',
    'пл': 'площадь', 'обл': 'область', 'корп': 'корпус', 'под': 'подъезд',
    'эт': 'этаж', 'кв': 'квартира', 'стр': 'строение', 'ш': 'шоссе', 'г': 'город',
    'авт': 'автобусная', 'ген': 'генерала', 'гос': 'государственный', 'км': 'километр',
    'мкр': 'микрорайон', 'пос': 'посёлок', 'р': 'река', 'ст': 'станция', 'тел': 'телефон',
    'треб': 'требуется', 'внутр': 'внутренняя',
}
_CAPITAL_WORDS = {
    'МКАД': 'Московская кольцевая автомобильная дорога', 'МО': 'Московская область', 'MO': 'Московская область',
    'ТЦ': 'торговый центр', 'ТК': 'торговый комплект', 'АЗС': 'автозаправочная станция',
    'КПП': 'контрольно-пропускной пункт', 'СНТ': 'эс эн тэ', 'ДТП': 'дэ тэ пэ',
    'ДДС': 'дэ дэ эс', 'МЧС': 'эм чэ эс', 'МВД': 'эм вэ дэ', 'МИД': 'Министерство иностранных дел', 'СК': 'Садовое кольцо', 'СМС': 'эс эм эс',
    'ТТК': 'Третье транспортное кольцо', 'ТС': 'транспортное средство',
    'МЖД': 'Московская железная дорога',
    'Ж/Д': 'железнодорожный', 'А/М': 'автомобиль',
}
_SLASH_WORDS = {
    'Б/П': 'без пострадавших',
    'Б/З': 'без раненых',
}


def correct_verified_phrases(text: str) -> str:
    def punctuated(match):
        value = _ABBREVIATIONS[match[1].lower()]
        following = text[match.end():match.end() + 1]
        return value + (' ' if following and following.isalpha() else '')

    text = _PUNCTUATED_ABBREVIATION.sub(punctuated, text)
    text = _CAPITAL_ABBREVIATION.sub(lambda match: _CAPITAL_WORDS[match[0].upper()], text)
    text = _SLASH_ABBREVIATION.sub(lambda match: _SLASH_WORDS[match[0].upper()], text)
    text = _ADDRESS_SLASH_ABBREVIATION.sub('сельское поселение', text)
    text = re.sub(r'(?<!\w)А/Д(?!\w)', 'артериальное давление', text, flags=re.I)
    text = re.sub(r'(?<!\w)д/р(?!\w)', 'дата рождения', text, flags=re.I)
    text = re.sub(r'(?<!\w)ч/дом(?!\w)', 'частный дом', text, flags=re.I)

    # The abbreviation д. is ambiguous (дом / деревня), so expand it only
    # when it is directly followed by a numeric house number.
    text = re.sub(r'(?<!\w)д\.\s*(?=\d)', 'дом ', text, flags=re.I)

    def replace(match):
        word = match[2]
        stressed = 'Н+ОМЕРА' if word.isupper() else 'Н+омера' if word[0].isupper() else 'н+омера'
        return match[1] + stressed
    text = _PHONE_GENITIVE.sub(replace, text)

    def on_line(match):
        first = match[1]
        if first.isupper():
            first = first.capitalize()
        return f'{first} св+язи'

    text = _ON_LINE.sub(on_line, text)

    def attackers(match):
        return 'НАПАД+АВШИХ' if match[0].isupper() else 'Напад+авших' if match[0][0].isupper() else 'напад+авших'

    return _ATTACKERS.sub(attackers, text)
