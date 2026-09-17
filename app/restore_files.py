"""Journaled replacement of media/runtime volume contents during maintenance."""
import json
import os
import re
import shutil
from pathlib import Path

class FilesRestore:
    def __init__(self,job_id,directory,roots):
        if not re.fullmatch('[a-f0-9]{32}',job_id):raise ValueError('invalid restore job')
        self.job_id=job_id;self.directory=Path(directory);self.roots={name:Path(path) for name,path in roots.items()}
        self.journal=self.directory/'files_state.json'
        self.data=json.loads(self.journal.read_text()) if self.journal.exists() else {}

    def save(self):
        temporary=self.journal.with_suffix('.tmp')
        with temporary.open('w') as stream:
            json.dump(self.data,stream);stream.flush();os.fsync(stream.fileno())
        temporary.replace(self.journal)
        handle=os.open(self.directory,os.O_RDONLY)
        try:os.fsync(handle)
        finally:os.close(handle)

    def paths(self,name):
        root=self.roots[name]
        return root,root/('.restore_stage_'+self.job_id),root/('.restore_old_'+self.job_id)

    @staticmethod
    def remove(path):
        if path.is_symlink() or path.is_file():path.unlink()
        elif path.exists():shutil.rmtree(path)

    def pending(self):
        return any(info.get('phase') not in ('rolled_back','cleaned') for info in self.data.values())

    def rollback(self):
        for name in reversed(list(self.roots)):
            root,stage,old=self.paths(name);info=self.data.get(name)
            if info:
                if info['phase'] in ('rolled_back','cleaned'):
                    self.remove(stage)
                    if old.exists():old.rmdir()
                    continue
                if info['phase'] in ('old_moved','installed'):
                    for entry in info['new_names']:self.remove(root/entry)
                # Persist this before returning originals: retries must never erase them.
                info['phase']='imports_removed';self.save()
                if old.is_dir():
                    for original in old.iterdir():
                        target=root/original.name
                        if target.exists() or target.is_symlink():self.remove(target)
                        original.replace(target)
                info['phase']='rolled_back';self.save()
            elif old.exists() and any(old.iterdir()):raise OSError('Original files require recovery; restore journal is missing')
            self.remove(stage)
            if old.exists():old.rmdir()

    def install(self,extracted):
        extracted=Path(extracted)
        for name in self.roots:
            root,stage,old=self.paths(name)
            self.remove(stage)
            if old.exists():raise OSError('Previous file replacement has not been recovered')
            incoming=extracted/name
            needed=sum(path.stat().st_size for path in incoming.rglob('*') if path.is_file())
            if shutil.disk_usage(root).free<needed+16*1024**2:raise OSError('Not enough free disk space for restored files')
            shutil.copytree(incoming,stage)
            new_names=sorted(path.name for path in stage.iterdir())
            if any(entry.startswith(('.restore_stage_','.restore_old_')) for entry in new_names):raise ValueError('Reserved restore directory in archive')
            old_names=sorted(path.name for path in root.iterdir() if path!=stage)
            self.data[name]={'phase':'prepared','old_names':old_names,'new_names':new_names};self.save()
            old.mkdir(mode=0o700)
            for entry in old_names:(root/entry).replace(old/entry)
            self.data[name]['phase']='old_moved';self.save()
            for entry in new_names:(stage/entry).replace(root/entry)
            self.data[name]['phase']='installed';self.save()

    def cleanup(self):
        for name in self.roots:
            root,stage,old=self.paths(name)
            self.remove(stage);self.remove(old)
            if name in self.data:self.data[name]['phase']='cleaned'
        self.save()
