# Lab features

Everything here is optional. With the defaults, PLAN and APPLY build exactly the lab described in
[THIRD_TIER.md](THIRD_TIER.md). Turning a feature off again (and running APPLY with `ALLOW_REMOVALS`
ticked, if it added servers) removes it.

## Actions

| ACTION | What it does | Approval |
|---|---|---|
| `PLAN` | Preview Terraform changes, a rough monthly cost, and any servers/disks that would be removed | – |
| `APPLY` | Build or update the lab | Plan approval, plus seed donor approvals |
| `DESTROY` | Remove everything Terraform owns | Plan approval |
| `STOP` | Stop every lab server; disks are kept | – |
| `LOAD_TEST` | Run `LOAD_PROFILE` for `LOAD_MINUTES` and report lag on every replica | – |
| `DRILL` | Run `DRILL` against `DRILL_TARGET`, put things back, verify replication | Yes |
| `BACKUP` | Stream `mariadb-backup` of the last intermediate replica to S3 | – |
| `RESTORE_TEST` | Restore the newest backup into a scratch copy and check it | – |
| `BUILD_IMAGE` | Bake a pre-installed server image with Packer | – |

Any action that needs the servers (APPLY, LOAD_TEST, DRILL, BACKUP, RESTORE_TEST) starts stopped
servers first. Started servers get new public IPs; APPLY's plan updates DNS for them.

Each feature action writes a report that is archived with the build and included in `lab-summary.txt`.

## Settings that stick

Lab-shape settings start each build on the value the previous build used, so running APPLY with
untouched settings never quietly shrinks the lab: `REPLICA_COUNT`, `MULTI_SOURCE`, `LOADGEN_COUNT`,
`DB_INSTANCE_TYPE`, `MARIADB_VERSION`, `USE_LAB_IMAGE`, `CONFIGURE_MONITORING`, plus the load, drill,
backup and nightly settings. `CONFIG_ONLY` and `ALLOW_REMOVALS` never stick.

As a second guard, APPLY refuses a plan that deletes or replaces any server or disk unless
`ALLOW_REMOVALS` is ticked.

New parameters appear in "Build with Parameters" after Jenkins has run this Jenkinsfile once.

## Speed options

* `CONFIG_ONLY` (APPLY): skip Terraform and rerun configuration on the existing servers.
* `CONFIGURE_MONITORING` unticked (APPLY): skip the health endpoint, dashboard and SSM stages.
* `USE_LAB_IMAGE` + `BUILD_IMAGE`: new servers start with MariaDB, ZFS tools, sysbench and the SSM
  client already installed. Existing servers are never replaced (Terraform ignores image changes).

## Multi-source replication (`MULTI_SOURCE`)

Adds two servers:

* `orders-primary`: an independent primary (GTID domain 2, server ID 4001). An event inserts a row
  into `orders.orders` every 5 seconds and purges rows older than 7 days.
* `warehouse`: follows two primaries over named channels:
  * `main` from `primary`: `lab.*` only
  * `orders` from `orders-primary`: `orders.*` only

A fresh warehouse is seeded from a consistent `mariadb-dump --single-transaction` of each source.
Each channel then starts at its dump's GTID. The seed refuses to overwrite a warehouse that already holds data.
APPLY verifies that both channels have no lag, that this build's marker arrived from `primary`, and that new orders keep
arriving. The dashboard draws both incoming channels.

These nodes are not in MaxScale, ZFS mirrors, Sanoid or the seed/migration stages. Their data lives on the
root disk.

## Load generators (`LOADGEN_COUNT`, `LOAD_TEST`)

`LOADGEN_COUNT` adds 1–2 `t3.small` servers with sysbench. APPLY creates `sbtest` (4 tables × 100k rows)
through MaxScale and grants `lab_app` access to it. `LOAD_TEST` then runs, on every load generator:

| `LOAD_PROFILE` | Workload |
|---|---|
| `light` | `oltp_read_write`, 4 threads, 50 tx/s |
| `heavy` | `oltp_read_write`, 16 threads, unthrottled |
| `spiky` | alternates 1 minute light (2 threads, 20 tx/s) and 1 minute heavy |
| `read_only` | autocommit `oltp_read_only`, so MaxScale routes reads to replicas |

The report shows sysbench throughput and latency, the maximum lag per replica and channel (warehouse
included), and MaxScale's backends. LOAD_TEST refuses to start if any ZFS pool is 70% full, because heavy
load writes binlogs on every node (kept 7 days).

## Drills (`DRILL`, `DRILL_TARGET`)

| `DRILL` | What happens |
|---|---|
| `SWITCHOVER_ROUNDTRIP` | MaxScale switches the primary role to `DRILL_TARGET`, a write goes through, then it switches back |
| `FAILOVER_ROUNDTRIP` | MariaDB on `primary` is stopped; MaxScale fails over; `primary` restarts read-only, rejoins, and is switched back |
| `REPLICA_RESTART` | Restart MariaDB on `DRILL_TARGET` |
| `NETWORK_DELAY` | 250 ms ±50 ms latency on `DRILL_TARGET` for 2 minutes (`tc netem`) |
| `SQL_THREAD_PAUSE` | Stop `DRILL_TARGET`'s SQL thread for 2 minutes and watch it catch up |

Safety measures:

* The extra MaxScale privileges that switchover and failover need are granted only for the drill. They are revoked
  once `primary` is the writable master again.
* The network-delay and SQL-pause drills schedule their own undo on the server before they start. The server
  recovers even if Jenkins loses the connection.
* If a cluster drill fails partway, it stops and reports where MaxScale thinks the cluster is, without guessing.
  Inspect the cluster before the next APPLY. A failed failover drill leaves `/etc/mysql/mariadb.conf.d/99-lab-drill.cnf`
  on `primary`, which keeps it read-only until you remove that file.
* Every drill ends with the normal replication verification.

## Backups (`BACKUP`, `RESTORE_TEST`, `BACKUP_KEEP`)

`BACKUP` streams `mariadb-backup --slave-info --safe-slave-backup` from the highest-numbered
intermediate replica. The data goes over SSH, through Jenkins, to
`s3://jenkins-mariadb-lab-robtownley-20261002/mariadb-lab/backups/` (SSE-S3). The servers need no AWS
access. The newest `BACKUP_KEEP` backups are kept.

`RESTORE_TEST` restores the newest backup into `/var/tmp` on that replica and starts a throwaway MariaDB
there. That copy has no networking, binlog or replication. The test checks `lab.pipeline_checks`, runs `CHECK TABLE`,
reports the backup's replication position, and then deletes the copy.

## Cost control

* `STOP` stops all lab servers. Disks are still billed.
* `NIGHTLY_STOP` schedules a STOP at about 23:00 every night. A timer-triggered build always runs STOP,
  whatever the other settings say.
* Every PLAN prints a rough monthly cost (eu-west-2 on-demand prices; an estimate, not a bill).

## One-time setup for some features

* **Hosts helper** (MULTI_SOURCE or LOADGEN_COUNT): the installed `/usr/local/sbin/update-mariadb-lab-hosts`
  must accept the new node names. The pipeline compares it with the repository copy and prints the
  `sudo install …` command when they differ.
* **Route 53 policy**: `route53-lab-policy.json` now includes `orders-primary`, `warehouse`, `loadgen1`
  and `loadgen2`. Update the IAM policy before an APPLY that adds them.
* **IAM for the Jenkins identity**, depending on the features used:
  * STOP/start: `ec2:StartInstances`, `ec2:StopInstances`, `ec2:DescribeInstances`
  * BACKUP/RESTORE_TEST: `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject` on `…/mariadb-lab/backups/*` and
    `s3:ListBucket` for that prefix
  * BUILD_IMAGE/USE_LAB_IMAGE: the usual Packer `amazon-ebs` permissions (run/terminate instances, temporary
    key pair and security group, `ec2:CreateImage`, `ec2:RegisterImage`, `ec2:DeregisterImage`,
    `ec2:DescribeImages`, `ec2:CreateSnapshot`, `ec2:DeleteSnapshot`, `ec2:CreateTags`)
* **Packer** (BUILD_IMAGE): install `packer` on the Jenkins host.

## Validation

`python3 -m unittest discover -s tests` covers the following:

* inventories with and without the new nodes
* the hosts helper
* plan removal and cost reporting
* GTID merging for the warehouse seed
* lag sampling, backup naming and pruning
* image pruning and server start/stop selection

The pipeline also syntax-checks every playbook. None of the AWS-facing features have run against a live lab yet:
start with PLAN, then try each feature on its own.
