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
    assert len(hardware['password'])==10 and hardware['password']!=data['password']
    assert all(c in 'abcdefghjkmnpqrstuvwxyz23456789' for c in hardware['password'])
    assert client.post('/api/telephony/account?device=hardware',headers=student).json()['password']==hardware['password']
    provision=(tmp_path/'sip'/'users.conf').read_text()
    hw=provision.split('['+hardware['username']+']',1)[1].split('['+hardware['username']+'-auth]',1)[0]
    assert 'webrtc=no' in hw and 'media_encryption=no' in hw and 'transport=transport-udp' in hw
    assert 'set_var=ARM_USER_ID=' in hw
    assert 'from_domain=arm112.local' in provision
    hardware_auth=provision.split('['+hardware['username']+'-auth]',1)[1].split('['+hardware['username']+']',1)[0]
    assert 'password='+hardware['password']+'\n' in hardware_auth
    assert 'password='+data['password']+'\n' in provision
    assert len(reloads)==1, 'Reading the hardware profile must not reload PJSIP'
    again=client.post('/api/telephony/account',headers=student)
    assert again.status_code==200 and len(reloads)==1
    assert client.post('/api/telephony/account?device=unknown',headers=student).status_code==422


def test_ami_preserves_contact_output_and_detects_registration(monkeypatch):
    import io
    reply=telephony.read_frame(io.BytesIO(b'Response: Success\r\nOutput: Contact:  arm3/sip:test Avail\r\nOutput: footer\r\n\r\n'))
    assert len(reply['Output'])==2
    monkeypatch.setattr(telephony,'ami_action',lambda *a,**kw:reply)
    assert telephony.phone_registered('arm3')
    assert not telephony.phone_registered('arm4')
    monkeypatch.setattr(telephony,'ami_action',lambda *a,**kw:{'Output':['Contact: arm3/sip:test Unavail']})
    assert not telephony.phone_registered('arm3')


def test_unregistered_call_is_rejected_and_prepared_calls_can_cancel_and_retry(client,monkeypatch,tmp_path):
    from test_workflows import create_scenario
    from app.models import VoipCall
    from sqlalchemy import select
    from app import main
    student,teacher=auth(client,'student'),auth(client,'teacher')
    monkeypatch.setenv('SIP_PROVISION_ROOT',str(tmp_path/'sip'))
    monkeypatch.setattr(telephony,'ami_action',lambda *a,**kw:{'Response':'Success'})
    assert client.post('/api/telephony/account',headers=student).status_code==200
    scenario=create_scenario(client,teacher,assigned=True);run=client.post(f'/api/runs/{scenario}/start',headers=student).json();identifier=run['run_id']
    response=client.post(f'/api/telephony/runs/{identifier}/call',headers=student)
    assert response.status_code==409 and 'не зарегистрирован' in response.json()['detail']
    launched=[];monkeypatch.setattr(telephony,'phone_registered',lambda name:True)
    monkeypatch.setattr(telephony,'launch_worker',lambda target,args:launched.append((target,args)))
    first=client.post(f'/api/telephony/runs/{identifier}/call',headers=student).json()
    repeated=client.post(f'/api/telephony/runs/{identifier}/call',headers=student).json()
    assert first['call_id']==repeated['call_id'] and len(launched)==1
    assert client.post(f'/api/telephony/runs/{identifier}/call/cancel',headers=student).status_code==200
    monkeypatch.setattr(telephony,'prepare_speech',lambda *a:(_ for _ in ()).throw(AssertionError('Cancelled calls must not synthesize audio')))
    target,args=launched[0];target(*args)
    with main.SessionLocal() as s:assert s.get(VoipCall,first['call_id']).state=='cancelled'
    retry=client.post(f'/api/telephony/runs/{identifier}/call',headers=student).json()
    assert retry['call_id']!=first['call_id'] and len(launched)==2
    status=client.get(f'/api/telephony/runs/{identifier}',headers=student).json()
    assert status[-1]['created_at'] and status[-1]['state']=='queued'
    assert client.get(f'/api/runs/{identifier}',headers=student).json()['card']['channel']=='SIP / IP-телефон'


def test_audio_prewarm_requires_assigned_published_call_scenario(client,monkeypatch):
    from test_workflows import create_scenario
    student,teacher=auth(client,'student'),auth(client,'teacher');scenario=create_scenario(client,teacher)
    assert client.post(f'/api/telephony/scenarios/{scenario}/prepare',headers=student).status_code==403
    from test_workflows import assign_scenario
    assign_scenario(client,teacher,scenario)
    monkeypatch.setattr(telephony,'prepare_speech',lambda *args:{'key':'a'*64,'voice':'irina','cached':True})
    response=client.post(f'/api/telephony/scenarios/{scenario}/prepare',headers=student)
    assert response.status_code==200 and response.json()['cached']
    assert client.post(f'/api/telephony/scenarios/{scenario}/prepare',headers=teacher).status_code==403
    unpublished=create_scenario(client,teacher,published=False)
    assert client.post(f'/api/telephony/scenarios/{unpublished}/prepare',headers=student).status_code==403
