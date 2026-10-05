import json
import os

with open("nodes.json") as source:
    nodes = json.load(source)

hosts = {}
for role, node in nodes.items():
        hosts[role] = {
        "ansible_host": node["private_ip"],
        "lab_role": role,
        "zfs_volume_ids": node.get("zfs_volume_ids", []),
    }

inventory = {
    "all": {
        "vars": {
            "ansible_user": "ubuntu",
            "ansible_python_interpreter": "/usr/bin/python3",
            "ansible_ssh_private_key_file":
                "/var/lib/jenkins/.ssh/mariadb-lab",
            "ansible_ssh_common_args": (
                "-o IdentitiesOnly=yes "
                "-o StrictHostKeyChecking=accept-new "
                f"-o UserKnownHostsFile={os.getcwd()}/lab-known-hosts"
            ),
        },
        "children": {
            "database": {
                "hosts": {
                    role: hosts[role]
                    for role in ("primary", "replica1", "replica2")
                }
            },
            "proxy": {
                "hosts": {"maxscale": hosts["maxscale"]}
            },
        },
    }
}

with open("inventory.json", "w") as target:
    json.dump(inventory, target, indent=2)

print("Inventory generated for:", ", ".join(hosts))
