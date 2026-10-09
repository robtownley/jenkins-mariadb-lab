import json
import os
import re
from pathlib import Path

nodes = json.loads(Path("nodes.json").read_text())
if "primary" not in nodes or "maxscale" not in nodes:
    raise SystemExit("Terraform output must include primary and maxscale.")

unexpected = [name for name in nodes if name not in ("primary", "maxscale", "monitor", "ssm")
              and not re.fullmatch(r"replica[1-9][0-9]*", name)]
if unexpected:
    raise SystemExit(f"Unexpected node names: {unexpected}")

replicas = sorted((name for name in nodes if name.startswith("replica")),
                  key=lambda name: int(name[7:]))
hosts = {}
for role, node in nodes.items():
    hosts[role] = {
        "ansible_host": node["private_ip"],
        "lab_role": role,
        "dashboard_public_ip": node.get("public_ip", ""),
        "zfs_volume_ids": node.get("zfs_volume_ids", []),
    }
    if role == "primary":
        hosts[role]["mariadb_server_id"] = 1
    elif role in replicas:
        hosts[role]["mariadb_server_id"] = int(role[7:]) + 1

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
                "replicas": {"hosts": {name: hosts[name] for name in replicas}},
            }},
            "proxy": {"hosts": {"maxscale": hosts["maxscale"]}},
            "ssm_monitoring": {"hosts": {"ssm": hosts["ssm"]} if "ssm" in hosts else {}},
            "monitoring": {"hosts": {"monitor": hosts["monitor"]} if "monitor" in hosts else {}},
        },
    }
}
Path("inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
Path("replicas.txt").write_text("".join(name + "\n" for name in replicas))
Path("database-order.txt").write_text("".join(name + "\n" for name in replicas + ["primary"]))
print("Inventory generated for:", ", ".join(["primary"] + replicas + ["maxscale"]))
