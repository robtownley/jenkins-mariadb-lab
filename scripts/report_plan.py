import json
from pathlib import Path

plan = json.loads(Path("terraform/tfplan.json").read_text())
lines = ["Infrastructure changes requiring review:"]
for resource in plan.get("resource_changes", []):
    actions = resource["change"]["actions"]
    if actions == ["no-op"]:
        continue
    line = f"{','.join(actions):18} {resource['address']}"
    if "delete" in actions and resource["type"] == "aws_ebs_volume":
        line += "  [DELETES DISK DATA AND ITS LOCAL ZFS SNAPSHOTS]"
    lines.append(line)
if len(lines) == 1:
    lines.append("No infrastructure changes.")
summary = "\n".join(lines) + "\n"
Path("terraform/change-summary.txt").write_text(summary)
print(summary)
