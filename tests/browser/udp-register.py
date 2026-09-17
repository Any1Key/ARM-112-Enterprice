import json,socket,secrets,re,hashlib,sys
from pathlib import Path
config=json.loads(Path(sys.argv[1]).read_text())
sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.bind(('0.0.0.0',0));sock.settimeout(10)
username=config['username'];password=config['password'];uri='sip:127.0.0.1';port=sock.getsockname()[1];callid=secrets.token_hex(16)
def request(sequence,authorization=''):
 branch='z9hG4bK'+secrets.token_hex(10)
 return (f'REGISTER {uri} SIP/2.0\r\nVia: SIP/2.0/UDP 127.0.0.1:{port};branch={branch};rport\r\nMax-Forwards: 70\r\nFrom: <sip:{username}@127.0.0.1>;tag=proof\r\nTo: <sip:{username}@127.0.0.1>\r\nCall-ID: {callid}\r\nCSeq: {sequence} REGISTER\r\nContact: <sip:{username}@127.0.0.1:{port}>\r\nExpires: 60\r\n{authorization}Content-Length: 0\r\n\r\n').encode()
sock.sendto(request(1),('127.0.0.1',5060));reply=sock.recv(65536).decode();assert reply.startswith('SIP/2.0 401'),reply.splitlines()[0]
challenge=next(line.split(':',1)[1] for line in reply.splitlines() if line.lower().startswith('www-authenticate:'))
print('Digest challenge:',{'algorithm':re.search(r'algorithm=([^, ]+)',challenge).group(1) if 'algorithm=' in challenge else 'MD5','realm':re.search(r'realm="([^"]+)"',challenge).group(1),'qop':re.search(r'qop="([^"]+)"',challenge).group(1) if 'qop=' in challenge else None})
values=dict(re.findall(r'(\w+)="([^"]*)"',challenge));realm=values['realm'];nonce=values['nonce'];md5=lambda x:hashlib.md5(x.encode()).hexdigest();cnonce=secrets.token_hex(12);ha1=md5(f'{username}:{realm}:{password}');ha2=md5('REGISTER:'+uri)
if 'qop' in values:
 response=md5(f'{ha1}:{nonce}:00000001:{cnonce}:auth:{ha2}');extra=f', qop=auth, nc=00000001, cnonce="{cnonce}"'
else:response=md5(f'{ha1}:{nonce}:{ha2}');extra=''
auth=f'Authorization: Digest username="{username}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{response}", algorithm=MD5{extra}\r\n'
sock.sendto(request(2,auth),('127.0.0.1',5060));reply=sock.recv(65536).decode();assert reply.startswith('SIP/2.0 200'),reply.splitlines()[0]
print('Hardware profile: real authenticated SIP REGISTER over UDP/5060 -> 200 OK PASS')
sock.close()
