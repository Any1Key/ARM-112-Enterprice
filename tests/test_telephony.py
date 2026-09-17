from test_health import client, auth
from app import telephony

def test_sip_access_and_account_provision_are_per_student(client,monkeypatch,tmp_path):
    student=auth(client,'student');teacher=auth(client,'teacher')
    assert client.get('/api/telephony/health').status_code==401
    assert client.post('/api/telephony/account',headers=teacher).status_code==403
    monkeypatch.setenv('SIP_PROVISION_ROOT',str(tmp_path/'sip'))
    monkeypatch.setattr(telephony,'ami_action',lambda *args,**kwargs:{'Response':'Success'})
    account=client.post('/api/telephony/account',headers=student)
    assert account.status_code==200
    data=account.json();assert data['username'].startswith('arm') and len(data['password'])>=32
    assert data['ice_servers'][0]['urls'].startswith('turn:')
    provision=(tmp_path/'sip'/'users.conf').read_text()
    assert data['username']+'-auth' in provision and 'media_encryption=dtls' in provision
    assert client.get('/api/telephony/runs/99999',headers=student).status_code==404
    assert client.post('/api/telephony/runs/99999/call',headers=student).status_code==404
