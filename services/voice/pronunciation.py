"""Small, local Russian pronunciation dictionary; no model or network calls."""
import hashlib
import json
import re
import unicodedata
from pathlib import Path

_RAW = Path(__file__).with_name('pronunciation_ru.json').read_bytes()
_DATA = json.loads(_RAW)
if _DATA['language'] != 'ru-RU':
    raise ValueError('Pronunciation dictionary must be Russian')
WORDS = _DATA['words']
CONTEXTUAL_WORDS = _DATA['contextual_words']
SOURCES = _DATA['sources']
ABBREVIATIONS = _DATA['abbreviations']
REVISION = hashlib.sha256(b'ru-pronunciation-v1\0' + _RAW).hexdigest()[:16]
_TOKEN = re.compile(r'[А-Яа-яЁё]+(?:\u0301[А-Яа-яЁё]*)*')
_ABBREVIATION = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(k) for k in sorted(ABBREVIATIONS, key=len, reverse=True)) + r')(?!\w)')
_PHONE = re.compile(r'(?<![\w\d])(?P<prefix>\+7|8)[\s(.-]*(\d{3})[\s).\-]*(\d{3})[\s.\-]*(\d{2})[\s.\-]*(\d{2})(?!\d)')
_DIGITS = ('ноль', 'один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять')


def prepare_text(text: str) -> str:
    """Prepare only the spoken copy. Existing explicit stress is preserved."""
    text = unicodedata.normalize('NFC', text)
    text = _PHONE.sub(lambda m: ('плюс семь' if m['prefix'] == '+7' else 'восемь') + ', ' + ', '.join(' '.join(_DIGITS[int(d)] for d in group) for group in m.groups()[1:]), text)
    text = _ABBREVIATION.sub(lambda m: ABBREVIATIONS[m[0]], text)
    for short, full in [('ул', 'улица'), ('кв', 'квартира'), ('д', 'дом'), ('корп', 'корпус'), ('просп', 'проспект'), ('пер', 'переулок')]:
        following = r'\d' if short in ('кв', 'д', 'корп') else r'[А-Яа-яЁё]'
        text = re.sub(r'(?<!\w)' + short + r'\.\s*(?=' + following + ')', full + ' ', text, flags=re.I)
    def structure(match):
        context=text[max(0,match.start()-80):match.start()].lower()
        return 'строение ' if re.search(r'дом|корпус|улиц|адрес',context) else match[0]
    text = re.sub(r'(?<!\w)стр\.\s*(?=\d)', structure, text, flags=re.I)
    # A year followed by the next sentence must not become a city.
    def city(match):
        before=text[:match.start()].rstrip()
        return match[0] if before and before[-1].isdigit() else 'город '
    text = re.sub(r'(?<!\w)г\.\s*(?=[А-ЯЁ])', city, text)

    def word(match):
        original = match[0]
        if '\u0301' in original:
            return original
        replacement = WORDS.get(original.lower())
        if original.lower() in CONTEXTUAL_WORDS:
            context=text[max(0,match.start()-80):match.end()+80].lower()
            if re.search(r'электр|контактн|высоковольт|оборва|обрыв|искр|кабел|напряжени',context) and not re.search(r'церемон|шеств|прощан',context):
                replacement=CONTEXTUAL_WORDS[original.lower()]
        if replacement is None:
            return original
        if original.isupper():
            return replacement.upper()
        if original[0].isupper():
            return replacement[0].upper() + replacement[1:]
        return replacement.lower()
    return _TOKEN.sub(word, text)


def cache_key(voice: str, text: str) -> str:
    return hashlib.sha256((f'piper-{voice}-v2\0{REVISION}\0' + text).encode()).hexdigest()
