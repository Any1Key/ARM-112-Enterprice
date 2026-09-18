import re
from services.voice.pronunciation import prepare_text,cache_key,WORDS,REVISION,CONTEXTUAL_WORDS,SOURCES


def test_dictionary_is_russian_and_preserves_word_spelling():
    assert len(WORDS)>25
    for source,spoken in {**WORDS,**CONTEXTUAL_WORDS}.items():
        assert re.fullmatch('[а-яё]+',source),source
        assert re.fullmatch('[А-Яа-яЁё\u0301]+',spoken),spoken
        assert spoken.replace('\u0301','').lower().replace('ё','е')==source.replace('ё','е')
        assert '\u0301' in spoken or 'ё' in spoken
        for previous,character in zip(spoken,spoken[1:]):
            if character=='\u0301':assert previous.lower() in 'аеёиоуыэюя'


def test_stress_yo_and_word_boundaries_without_changing_original():
    source='Город Элиста, улица Березовая. Обрыв контактных проводов. Псевдоэлиста.'
    result=prepare_text(source)
    assert 'Элиста́' in result and 'Берёзовая' in result and 'проводо́в' in result
    assert 'Псевдоэлиста' in result
    assert source=='Город Элиста, улица Березовая. Обрыв контактных проводов. Псевдоэлиста.'
    assert prepare_text('ЭЛИСТА')=='ЭЛИСТА́'
    assert prepare_text('элиста')=='элиста́'
    assert prepare_text('э́листа')=='э́листа'
    # Ambiguous words require context, not blind global replacements.
    assert prepare_text('замок стоит рядом; все дома')=='замок стоит рядом; все дома'
    assert prepare_text(prepare_text(source))==result
    assert prepare_text('Кленовая')=='Клено́вая'
    assert prepare_text('Элиста Элисты Элисте')=='Элиста́ Элисты́ Элисте́'
    assert prepare_text('сегодня нет проводов')=='сегодня нет проводов'
    assert prepare_text('обрыв проводов')=='обрыв проводо́в'
    assert prepare_text('церемония проводов')=='церемония проводов'


def test_russian_address_abbreviations_and_uppercase_services():
    result=prepare_text('г. Тула, ул. Березовая, д. 26, кв. 12. Нужны МЧС и ДДС. ДТП.')
    assert 'город Тула, улица Берёзовая, дом 26, квартира 12' in result
    assert 'эм чэ эс' in result and 'дэ дэ эс' in result and 'дэ тэ пэ' in result
    assert prepare_text('2026 г. конец года; мвд')=='2026 г. конец года; мвд'
    assert prepare_text('2026 г. Конец года. А. Д. Иванов. См. стр. 12.')=='2026 г. Конец года. А. Д. Иванов. См. стр. 12.'
    assert prepare_text('дом 26, стр. 2')=='дом 26, строение 2'


def test_phone_digits_preserve_zeros_and_do_not_touch_other_numbers():
    assert prepare_text('+7 921 097 42 70')=='плюс семь, девять два один, ноль девять семь, четыре два, семь ноль'
    assert prepare_text('8 (921) 097-42-70')=='восемь, девять два один, ноль девять семь, четыре два, семь ноль'
    assert prepare_text('Дом 26, квартира 12. 12 дней. Номер 892109742700.')=='Дом 26, квартира 12. 12 дней. Номер 892109742700.'


def test_cache_includes_dictionary_revision_voice_and_prepared_text():
    import hashlib
    result=cache_key('irina',prepare_text('Элиста'))
    assert len(result)==64
    assert result!=cache_key('denis',prepare_text('Элиста'))
    assert result!=hashlib.sha256(('piper-irina-v1\\0Элиста').encode()).hexdigest()
    assert result==hashlib.sha256((f'piper-irina-v2\0{REVISION}\0'+prepare_text('Элиста')).encode()).hexdigest()


def test_every_stress_override_has_a_russian_dictionary_source():
    verified={word for source in SOURCES for word in source['forms']}
    assert verified==set(WORDS)|set(CONTEXTUAL_WORDS)
    assert all(source['url'].startswith('https://gramota.ru/') for source in SOURCES)
