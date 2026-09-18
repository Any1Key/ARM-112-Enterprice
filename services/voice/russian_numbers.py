"""Russian speech for explicit phone/address numbers; leave quantities alone."""
import re

_DIGITS=('ноль','один','два','три','четыре','пять','шесть','семь','восемь','девять')
_TEENS=('десять','одиннадцать','двенадцать','тринадцать','четырнадцать','пятнадцать','шестнадцать','семнадцать','восемнадцать','девятнадцать')
_TENS=('','','двадцать','тридцать','сорок','пятьдесят','шестьдесят','семьдесят','восемьдесят','девяносто')
_HUNDREDS=('','сто','двести','триста','четыреста','пятьсот','шестьсот','семьсот','восемьсот','девятьсот')
_PHONE=re.compile(r'(?<!\w)(\+7|8)[\s(.-]*(\d{3})[\s).\-]*(\d{3})[\s.\-]*(\d{2})[\s.\-]*(\d{2})(?!\w)')
# No partial conversion of fractional/alphanumeric addresses, times or ranges.
_NAMED=re.compile(r'(\b(?:дом|квартира|этаж|подъезд|корпус|строение|цифра|служба)\s+)(\d{1,4})(?![\w/:.\-–—]\d|[\w/\-–—])',re.I)


def cardinal(number: int) -> str:
    if not 0<=number<=9999:raise ValueError('Supported range is 0..9999')
    if number==0:return _DIGITS[0]
    parts=[]
    thousands,rest=divmod(number,1000)
    if thousands:
        name={1:'одна',2:'две'}.get(thousands,_DIGITS[thousands])
        form='тысяча' if thousands==1 else 'тысячи' if thousands in (2,3,4) else 'тысяч'
        parts.extend([name,form])
    hundreds,rest=divmod(rest,100)
    if hundreds:parts.append(_HUNDREDS[hundreds])
    if rest>=20:
        tens,ones=divmod(rest,10);parts.append(_TENS[tens])
        if ones:parts.append(_DIGITS[ones])
    elif rest>=10:parts.append(_TEENS[rest-10])
    elif rest:parts.append(_DIGITS[rest])
    return ' '.join(parts)


def prepare_number_text(text: str) -> str:
    def phone(match):
        prefix='плюс семь' if match[1]=='+7' else 'восемь'
        return prefix+', '+', '.join(' '.join(_DIGITS[int(d)] for d in group) for group in match.groups()[1:])
    text=_PHONE.sub(phone,text)
    def named(match):
        value=match[2]
        # Preserve meaningful leading zeros in identifiers.
        spoken=' '.join(_DIGITS[int(d)] for d in value) if len(value)>1 and value.startswith('0') else cardinal(int(value))
        return match[1]+spoken
    return _NAMED.sub(named,text)
