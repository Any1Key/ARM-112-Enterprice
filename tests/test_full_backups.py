"""Portable archive integrity, safe extraction, volume rollback and admin export."""
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
from app.backup_archives import ArchiveError,FILES,pack_bundle,unpack_bundle,safe_extract
from app.restore_files import FilesRestore
from test_health import client,auth
from test_restoration import restore_root,payload


def fixture_bundle(directory):
    directory.mkdir()
    (directory/'database.dump').write_bytes(b'PGDMP-fixture')
    for name,entry,data in [('media.tar.gz','recording-7-8.wav',b'original recording'),('runtime.tar.gz','card_attachments/example.txt',b'original attachment')]:
        with tarfile.open(directory/name,'w:gz') as archive:
            info=tarfile.TarInfo(entry);info.size=len(data);archive.addfile(info,io.BytesIO(data))
    checks=''.join(hashlib.sha256((directory/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in FILES[:3])
    (directory/'SHA256SUMS').write_text(checks)
    (directory/'manifest.json').write_text(json.dumps({'version':1,'scope':['database','media','runtime'],'created_at':'2026-09-17T18:53:10Z'}))
    return directory


def test_complete_package_and_integrity(tmp_path):
    bundle=fixture_bundle(tmp_path/'bundle');archive=tmp_path/'backup.tar.gz'
    pack_bundle(bundle,archive)
    imported=unpack_bundle(archive,tmp_path/'imported')
    assert set(path.name for path in imported.iterdir())==set(FILES)
    safe_extract(imported/'media.tar.gz',tmp_path/'media')
    safe_extract(imported/'runtime.tar.gz',tmp_path/'runtime')
    assert (tmp_path/'media/recording-7-8.wav').read_bytes()==b'original recording'
    assert (tmp_path/'runtime/card_attachments/example.txt').read_bytes()==b'original attachment'
    (bundle/'database.dump').write_bytes(b'PGDMP-corrupted')
    with pytest.raises(ArchiveError,match='Контрольная'):pack_bundle(bundle,tmp_path/'invalid.tar.gz')


@pytest.mark.parametrize('name,kind',[('../escape','file'),('/absolute','file'),('a/../../escape','file'),('link','symlink'),('hardlink','hardlink')])
def test_unsafe_members_are_rejected(tmp_path,name,kind):
    path=tmp_path/'unsafe.tar.gz'
    with tarfile.open(path,'w:gz') as archive:
        info=tarfile.TarInfo(name)
        if kind=='file':info.size=1;archive.addfile(info,io.BytesIO(b'x'))
        else:info.type=tarfile.SYMTYPE if kind=='symlink' else tarfile.LNKTYPE;info.linkname='../../escape';archive.addfile(info)
    with pytest.raises(ArchiveError):safe_extract(path,tmp_path/'output')
    assert not (tmp_path/'escape').exists()


def test_expansion_limit_and_truncated_gzip(tmp_path):
    bundle=fixture_bundle(tmp_path/'bundle')
    with pytest.raises(ArchiveError,match='размер'):safe_extract(bundle/'media.tar.gz',tmp_path/'bounded',budget=2)
    damaged=tmp_path/'damaged.tar.gz';damaged.write_bytes((bundle/'media.tar.gz').read_bytes()[:-4])
    with pytest.raises(ArchiveError):safe_extract(damaged,tmp_path/'damaged-output')


def test_volume_replacement_and_recovery_after_partial_move(tmp_path,monkeypatch):
    roots={name:tmp_path/name for name in ('media','runtime')}
    extracted=tmp_path/'incoming';job=tmp_path/'job';job.mkdir()
    for name,root in roots.items():
        root.mkdir();(root/'same.txt').write_text('old-'+name);(root/'old-only.txt').write_text('old-only')
        incoming=extracted/name;incoming.mkdir(parents=True);(incoming/'same.txt').write_text('new-'+name);(incoming/'new-only.txt').write_text('new-only')
    controller=FilesRestore('a'*32,job,roots)
    controller.install(extracted)
    assert (roots['media']/'same.txt').read_text()=='new-media'
    assert not (roots['runtime']/'old-only.txt').exists()
    recovered=FilesRestore('a'*32,job,roots);recovered.rollback()
    for name,root in roots.items():
        assert (root/'same.txt').read_text()=='old-'+name
        assert (root/'old-only.txt').exists()
        assert not (root/'new-only.txt').exists()
    original=Path.replace;failed=False
    def interrupted(path,target):
        nonlocal failed
        if not failed and path==roots['runtime']/'same.txt':failed=True;raise OSError('simulated filesystem interruption')
        return original(path,target)
    monkeypatch.setattr(Path,'replace',interrupted)
    with pytest.raises(OSError):controller.install(extracted)
    FilesRestore('a'*32,job,roots).rollback()
    for name,root in roots.items():assert (root/'same.txt').read_text()=='old-'+name


def test_export_ticket_and_full_upload_are_admin_only(restore_root,client):
    bundle_id='arm112_20260917_123';fixture_bundle(restore_root/bundle_id)
    admin=auth(client,'admin');student=auth(client,'student')
    path='/api/operations/backups/'+bundle_id+'/export'
    assert client.post(path,headers=student).status_code==403
    response=client.post(path,headers=admin)
    assert response.status_code==201,response.text
    link=response.json()['url'];download=client.get(link)
    assert download.status_code==200
    assert download.headers['cache-control']=='no-store'
    assert client.get(link).status_code==404
    uploaded=client.post('/api/operations/restore/uploads?kind=full',headers=admin,content=download.content)
    assert uploaded.status_code==201 and uploaded.json()['kind']=='full'
    assert client.post('/api/operations/restore/uploads?kind=full',headers=student,content=download.content).status_code==403
    queued=client.post('/api/operations/restore/jobs',headers=admin,json=payload(uploaded.json()['upload_id']))
    assert queued.status_code==202,queued.text
    request=json.loads((restore_root/'restore_jobs'/queued.json()['job_id']/'request.json').read_text())
    assert request['scope']=='full' and request['source'].endswith('/full.tar.gz')


def test_rollback_can_resume_after_original_file_is_returned(tmp_path,monkeypatch):
    root=tmp_path/'media';root.mkdir();(root/'same.txt').write_text('original')
    incoming=tmp_path/'incoming/media';incoming.mkdir(parents=True);(incoming/'same.txt').write_text('imported')
    job=tmp_path/'job';job.mkdir();controller=FilesRestore('b'*32,job,{'media':root});controller.install(incoming.parent)
    replace=Path.replace
    def interrupted(path,target):
        result=replace(path,target)
        if path.name=='same.txt' and path.parent.name.startswith('.restore_old_'):raise OSError('power interruption after original returned')
        return result
    monkeypatch.setattr(Path,'replace',interrupted)
    with pytest.raises(OSError):controller.rollback()
    assert (root/'same.txt').read_text()=='original'
    monkeypatch.setattr(Path,'replace',replace)
    FilesRestore('b'*32,job,{'media':root}).rollback()
    assert (root/'same.txt').read_text()=='original'
