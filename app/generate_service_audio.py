"""Generate the paired start/end prompts for every internal training service."""
import shutil
import urllib.request
import json
from pathlib import Path

from app.main import SessionLocal
from app.dds import directory
from app.service_directory import spoken_entries

VOICE_URL = 'http://voice-silero:8092/speech'
MEDIA = Path('/media')
VOICES = ('aidar', 'baya', 'eugene', 'kseniya', 'xenia')
PAIRS = (
 ('Здравствуйте. На связи служба {name}. Дежурный диспетчер слушает. Сообщите место и обстоятельства.', 'Информацию приняли. Спасибо за обращение. До свидания.'),
 ('Добрый день. Служба {name}, я вас слушаю.', 'Заявка зарегистрирована. Информация принята. Всего доброго.'),
 ('Служба {name}, здравствуйте. Говорите, пожалуйста.', 'Сообщение принято. Спасибо за звонок. До свидания.'),
 ('Здравствуйте. Дежурный службы {name} у телефона. Передайте необходимые сведения.', 'Ваша информация зарегистрирована. До свидания.'),
 ('Добрый день. Служба {name} слушает вас.', 'Данные переданы дежурному. Информация принята. До связи.'),
 ('На связи служба {name}. Назовите адрес и расскажите, что произошло.', 'Адрес и обстоятельства записаны. Информация принята. До свидания.'),
 ('Здравствуйте. Дежурный службы {name} готов выслушать ваше сообщение.', 'Сообщение принято. Благодарю за обращение. До свидания.'),
 ('Служба {name}, добрый день. Я внимательно вас слушаю.', 'Информация сохранена и принята в работу. До свидания.'),
 ('Здравствуйте. Вы дозвонились до службы {name}. Сообщите подробности происшествия.', 'Подробности получены. Информация принята. До свидания.'),
 ('Добрый день. Служба {name} на связи. Передавайте сообщение, пожалуйста.', 'Сообщение принято. Спасибо, до свидания.'),
)

def speech(text, voice, caller_name):
    request=urllib.request.Request(VOICE_URL, data=json.dumps({'text':text,'voice':voice,'caller_name':caller_name}).encode(), headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:return json.load(response)['key']

def main():
    with SessionLocal() as session:
        services=[item for item in spoken_entries(directory(session)) if item['code'] not in {'101','102','103','104'}]
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
