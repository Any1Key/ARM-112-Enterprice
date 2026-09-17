"""Queued custom-format restore: validate in a restricted staging DB, then swap names."""
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from threading import Event,Thread

ROOT=Path(os.getenv('BACKUP_ROOT','/backups'))
HOST=os.getenv('PGHOST','db')
USER=os.environ['POSTGRES_USER']
TARGET=os.environ['POSTGRES_DB']

class RestoreError(Exception):pass

def identifier(value):return '"'+value.replace('"','""')+'"'
def literal(value):return "'"+value.replace("'","''")+"'"
def atomic(path,data):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False));temporary.chmod(0o600);temporary.replace(path)

def command(args,*,env=None,timeout=1200):
    result=subprocess.run(args,env=env,capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        with (directory/'worker.log').open('a') as log:log.write(result.stderr+'\n')
        raise RestoreError('Операция PostgreSQL не выполнена. Подробности в закрытом журнале сервиса резервирования.')
    return result.stdout

def sql(statement,database='postgres'):
    return command(['psql','-X','-h',HOST,'-U',USER,'-d',database,'-v','ON_ERROR_STOP=1','-At','-c',statement],timeout=120)

def exists(name):return sql('SELECT count(*) FROM pg_database WHERE datname='+literal(name)).strip()=='1'

def state(phase,message,**extra):
    previous=json.loads((directory/'status.json').read_text())
    atomic(directory/'status.json',{**previous,**extra,'status':phase,'message':message,'updated_at':time.time()})
    (ROOT/'.restore-worker-heartbeat').touch()

def unblock():
    (ROOT/'.restore-maintenance').unlink(missing_ok=True)
    (Path(os.getenv('RESTORE_PROVISION_ROOT','/backup-provision'))/'maintenance').unlink(missing_ok=True)
    try:(ROOT/'.restore-lock').rmdir()
    except FileNotFoundError:pass
    (ROOT/'.restore-request').unlink(missing_ok=True)

SCHEMA="""SELECT coalesce(json_agg(row_to_json(c) ORDER BY c.table_name,c.ordinal_position),'[]')
FROM (SELECT table_name,column_name,ordinal_position,data_type,udt_name,is_nullable,character_maximum_length
FROM information_schema.columns WHERE table_schema='public') c"""

def validate_archive(path):
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):raise RestoreError('Файл копии не найден')
    with path.open('rb') as stream:
        if stream.read(5)!=b'PGDMP':raise RestoreError('Ожидался PostgreSQL custom-format дамп')
    try:toc=command(['pg_restore','--list',str(path)],timeout=120)
    except RestoreError:raise RestoreError('Дамп повреждён или несовместим с PostgreSQL 16. Загрузите custom-format копию из совместимой версии системы.')
    if re.search(r'^\d+;\s+\d+\s+\d+\s+(FUNCTION|PROCEDURE|TRIGGER|RULE|EVENT TRIGGER|FOREIGN|SERVER|EXTENSION)\b',toc,re.MULTILINE):
        raise RestoreError('Дамп содержит дополнительные исполняемые объекты и не подходит для автоматического восстановления этого приложения')

def restore(spec):
    if TARGET in ('postgres','template0','template1'):raise RestoreError('Восстановление системной базы запрещено')
    suffix=spec['job_id'][:24]
    stage='arm112_stage_'+suffix
    old='arm112_before_'+suffix
    role='arm112_import_'+suffix
    if exists(old) and exists(TARGET) and not exists(stage):
        state('finalizing','Завершается подключение приложения к восстановленной базе',previous_database=old)
        return
    if exists(old):raise RestoreError('Обнаружено незавершённое переключение базы; требуется проверка администратора')
    promoted=False
    try:
        state('draining','Ожидание завершения текущих запросов')
        deadline=time.monotonic()+120
        while True:
            try:active=int((ROOT/'.restore-inflight').read_text())
            except (OSError,ValueError):active=1
            if active==0:break
            if time.monotonic()>deadline:raise RestoreError('Текущие запросы не завершились. База не изменена; повторите позднее.')
            (ROOT/'.restore-worker-heartbeat').touch();time.sleep(1)
        if sql("SELECT count(*) FROM voip_calls WHERE state IN ('queued','ringing','answered')",TARGET).strip()!='0':
            raise RestoreError('Есть активные звонки. Завершите их и повторите восстановление.')
        if sql("SELECT count(*) FROM ai_jobs WHERE status IN ('queued','running')",TARGET).strip()!='0':
            raise RestoreError('Есть незавершённая генерация. Дождитесь её окончания.')
        source=ROOT/spec['source']
        validate_archive(source)
        digest=hashlib.sha256()
        with source.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        if digest.hexdigest()!=spec['sha256']:raise RestoreError('Дамп изменился после выбора. Загрузите или выберите его заново.')
        state('validating','Дамп восстанавливается в отдельную проверочную базу')
        if exists(stage):sql('DROP DATABASE '+identifier(stage)+' WITH (FORCE)')
        sql('DROP ROLE IF EXISTS '+identifier(role))
        role_password=secrets.token_hex(24)
        sql('CREATE ROLE '+identifier(role)+' LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '+literal(role_password))
        sql('CREATE DATABASE '+identifier(stage)+' OWNER '+identifier(role)+' TEMPLATE template0')
        sandbox_env={**os.environ,'PGPASSWORD':role_password,'PGOPTIONS':'-c statement_timeout=120000 -c lock_timeout=10000'}
        command(['pg_restore','-h',HOST,'-U',role,'-d',stage,'--exit-on-error','--single-transaction','--no-owner','--no-privileges',str(source)],env=sandbox_env)
        if json.loads(sql(SCHEMA,stage))!=json.loads(sql(SCHEMA,TARGET)):
            raise RestoreError('Структура дампа отличается от текущей версии приложения. Сначала используйте одинаковые версии на обоих серверах.')
        if sql("SELECT count(*) FROM users u LEFT JOIN account_states a ON a.user_id=u.id WHERE u.role='admin' AND coalesce(a.blocked,false)=false",stage).strip()=='0':
            raise RestoreError('В дампе нет доступного администратора. Текущая база сохранена.')
        sql('REASSIGN OWNED BY '+identifier(role)+' TO '+identifier(USER),stage)
        sql('ALTER DATABASE '+identifier(stage)+' OWNER TO '+identifier(USER))
        sql('DROP ROLE '+identifier(role))
        state('backing_up','Создаётся полная копия текущих данных перед заменой базы')
        command(['sh','/backup.sh'],env={**os.environ,'BACKUP_LOCK_HELD':'1'})
        safety=(ROOT/'latest.bundle').read_text().strip()
        state('switching','Подключения закрываются, проверенная база заменяет рабочую',safety_backup=safety,previous_database=old)
        sql('ALTER DATABASE '+identifier(TARGET)+' WITH ALLOW_CONNECTIONS false')
        sql('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='+literal(TARGET)+' AND pid<>pg_backend_pid()')
        # Both names change in one transaction; a failure leaves the old name intact.
        sql('BEGIN; ALTER DATABASE '+identifier(TARGET)+' RENAME TO '+identifier(old)+'; ALTER DATABASE '+identifier(stage)+' RENAME TO '+identifier(TARGET)+'; ALTER DATABASE '+identifier(TARGET)+' WITH ALLOW_CONNECTIONS true; COMMIT;')
        promoted=True
        state('finalizing','База заменена. Обновляются сеансы и настройки телефонии',safety_backup=safety,previous_database=old)
    except BaseException:
        if not promoted and exists(TARGET):sql('ALTER DATABASE '+identifier(TARGET)+' WITH ALLOW_CONNECTIONS true')
        raise
    finally:
        if not promoted and exists(stage):sql('DROP DATABASE '+identifier(stage)+' WITH (FORCE)')
        if not promoted:sql('DROP ROLE IF EXISTS '+identifier(role))

def main():
    global directory
    os.umask(0o077)
    request=json.loads((ROOT/'.restore-request').read_text())
    job_id=request['job_id']
    if not re.fullmatch('[a-f0-9]{32}',job_id):raise ValueError('invalid job id')
    directory=ROOT/'restore_jobs'/job_id
    spec=json.loads((directory/'request.json').read_text())
    status=json.loads((directory/'status.json').read_text())
    if status['status']=='succeeded':
        old=status.get('previous_database','')
        if re.fullmatch('arm112_before_[a-f0-9]{24}',old) and exists(old):sql('DROP DATABASE '+identifier(old)+' WITH (FORCE)')
        if spec['source'].startswith('uploads/'):(ROOT/spec['source']).unlink(missing_ok=True)
        (Path(os.getenv('RESTORE_PROVISION_ROOT','/backup-provision'))/'maintenance').unlink(missing_ok=True)
        (ROOT/'.restore-request').unlink(missing_ok=True)
        return
    if status['status']=='finalizing':return
    with (ROOT/'.backup.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:restore(spec)
        except (Exception,KeyboardInterrupt) as exc:
            # Never reopen after a successful promotion with unfinished finalization.
            suffix=spec['job_id'][:24]
            if exists('arm112_before_'+suffix) and exists(TARGET) and not exists('arm112_stage_'+suffix):
                state('finalizing','Переключение завершено; приложение повторит завершение')
            else:
                message=str(exc) if isinstance(exc,RestoreError) else 'Проверка или восстановление не завершены. Текущая база сохранена; проверьте журнал backup.'
                state('failed',message)
                unblock()

if __name__=='__main__':
    stop=Event()
    def heartbeat():
        while not stop.is_set():
            (ROOT/'.restore-worker-heartbeat').touch()
            stop.wait(5)
    Thread(target=heartbeat,daemon=True).start()
    try:main()
    finally:stop.set()

