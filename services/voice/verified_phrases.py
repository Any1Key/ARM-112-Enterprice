"""Only narrow Russian phrases verified on the installed speech engine."""
import re

_PHONE_GENITIVE = re.compile(r'(\bс\s+(?:того|этого)\s+же\s+)(номера)\b',re.I)


def correct_verified_phrases(text: str) -> str:
    def replace(match):
        word=match[2]
        stressed='НО́МЕРА' if word.isupper() else 'Но́мера' if word[0].isupper() else 'но́мера'
        return match[1]+stressed
    return _PHONE_GENITIVE.sub(replace,text)
