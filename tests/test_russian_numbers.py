import pytest
from services.voice.russian_numbers import cardinal,prepare_number_text


@pytest.mark.parametrize('number,spoken',[(0,'ноль'),(7,'семь'),(12,'двенадцать'),(26,'двадцать шесть'),(112,'сто двенадцать'),(921,'девятьсот двадцать один'),(1000,'одна тысяча'),(2001,'две тысячи один'),(9999,'девять тысяч девятьсот девяносто девять')])
def test_russian_cardinal(number,spoken):
    assert cardinal(number)==spoken


def test_phone_numbers_preserve_every_digit_and_group():
    expected='плюс семь, девятьсот двадцать один, ноль девять семь, сорок два, семьдесят'
    for number in ['+7 921 097 42 70','+7(921)097-42-70','+79210974270']:
        assert prepare_number_text(number)==expected
    assert prepare_number_text('8 (921) 097-42-70')==expected.replace('плюс семь','восемь')
    assert prepare_number_text('+7 921 097 42 700')=='+7 921 097 42 700'


def test_named_numbers_without_blind_grammar_changes():
    assert prepare_number_text('Служба 112? Дом 26, квартира 12, этаж 3.')=='Служба сто двенадцать? Дом двадцать шесть, квартира двенадцать, этаж три.'
    assert prepare_number_text('дом 007')=='дом ноль ноль семь'
    text='2 женщины, 12 дней, около 3 часов, в 19:30, дом 26/2, дом 26А, дом 26-28, дом 26–28, дом 26—28, дом 26.5, дом 12345.'
    assert prepare_number_text(text)==text
    assert prepare_number_text('дом 26. Квартира 12.')=='дом двадцать шесть. Квартира двенадцать.'
    # Re-running processing cannot turn Russian words into another number.
    prepared=prepare_number_text('Дом 26. Телефон +7 921 097 42 70.')
    assert prepare_number_text(prepared)==prepared


def test_phone_groups_read_whole_unless_they_start_with_zero():
    assert prepare_number_text('+7 921 097 48 70')=='плюс семь, девятьсот двадцать один, ноль девять семь, сорок восемь, семьдесят'
    assert prepare_number_text('+7 900 000 01 00')=='плюс семь, девятьсот, ноль ноль ноль, ноль один, ноль ноль'
    assert prepare_number_text('+7 999 123 10 99')=='плюс семь, девятьсот девяносто девять, сто двадцать три, десять, девяносто девять'
