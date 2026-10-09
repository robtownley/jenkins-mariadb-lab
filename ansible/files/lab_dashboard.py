#!/usr/bin/python3
import json,time,socket,threading,urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
NODES=json.loads(Path('/etc/mariadb-lab-monitor/nodes.json').read_text())
state={'updated':None,'nodes':[]}
def check(node):
    result={**node,'status':'red','reason':'Node unavailable'}
    try:
        if node['role']=='maxscale':
            with socket.create_connection((node['ip'],3306),timeout=3): pass
            result.update(status='green',reason='Proxy listener reachable (TCP check)')
        else:
            with urllib.request.urlopen('http://'+node['ip']+':9109/health',timeout=7) as response:
                health=json.load(response)
            result['details']=health
            if not health.get('reachable'): return result
            channels=health.get('channels',[])
            if node['role']=='primary':
                result.update(status='green' if health['read_only']=='0' else 'red',reason='Writable primary' if health['read_only']=='0' else 'Primary is read only')
            elif not channels:
                result['reason']='Replication channel missing'
            elif any(c.get('Slave_IO_Running')!='Yes' or c.get('Slave_SQL_Running')!='Yes' or c.get('Seconds_Behind_Master') in (None,'NULL') or c.get('Last_IO_Errno','0')!='0' or c.get('Last_SQL_Errno','0')!='0' for c in channels):
                result['reason']='Replication stopped, disconnected or reporting errors'
            else:
                lag=max(int(c['Seconds_Behind_Master']) for c in channels)
                result.update(status='amber' if lag>0 else 'green',reason=f'Replication lag: {lag}s',lag=lag)
    except Exception:
        pass
    return result

def poll():
    global state
    while True:
        with ThreadPoolExecutor(max_workers=8) as pool: rows=list(pool.map(check,NODES))
        state={'updated':time.time(),'nodes':rows}
        time.sleep(10)

PAGE='''<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>MariaDB Lab</title><style>body{background:#101827;color:#eef2ff;font:16px system-ui;padding:30px}h1{margin-bottom:5px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px;margin-top:25px}.card{background:#1c293c;border-radius:12px;padding:22px;border-top:5px solid #ef4444}.green{border-color:#22c55e}.amber{border-color:#f59e0b}.red{border-color:#ef4444}small{color:#a8b7cc}pre{white-space:pre-wrap;font-size:12px}button{cursor:pointer}</style></head><body><h1>MariaDB cluster</h1><p>Green: healthy · Amber: replication lag · Red: unavailable or broken</p><small id="time">Waiting for first check…</small><div class="grid" id="nodes"></div><script>async function refresh(){try{const d=await(await fetch('/api/health',{cache:'no-store'})).json();document.getElementById('time').textContent=d.updated?'Last check: '+new Date(d.updated*1000).toLocaleString():'Starting checks…';const grid=document.getElementById('nodes');grid.replaceChildren();for(const n of d.nodes){const card=document.createElement('div');const stale=!d.updated||Date.now()/1000-d.updated>35;card.className='card '+(stale?'red':n.status);for(const text of [n.name,n.ip,stale?'Monitoring results stale':n.reason]){const p=document.createElement('p');p.textContent=text;card.append(p)}if(n.details){const detail=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Database details';const pre=document.createElement('pre');pre.textContent=JSON.stringify(n.details,null,2);detail.append(summary,pre);card.append(detail)}grid.append(card)}}catch(e){document.getElementById('time').textContent='Dashboard connection lost — displayed results may be stale'}}refresh();setInterval(refresh,5000)</script></body></html>'''
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path=='/api/health': data=json.dumps(state).encode(); kind='application/json'
        elif self.path=='/': data=PAGE.encode(); kind='text/html; charset=utf-8'
        else: self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    def log_message(self,*args):pass
if __name__=='__main__':
    threading.Thread(target=poll,daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1',8081),Handler).serve_forever()
