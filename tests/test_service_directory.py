from app.service_directory import UNKNOWN_CODES, entries, spoken_entries


def test_training_service_directory_keeps_stable_numbers_and_excludes_unknown_codes():
    names = {'PSC': 'ОДС ПСЦ', 'EKP37': 'Автомобильные дороги', 'EKP72': 'С/П Вороновское'}
    all_entries = entries(names)
    spoken = spoken_entries(names)
    assert {item['extension'] for item in spoken} == {'2001', '2003'}
    assert next(item for item in spoken if item['code'] == 'EKP37')['name'] == 'Автомобильные дороги'
    assert all(item['code'] not in UNKNOWN_CODES for item in spoken)
    assert not any(item['code'] == 'EKP72' for item in spoken)
