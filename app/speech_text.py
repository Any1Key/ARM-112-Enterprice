"""Keep acting directions out of spoken caller text."""
import re
from app.caller_variation import PLATE

_DIRECTION = re.compile(
    r'(?:пауза|самопоправка|внезапно|останавлива|замолка|молчит|'
    r'вздых|сме[её]|плачет|рыда|кашля|заика|запина|кричит|'
    r'ш[её]пот|шепчет|волнуется|нервничает|говорит|вдох|выдох)', re.I)

def spoken_text(text: str) -> str:
    def replace(match):
        return ' ' if _DIRECTION.search(match.group(1)) else match.group(0)
    text = re.sub(r'\[([^\[\]]{1,250})\]', replace, text)
    text = re.sub(r'\(([^()]{1,100})\)', replace, text)
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'\s+([,.;:!?])', r'\1', text)


def tts_text(text: str) -> str:
    names=dict(zip('АВЕКМНОРСТУХ', ('а','вэ','е','ка','эм','эн','о','эр','эс','тэ','у','ха')))
    digits=('ноль','один','два','три','четыре','пять','шесть','семь','восемь','девять')
    def plate(match):
        first,number,pair,region=match.groups()
        return (f'буква {names[first]}, цифры '+', '.join(digits[int(d)] for d in number)+
                f', буквы {names[pair[0]]} и {names[pair[1]]}, регион {region}')
    return PLATE.sub(plate,spoken_text(text))
