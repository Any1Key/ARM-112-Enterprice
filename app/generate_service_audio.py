"""Generate the paired start/end prompts for every internal training service."""
import shutil
import urllib.request
import json
from pathlib import Path

from app.main import SessionLocal
from app.dds import directory
from app.service_directory import entries

VOICE_URL = 'http://voice-silero:8092/speech'
MEDIA = Path('/media')
VOICES = ('aidar', 'baya', 'eugene', 'kseniya', 'xenia')
PAIRS = (
 ('Здравствуйте. На связи {name}. Дежурный диспетчер слушает. Сообщите место и обстоятельства.', 'Информацию принял {name}. Спасибо за обращение. До свидания.'),
 ('Добрый день. Это {name}, служба на учебной линии. Я вас слушаю.', 'Заявка записана, {name}. Информация принята. Всего доброго.'),
 ('Служба {name}, здравствуйте. Говорите, пожалуйста, я готов принять сообщение.', 'Сообщение от вас получил {name}. Спасибо, до свидания.'),
 ('Здравствуйте, дежурный {name} у телефона. Передайте необходимые сведения.', 'Ваша информация зарегистрирована. С вами был {name}. До свидания.'),
 ('Добрый день. {name} слушает вас по учебному вызову.', 'Данные переданы дежурному. Информация принята. До связи.'),
 ('На связи служба {name}. Назовите адрес и расскажите, что произошло.', 'Адрес и обстоятельства записаны. {name} принял информацию. До свидания.'),
 ('Здравствуйте. Дежурный службы {name} готов выслушать ваше сообщение.', 'Сообщение принято службой {name}. Благодарю за звонок. До свидания.'),
 ('Служба {name}, добрый день. Я внимательно вас слушаю.', 'Информация сохранена и принята в работу. До свидания.'),
 ('Здравствуйте. Вы дозвонились до службы {name}. Сообщите подробности происшествия.', 'Подробности получены. Дежурный {name} подтверждает приём. До свидания.'),
 ('Добрый день, {name} на связи. Передавайте сообщение, пожалуйста.', 'Сообщение принято. Спасибо, что сообщили. Дежурный {name}, до свидания.'),
)

def speech(text, voice, caller_name):
    request=urllib.request.Request(VOICE_URL, data=json.dumps({'text':text,'voice':voice,'caller_name':caller_name}).encode(), headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:return json.load(response)['key']

def main():
    with SessionLocal() as session:
        services=entries(directory(session))
    total=0
    for item in services:
        for index,(start,end) in enumerate(PAIRS,1):
            voice=VOICES[(index-1)%len(VOICES)]
            caller=f'Дежурный {index}'
            for suffix,text in (('start',start),('end',end)):
                key=speech(text.format(name=item['name']),voice,caller)
                source=MEDIA/(key+'.wav');target=MEDIA/f"service-{item['extension']}-{index}-{suffix}.wav"
                if not target.exists():shutil.copyfile(source,target)
                total+=1
            print(f"{item['extension']} {index}/10", flush=True)
    print(f'Generated {total} service audio files for {len(services)} services')

if __name__=='__main__':main()
