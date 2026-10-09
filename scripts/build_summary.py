#!/usr/bin/env python3
import argparse,json,os
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--result',required=True);args=parser.parse_args()
action=os.environ.get('ACTION','UNKNOWN');success=args.result=='SUCCESS';applied=Path('infrastructure-applied').exists()
lines=[f'MariaDB lab — {action} — {args.result}',f'Requested replica count: {os.environ.get("REPLICA_COUNT","unknown")}','']
plan_path=Path('terraform/tfplan.json')
if plan_path.exists():
    plan=json.loads(plan_path.read_text());changes=plan.get('resource_changes',[])
    lines.append('DNS changes '+('(applied)' if applied else '(planned; application not confirmed)'))
    found=False
    for row in changes:
        if row.get('type')!='aws_route53_record':continue
        c=row['change'];actions=c['actions'];before=c.get('before') or {};after=c.get('after') or {}
        if actions==['no-op']: continue
        found=True
        label='REPLACE' if 'create' in actions and 'delete' in actions else '/'.join(actions).upper()
        record=after or before
        values=record.get('records') or []
        if not values:values=['IP assigned during apply']
        lines.append(f'  {label}: {record.get("name",row["address"])} {record.get("type","A")} -> {", ".join(values)}')
        if 'update' in actions:lines.append('    previous: '+', '.join(before.get('records') or []))
    if not found:lines.append('  No DNS changes.')
    totals={a:sum(a in r['change']['actions'] for r in changes) for a in ['create','update','delete']}
    lines.extend(['',f'Terraform resources: {totals["create"]} additions, {totals["update"]} updates, {totals["delete"]} deletions.'])
else: lines.append('No Terraform plan available; build stopped before a plan was saved.')
if success and action=='APPLY':
    lines.extend(['','Dashboard: https://dashboard.roblabb.com','Login: rob (password stored in Jenkins credential)',''])
    nodes_path=Path('nodes.json')
    if nodes_path.exists():
        nodes=json.loads(nodes_path.read_text())
        lines.append(f'Active instances: {len(nodes)}')
        for name,n in nodes.items():lines.append(f'  {name}: {n["instance_id"]}; private={n["private_ip"]}; public={n.get("public_ip", "")}')
    lines.extend(['','Configuration stages completed (some tasks may already have been correct):','  Jenkins hosts, instance hostnames and SSH hopping','  MariaDB/MaxScale installation, topology configuration and verification','  Seed planning; data seeding only where required and approved','  ZFS mirrors, datadir/log/temp/redo path migration checks','  Replica replication verification','  Sanoid: 24 hourly and 1 daily snapshots per replica','  Monitoring inventory and health endpoints','  HTTPS dashboard, password authentication and certificate renewal'])
elif success and action=='DESTROY':
    lines.extend(['','Lab resources and Terraform-managed DNS records removed.','Jenkins instance and the Route 53 hosted zone remain.','Dashboard is no longer deployed.'])
elif success and action=='PLAN':lines.extend(['','Preview only: no infrastructure or configuration changes applied.'])
else:lines.extend(['','Build failed: configuration may be partially completed. See the failed stage in console output.', 'Terraform plan was applied successfully before the later failure.' if applied else 'Terraform application was not confirmed; inspect state and rerun PLAN.','Dashboard availability has not been verified by this build.'])
# Read actual DNS state when available; do not describe it as changes.
if success and action=='APPLY':
    import subprocess
    output=subprocess.run(['terraform','output','-json','lab_dns'],cwd='terraform',text=True,capture_output=True)
    if output.returncode==0:
        lines.extend(['','Current managed A records:'])
        for name,record in sorted(json.loads(output.stdout).items()):lines.append(f'  {record["hostname"]} -> {record["address"]}')
report='\n'.join(lines)+'\n';Path('lab-summary.txt').write_text(report);print(report)
