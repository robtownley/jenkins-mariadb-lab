import json
import os
from pathlib import Path

# Approximate eu-west-2 on-demand prices (USD). Used only for a rough estimate in the plan summary.
HOURLY = {
    "t3.micro": 0.0118, "t3.small": 0.0236, "t3.medium": 0.0472, "t3.large": 0.0944,
    "t3.xlarge": 0.1888, "m6i.large": 0.111, "m6i.xlarge": 0.222,
}
GP3_GB_MONTH = 0.0928
HOURS_PER_MONTH = 730
# Deleting these loses a server or its data.
REMOVAL_TYPES = {"aws_instance", "aws_ebs_volume"}


def removals(plan):
    """Instances and volumes the plan deletes or replaces."""
    return [r["address"] for r in plan.get("resource_changes", [])
            if r["type"] in REMOVAL_TYPES and "delete" in r["change"]["actions"]]


def monthly_cost(plan):
    """Rough monthly cost of everything the plan leaves running, as (instances, storage)."""
    resources = []

    def collect(module):
        resources.extend(module.get("resources", []))
        for child in module.get("child_modules", []):
            collect(child)

    collect(plan.get("planned_values", {}).get("root_module", {}))
    compute = storage = 0.0
    unknown = set()
    for resource in resources:
        values = resource.get("values") or {}
        if resource["type"] == "aws_instance":
            kind = values.get("instance_type")
            if kind in HOURLY:
                compute += HOURLY[kind] * HOURS_PER_MONTH
            else:
                unknown.add(kind)
            for disk in values.get("root_block_device") or []:
                storage += (disk.get("volume_size") or 0) * GP3_GB_MONTH
        elif resource["type"] == "aws_ebs_volume":
            storage += (values.get("size") or 0) * GP3_GB_MONTH
    return compute, storage, sorted(k for k in unknown if k)


def summarise(plan, action):
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
    removed = removals(plan)
    if removed and action != "DESTROY":
        lines += ["", f"WARNING: this plan deletes or replaces {len(removed)} servers/disks.",
                  "APPLY will refuse to run unless ALLOW_REMOVALS is ticked."]
    compute, storage, unknown = monthly_cost(plan)
    if action != "DESTROY":
        lines += ["", f"Rough running cost after this plan: ~${compute + storage:,.0f}/month "
                      f"(instances ~${compute:,.0f}, disks ~${storage:,.0f}; on-demand, 24x7, before tax/transfer)."]
        if unknown:
            lines.append("  No price listed for: " + ", ".join(unknown))
    return "\n".join(lines) + "\n", removed


if __name__ == "__main__":
    plan = json.loads(Path("terraform/tfplan.json").read_text())
    summary, removed = summarise(plan, os.environ.get("LAB_ACTION", os.environ.get("ACTION", "")))
    Path("terraform/change-summary.txt").write_text(summary)
    Path("terraform/removals.txt").write_text("".join(address + "\n" for address in removed))
    print(summary)
