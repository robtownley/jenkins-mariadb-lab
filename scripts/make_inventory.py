import json
import ipaddress
import shlex
import os
import re
from pathlib import Path

nodes = json.loads(Path("nodes.json").read_text())
if "primary" not in nodes or "maxscale" not in nodes:
    raise SystemExit("Terraform output must include primary and maxscale.")

unexpected = [name for name in nodes if name not in ("primary", "maxscale", "monitor", "ssm")
              and not re.fullmatch(r"(?:replica|sdds|affiliate-feed|heron)[1-9][0-9]*", name)]
if unexpected:
    raise SystemExit(f"Unexpected node names: {unexpected}")

replicas = sorted((name for name in nodes if name.startswith("replica")),
                  key=lambda name: int(name[7:]))
downstream = sorted((name for name in nodes if re.fullmatch(r'(sdds|affiliate-feed|heron)[1-9][0-9]*', name)),
                    key=lambda name: (int(re.search(r'[0-9]+$', name)[0]), name))
for name in downstream:
    parent = 'replica' + re.search(r'[0-9]+$', name)[0]
    if parent not in replicas:
        raise SystemExit(f'{name} requires upstream {parent}')
all_replicas = replicas + downstream
# Keep private addresses for replication; only the SSH transport uses public IPs.
ssh_common_args = (
    "-o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
    f"-o UserKnownHostsFile={shlex.quote(str(Path.cwd() / 'lab-known-hosts'))}"
)
hosts = {}
for role, node in nodes.items():
    hosts[role] = {
        "ansible_host": node["private_ip"],
        "lab_role": role,
        "dashboard_public_ip": node.get("public_ip", ""),
        "zfs_volume_ids": node.get("zfs_volume_ids", []),
    }
    try:
        ssh_address = ipaddress.IPv4Address(node["public_ip"])
        if ssh_address.is_unspecified or ssh_address.is_loopback or ssh_address.is_multicast:
            raise ValueError("Invalid SSH destination")
    except (KeyError, ValueError) as error:
        raise SystemExit(f"{role} needs a valid public IPv4 address for home SSH") from error
    hosts[role]["ansible_ssh_common_args"] = (
        f"{ssh_common_args} -o Hostname={ssh_address}"
    )
    if role == "primary":
        hosts[role]["mariadb_server_id"] = 1
    elif role in replicas:
        hosts[role]["mariadb_server_id"] = int(role[7:]) + 1
        hosts[role]['replication_upstream'] = 'primary'
        hosts[role]['database_tier'] = 2
    elif role in downstream:
        family, number = re.fullmatch(r'(sdds|affiliate-feed|heron)([1-9][0-9]*)', role).groups()
        hosts[role]['mariadb_server_id'] = {'sdds': 1000, 'affiliate-feed': 2000, 'heron': 3000}[family] + int(number)
        hosts[role]['replication_upstream'] = 'replica' + number
        hosts[role]['database_tier'] = 3

inventory = {
    "all": {
        "vars": {
            "ansible_user": "ubuntu",
            "ansible_python_interpreter": "/usr/bin/python3",
            "ansible_ssh_private_key_file": "/var/lib/jenkins/.ssh/mariadb-lab",
            "ansible_ssh_common_args": (
                "-o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
                f"-o UserKnownHostsFile={os.getcwd()}/lab-known-hosts"
            ),
        },
        "children": {
            "database": {"children": {
                "primary_node": {"hosts": {"primary": hosts["primary"]}},
                "replicas": {"children": {
                    "intermediate_replicas": {"hosts": {name: hosts[name] for name in replicas}},
                    "downstream_replicas": {"hosts": {name: hosts[name] for name in downstream}},
                }},
            }},
            "proxy": {"hosts": {"maxscale": hosts["maxscale"]}},
            "ssm_monitoring": {"hosts": {"ssm": hosts["ssm"]} if "ssm" in hosts else {}},
            "monitoring": {"hosts": {"monitor": hosts["monitor"]} if "monitor" in hosts else {}},
        },
    }
}
Path("inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
Path("replicas.txt").write_text("".join(name + "\n" for name in all_replicas))
Path("database-order.txt").write_text("".join(name + "\n" for name in list(reversed(downstream)) + replicas + ["primary"]))
print("Inventory generated for:", ", ".join(nodes))
