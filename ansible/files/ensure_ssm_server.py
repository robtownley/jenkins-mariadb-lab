#!/usr/bin/python3
import json,os,subprocess
from pathlib import Path

def run(*args):
    return subprocess.check_output(['docker',*args],text=True)
def inspect(name):
    p=subprocess.run(['docker','inspect',name],capture_output=True,text=True)
    return json.loads(p.stdout)[0] if p.returncode==0 else None
image='shatteredsilicon/ssm-server:latest'
if not inspect('ssm-data'):
    run('pull',image)
    run('create','-v','/opt/prometheus/data','-v','/opt/consul-data','-v','/var/lib/mysql','-v','/var/lib/grafana','--name','ssm-data',image,'/bin/true')
old=inspect('ssm-server')
password=os.environ['LAB_SSM_PASSWORD']
expected={'SERVER_USER':'rob','SERVER_PASSWORD':password,'DISABLE_UPDATES':'true'}
env=dict(x.split('=',1) for x in old['Config'].get('Env',[]) if '=' in x) if old else {}
if old and any(env.get(k)!=v for k,v in expected.items()):
    run('rm','-f','ssm-server');old=None
if not old:
    # Credentials are passed via a private environment file, not shell interpolation.
    path=Path('/etc/ssm-server.env');path.touch(mode=0o600,exist_ok=True);path.chmod(0o600)
    path.write_text('\n'.join(k+'='+v for k,v in expected.items())+'\n')
    run('run','-d','-p','127.0.0.1:8081:80','--volumes-from','ssm-data','--env-file',str(path),'--name','ssm-server','--restart','always',image)
    print('SSM_CREATED')
elif not old['State']['Running']:
    run('start','ssm-server');print('SSM_STARTED')
else: print('SSM_ALREADY_RUNNING')
