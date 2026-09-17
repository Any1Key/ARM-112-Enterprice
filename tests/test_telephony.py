from test_health import client, auth
from app import telephony

def test_sip_access_and_account_provision_are_per_student(client,monkeypatch,tmp_path):
    student=auth(client,'student');teacher=auth(client,'teacher')
    assert client.get('/api/telephony/health').status_code==401
    assert client.post('/api/telephony/account',headers=teacher).status_code==403
    monkeypatch.setenv('SIP_PROVISION_ROOT',str(tmp_path/'sip'))
    reloads=[]
    def ami_stub(*args,**kwargs):
        reloads.append((args,kwargs));return {'Response':'Success'}
    monkeypatch.setattr(telephony,'ami_action',ami_stub)
    account=client.post('/api/telephony/account',headers=student)
    assert account.status_code==200
    data=account.json();assert data['username'].startswith('arm') and len(data['password'])>=32
    assert data['ice_servers'][0]['urls'].startswith('turn:')
    provision=(tmp_path/'sip'/'users.conf').read_text()
    assert data['username']+'-auth' in provision and 'media_encryption=dtls' in provision
    assert client.get('/api/telephony/runs/99999',headers=student).status_code==404
    assert client.post('/api/telephony/runs/99999/call',headers=student).status_code==404

    hardware=client.post('/api/telephony/account?device=hardware',headers=student).json()
    assert hardware['username']==data['username']+'-hw' and hardware['transport']=='UDP'
    provision=(tmp_path/'sip'/'users.conf').read_text()
    hw=provision.split('['+hardware['username']+']',1)[1].split('['+hardware['username']+'-auth]',1)[0]
    assert 'webrtc=no' in hw and 'media_encryption=no' in hw and 'transport=transport-udp' in hw
    assert 'set_var=ARM_USER_ID=' in hw
    assert 'from_domain=arm112.local' in provision
    assert len(reloads)==1, 'Reading the hardware profile must not reload PJSIP'
    again=client.post('/api/telephony/account',headers=student)
    assert again.status_code==200 and len(reloads)==1
    assert client.post('/api/telephony/account?device=unknown',headers=student).status_code==422
