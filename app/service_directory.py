"""Stable internal training numbers for every response service."""

BASE_NUMBERS = {'101': '101', '102': '102', '103': '103', '104': '104'}

def entries(names: dict[str, str]) -> list[dict[str, str]]:
    extra = sorted(code for code in names if code not in BASE_NUMBERS and code != '112')
    numbers = {**BASE_NUMBERS, **{code: str(2001 + i) for i, code in enumerate(extra)}}
    return [{'code': code, 'name': name, 'extension': numbers[code], 'phone': numbers[code],
             'subscriber': name, 'training': True} for code, name in names.items() if code != '112']

def by_code(names):
    return {item['code']: item for item in entries(names)}
