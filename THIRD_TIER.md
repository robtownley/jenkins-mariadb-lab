# Three-tier MariaDB lab

This is the complete uploaded repository with the third tier added. Merge/replace
its tracked files in your existing Git checkout; do not create a new Terraform
state or change its backend. Existing instance and volume resource keys remain
unchanged.

## Topology

For each REPLICA_COUNT index N:

* `replicaN` follows `primary` (existing tier).
* `sddsN`, `affiliate-feedN`, `heronN` each follow `replicaN`.
* All use a single GTID replication channel, with strict GTID and
  `log_slave_updates=1`. This is cascading replication, not multi-source yet.
* The third-tier nodes are full database copies; their names do not imply
  schema/table replication filters.
* Third-tier nodes are not added to MaxScale's application service or monitor.
  Their health is shown by the topology dashboard and SSM clients.

| Intermediate replicas | Third-tier replicas | Total database servers | Total lab instances |
|---|---|---|---|
| 1 | 3 | 5 | 8 |
| 2 | 6 | 9 | 12 |
| 3 | 9 | 13 | 16 |
| 6 | 18 | 25 | 28 |

The extra three instances are MaxScale, topology monitor and SSM. Each database
has its own two 15 GB mirror disks plus its root disk. Running costs and regional
EC2 vCPU quotas increase accordingly. The existing SSM server remains its current
size; assess its RAM and retention as the monitored fleet grows.

## Before APPLY

1. Commit the updated repository. Keep the existing Jenkins credential IDs.
2. Update the supplemental Route 53 IAM policy using `route53-lab-policy.json`.
   It includes the new names for branches 1 through 6. Preserve the existing
   EC2 and Terraform-state permissions. For tighter scope replace both
   `arn:aws:route53:::hostedzone/*` resources with your roblabb.com zone ARN.
3. Run PLAN with the CURRENT intermediate replica count so Jenkins checks out
   the new files. The count still means intermediates, not total DB servers.
4. On the Jenkins host as root, refresh the installed hosts helper:

   ```bash
   sudo install -o root -g root -m 0755 \
     /var/lib/jenkins/workspace/mariadb-lab/scripts/update_jenkins_hosts.py \
     /usr/local/sbin/update-mariadb-lab-hosts
   ```

5. Review the plan, then run APPLY with the same count. At count 2 it adds six
   database instances, twelve data volumes, twelve attachments and six DNS
   records. Existing database servers/disks should not be replaced by this
   topology change. Other changes depend on current state/drift.

## Seeding and approval

* Fresh destinations are discovered across both replica tiers.
* One eligible intermediate replica supplies the shared seed for all fresh
  destinations. If none is eligible, Jenkins requests primary fallback.
* Jenkins asks approval for the exact donor and destination list. Execution
  rechecks that the selected source and target set match the approved values.
* The selected donor is drained, put into MaxScale maintenance, and verified
  to have zero connections before MariaDB is cleanly stopped for its seed.
  Primary fallback also performs this isolation and pauses application writes.
* Only isolation applied by this run is cleared, after donor recovery checks.
  Failure to recover leaves it isolated for inspection.
* Donor logs name every destination/source pair. A seed donor is a source of a
  consistent data copy; each destination subsequently follows its assigned
  upstream (for example `heron2` follows `replica2`).
* Intermediates connect and catch up first. A new primary marker provides a
  post-seed GTID anchor in intermediate binlogs, then children connect using
  their preserved seed GTID. A final proxy write is verified on every DB.

This keeps the existing conservative refusal to overwrite an unconfigured node
that already contains application data. Broken existing channels, missing retained
binlogs and parent IP changes require inspection; this update does not automatically
reset or reseed them, change upstream, or implement donor retry/failover.

## MaxScale availability

`install.yml` no longer stops/disables MaxScale on every APPLY. Backend membership
is reconciled with MaxCtrl while it runs; direct-tier replicas are added to routing
after replication verification. Fresh MaxScale starts primary-only routing.
The on-disk template and persisted runtime changes retain configuration for future
starts. Runtime monitor/service account credentials are also reconciled.

No MaxScale process restart is requested by normal APPLY. MariaDB maintenance,
a primary seed/migration, package operations, unavailable backends or external
failures can still interrupt database queries. Scaling down uses the existing
Terraform apply-before-Ansible order; this is not a pre-termination drain workflow.

## Snapshots, monitoring and scaling

All new databases inherit mirror preparation, data/log/temp/redo migration checks,
SSH/hosts/MOTD, Route 53 records, local health endpoints and SSM client setup.
All replicas, including children, inherit Sanoid's 24 hourly / 1 daily retention.
The dashboard follows actual replication addresses and uses configured parents
for dashed expected links when channels are unavailable.

Reducing REPLICA_COUNT removes the highest numbered intermediate AND its three
children, including their data disks and local snapshots. DESTROY removes all
Terraform-owned lab instances, volumes and records. No backups are shipped to S3.

## Local validation

`python3 -m unittest discover -s tests -v` checks inventories for 1/2/3/6 branches,
unique stable IDs, parent relationships, hosts-helper idempotency, and mocked
MaxScale runtime membership ordering/idempotency. HCL/YAML parsing and Python
compilation, dashboard JavaScript syntax and all 15 Ansible playbook syntax checks passed. AWS deployment and live MariaDB/MaxScale/SSM
integration are not tested locally; review PLAN and verify through Jenkins.
