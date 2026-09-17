"""Portable full backups and bounded extraction without links or path traversal."""
import gzip
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path,PurePosixPath

FILES=('database.dump','media.tar.gz','runtime.tar.gz','SHA256SUMS','manifest.json')
PAYLOADS=FILES[:3]
MAX_FULL_UPLOAD=2*1024**3
MAX_EXPANDED=8*1024**3
MAX_MEMBERS=100000

class ArchiveError(ValueError):pass

def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):value.update(chunk)
    return value.hexdigest()

def verify_bundle(directory):
    directory=Path(directory)
    for name in FILES:
        path=directory/name
        if path.is_symlink() or not path.is_file():raise ArchiveError('Полная копия не содержит всех необходимых файлов')
    try:
        manifest=json.loads((directory/'manifest.json').read_text())
        if manifest.get('version')!=1 or set(manifest.get('scope',[]))!={'database','media','runtime'}:raise ArchiveError('Неизвестный формат полной копии')
        checks={}
        for line in (directory/'SHA256SUMS').read_text().splitlines():
            checksum,name=line.split(maxsplit=1);name=name.lstrip('*')
            if name not in PAYLOADS or name in checks or len(checksum)!=64:raise ArchiveError('Некорректный список контрольных сумм')
            checks[name]=checksum
        if set(checks)!=set(PAYLOADS):raise ArchiveError('Отсутствуют контрольные суммы файлов')
        for name in PAYLOADS:
            if digest(directory/name)!=checks[name]:raise ArchiveError('Контрольная сумма не совпадает: '+name)
        with (directory/'database.dump').open('rb') as stream:
            if stream.read(5)!=b'PGDMP':raise ArchiveError('В архиве нет совместимого дампа PostgreSQL')
    except (OSError,KeyError,TypeError,AttributeError,UnicodeError,ValueError) as exc:
        if isinstance(exc,ArchiveError):raise
        raise ArchiveError('Полная копия повреждена или имеет неверный формат') from exc
    return manifest

def pack_bundle(directory,destination):
    verify_bundle(directory)
    with tarfile.open(destination,'w:gz',compresslevel=1) as archive:
        for name in FILES:archive.add(Path(directory)/name,arcname=name,recursive=False)

def safe_extract(archive_path,destination,*,outer=False,budget=MAX_EXPANDED):
    """All destinations are new private directories; never call extractall."""
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    seen=set();total=0;count=0
    try:
        with tarfile.open(archive_path,'r:gz') as archive:
            for member in archive:
                count+=1
                if count>MAX_MEMBERS:raise ArchiveError('Слишком много файлов в архиве')
                path=PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or '\\' in member.name or '\x00' in member.name:
                    raise ArchiveError('Недопустимый путь в архиве')
                name=str(path)
                if name=='.' and member.isdir():continue
                if not (member.isdir() or member.isfile()):raise ArchiveError('Ссылки и специальные файлы в копии запрещены')
                if name in seen:raise ArchiveError('Повторяющийся путь в архиве')
                seen.add(name)
                if outer and (name not in FILES or not member.isfile()):raise ArchiveError('Неизвестный файл в полной копии')
                if member.size<0:raise ArchiveError('Некорректный размер файла')
                total+=member.size
                if total>budget:raise ArchiveError('Распакованные данные превышают допустимый размер')
                target=destination/path
                if not target.resolve().is_relative_to(destination.resolve()):raise ArchiveError('Недопустимый путь в архиве')
                if member.isdir():target.mkdir(parents=True,exist_ok=True,mode=0o755);continue
                if shutil.disk_usage(destination).free<member.size+16*1024**2:raise ArchiveError('Недостаточно места для распаковки копии')
                target.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
                source=archive.extractfile(member)
                with source,target.open('xb') as stream:shutil.copyfileobj(source,stream,1024**2)
                target.chmod(member.mode & 0o755 or 0o600)
        if outer and seen!=set(FILES):raise ArchiveError('Полная копия не содержит всех необходимых файлов')
        # Read through gzip EOF as well: a tar end marker alone does not verify its CRC.
        expanded=0
        with gzip.open(archive_path,'rb') as stream:
            for chunk in iter(lambda:stream.read(1024**2),b''):
                expanded+=len(chunk)
                if expanded>budget+MAX_MEMBERS*2048+1024**2:raise ArchiveError('Превышен размер распакованного архива')
    except (OSError,EOFError,tarfile.TarError) as exc:raise ArchiveError('Архив повреждён или не является полной копией системы') from exc
    return total

def unpack_bundle(archive_path,destination):
    safe_extract(archive_path,destination,outer=True,budget=MAX_FULL_UPLOAD)
    verify_bundle(destination)
    return Path(destination)
