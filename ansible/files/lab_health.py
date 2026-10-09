#!/usr/bin/python3
import json, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def query(sql):
    out=subprocess.check_output(['mariadb','--connect-timeout=3','-B','-e',sql],text=True,timeout=5)
    lines=out.splitlines()
    return [dict(zip(lines[0].split('\t'),line.split('\t'))) for line in lines[1:]] if lines else []

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path!='/health': self.send_error(404); return
        try:
            info=query('SELECT @@hostname AS hostname, @@server_id AS server_id, @@read_only AS read_only')[0]
            info['channels']=query('SHOW ALL SLAVES STATUS')
            info['reachable']=True
        except Exception:
            info={'reachable':False,'error':'MariaDB health query failed'}
        data=json.dumps(info).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(data)
    def log_message(self,*args): pass
ThreadingHTTPServer(('0.0.0.0',9109),Handler).serve_forever()
