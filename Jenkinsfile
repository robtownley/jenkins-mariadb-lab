// Puts the value used by the previous build first, so a choice "sticks" between builds.
// This stops an APPLY with the default REPLICA_COUNT from quietly removing a branch you added earlier.
def sticky(current, options) {
    (current != null && options.contains(current.toString())) ? [current.toString()] + (options - current.toString()) : options
}

def flag(value, fallback) {
    value == null ? fallback : value
}

pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }

    triggers {
        // Only scheduled while the last build had NIGHTLY_STOP ticked. A timer build always runs ACTION=STOP.
        cron(params.NIGHTLY_STOP == true ? 'H 23 * * *' : '')
    }

    parameters {
        choice(name: 'ACTION',
               choices: ['PLAN', 'APPLY', 'DESTROY', 'STOP', 'LOAD_TEST', 'DRILL', 'BACKUP', 'RESTORE_TEST', 'BUILD_IMAGE'],
               description: '''PLAN/APPLY/DESTROY: preview, provision or remove the lab.
STOP: stop every lab server (disks kept; the next server action starts them again).
LOAD_TEST: run LOAD_PROFILE for LOAD_MINUTES from the load generators and report replication lag.
DRILL: run the DRILL below and verify recovery.
BACKUP / RESTORE_TEST: stream a mariadb-backup of the last intermediate replica to S3 / restore the newest one and check it.
BUILD_IMAGE: bake a pre-installed server image with Packer.''')

        // Lab shape. These stick between builds; the plan still shows every change before APPLY.
        choice(name: 'REPLICA_COUNT', choices: sticky(params.REPLICA_COUNT, ['2', '1', '3', '4', '5', '6']),
               description: 'Intermediate replicas; each adds sddsN, affiliate-feedN and heronN. Reducing destroys that entire branch and its disks.')
        booleanParam(name: 'MULTI_SOURCE', defaultValue: flag(params.MULTI_SOURCE, false),
                     description: 'Add orders-primary (an independent primary) and warehouse (replicates lab.* from primary and orders.* from orders-primary).')
        choice(name: 'LOADGEN_COUNT', choices: sticky(params.LOADGEN_COUNT, ['0', '1', '2']),
               description: 'Load generator servers (sysbench through MaxScale). Needed for LOAD_TEST.')
        choice(name: 'DB_INSTANCE_TYPE', choices: sticky(params.DB_INSTANCE_TYPE, ['t3.small', 't3.medium', 't3.large', 'm6i.large']),
               description: 'Size of every MariaDB server. Changing it stops and restarts each server in place (disks kept).')
        choice(name: 'MARIADB_VERSION', choices: sticky(params.MARIADB_VERSION, ['11.4', '10.11', '11.8']),
               description: 'MariaDB series for NEW servers. Existing servers must already run this series (in-place upgrades are refused).')
        booleanParam(name: 'USE_LAB_IMAGE', defaultValue: flag(params.USE_LAB_IMAGE, false),
                     description: 'Build NEW servers from the newest BUILD_IMAGE image for MARIADB_VERSION (much faster installs).')
        booleanParam(name: 'CONFIGURE_MONITORING', defaultValue: flag(params.CONFIGURE_MONITORING, true),
                     description: 'Run the health endpoint, dashboard and SSM stages. Untick for a faster APPLY when only the databases changed.')

        // Per-build switches. These never stick.
        booleanParam(name: 'CONFIG_ONLY', defaultValue: false,
                     description: 'APPLY only: skip Terraform plan/apply and rerun configuration against the servers that already exist.')
        booleanParam(name: 'ALLOW_REMOVALS', defaultValue: false,
                     description: 'APPLY only: allow a plan that deletes or replaces servers or disks. Leave unticked unless you meant to shrink the lab.')

        // Feature settings.
        choice(name: 'LOAD_PROFILE', choices: sticky(params.LOAD_PROFILE, ['light', 'heavy', 'spiky', 'read_only']),
               description: 'LOAD_TEST workload: light (4 threads, 50 tx/s), heavy (16 threads, unthrottled), spiky (alternates every minute), read_only (autocommit reads, routed to replicas).')
        choice(name: 'LOAD_MINUTES', choices: sticky(params.LOAD_MINUTES, ['5', '10', '15', '30']),
               description: 'LOAD_TEST duration.')
        choice(name: 'DRILL', choices: sticky(params.DRILL, ['SWITCHOVER_ROUNDTRIP', 'FAILOVER_ROUNDTRIP', 'REPLICA_RESTART', 'NETWORK_DELAY', 'SQL_THREAD_PAUSE']),
               description: '''SWITCHOVER_ROUNDTRIP: MaxScale promotes DRILL_TARGET, then switches back to primary.
FAILOVER_ROUNDTRIP: stop the primary, MaxScale fails over, the primary rejoins and is switched back.
REPLICA_RESTART: restart MariaDB on DRILL_TARGET.
NETWORK_DELAY: add 250ms latency to DRILL_TARGET for 2 minutes.
SQL_THREAD_PAUSE: pause DRILL_TARGET's SQL thread for 2 minutes and watch it catch up.''')
        string(name: 'DRILL_TARGET', defaultValue: params.DRILL_TARGET ?: 'replica1',
               description: 'Replica for the drill, for example replica1 or heron2. Switchover needs an intermediate replica.')
        choice(name: 'BACKUP_KEEP', choices: sticky(params.BACKUP_KEEP, ['7', '3', '14']),
               description: 'BACKUP keeps this many newest backups in S3 and deletes older ones.')
        booleanParam(name: 'NIGHTLY_STOP', defaultValue: flag(params.NIGHTLY_STOP, false),
                     description: 'Stop every lab server at about 23:00 each night to save money. Takes effect after one build with it ticked.')
    }

    environment {
        AWS_DEFAULT_REGION = 'eu-west-2'
        TF_IN_AUTOMATION = 'true'
        TF_INPUT = 'false'
        TF_VAR_replica_count = "${params.REPLICA_COUNT ?: '2'}"
        TF_VAR_multi_source_enabled = "${params.MULTI_SOURCE == true}"
        TF_VAR_loadgen_count = "${params.LOADGEN_COUNT ?: '0'}"
        TF_VAR_db_instance_type = "${params.DB_INSTANCE_TYPE ?: 't3.small'}"
        TF_VAR_mariadb_version = "${params.MARIADB_VERSION ?: '11.4'}"
        TF_VAR_use_lab_image = "${params.USE_LAB_IMAGE == true}"
        LAB_MARIADB_VERSION = "${params.MARIADB_VERSION ?: '11.4'}"
        // Survives deleteDir() so providers are not downloaded on every build.
        TF_PLUGIN_CACHE_DIR = '/var/lib/jenkins/.terraform.d/plugin-cache'
        // The lock file is regenerated each build (it is not committed), so allow the cache to be used for it.
        TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE = 'true'
        ANSIBLE_PLAYBOOK = '/opt/jenkins-mariadb-venv/bin/ansible-playbook'
        BACKUP_S3_URI = 's3://jenkins-mariadb-lab-robtownley-20261002/mariadb-lab/backups'
    }

    stages {
        stage('Choose action') {
            steps {
                script {
                    def timer = !currentBuild.getBuildCauses('hudson.triggers.TimerTrigger$TimerTriggerCause').isEmpty()
                    env.LAB_ACTION = timer ? 'STOP' : (params.ACTION ?: 'PLAN')
                    if (params.CONFIG_ONLY == true && env.LAB_ACTION != 'APPLY') {
                        error('CONFIG_ONLY can only be used with ACTION=APPLY.')
                    }
                    if (!((params.DRILL_TARGET ?: 'replica1') ==~ /(?:replica|sdds|affiliate-feed|heron)[1-9][0-9]?/)) {
                        error('DRILL_TARGET must be a replica name such as replica1 or heron2.')
                    }
                    // Actions that need the servers running and an inventory.
                    env.NEEDS_SERVERS = "${env.LAB_ACTION in ['APPLY', 'LOAD_TEST', 'DRILL', 'BACKUP', 'RESTORE_TEST']}"
                    // Actions that go through Terraform plan, review and apply.
                    env.USES_PLAN = "${env.LAB_ACTION in ['PLAN', 'APPLY', 'DESTROY'] && params.CONFIG_ONLY != true}"
                    currentBuild.description = timer ? 'Nightly STOP' : env.LAB_ACTION + (params.CONFIG_ONLY ? ' (config only)' : '')
                    echo "Action: ${env.LAB_ACTION}${timer ? ' (nightly timer)' : ''}"
                }
            }
        }

        stage('Checkout') {
            steps {
                deleteDir()
                checkout scm
            }
        }

        stage('Check Jenkins hosts updater') {
            when { expression { env.LAB_ACTION in ['APPLY', 'DESTROY'] } }
            steps {
                sh '''
                    set -eu
                    test -x /usr/local/sbin/update-mariadb-lab-hosts
                    sudo -n -l /usr/local/sbin/update-mariadb-lab-hosts >/dev/null
                    if ! cmp -s scripts/update_jenkins_hosts.py /usr/local/sbin/update-mariadb-lab-hosts; then
                        echo "The installed hosts helper differs from scripts/update_jenkins_hosts.py. Refresh it as root:"
                        echo "  sudo install -o root -g root -m 0755 $WORKSPACE/scripts/update_jenkins_hosts.py /usr/local/sbin/update-mariadb-lab-hosts"
                        if [ "$TF_VAR_multi_source_enabled" = true ] || [ "$TF_VAR_loadgen_count" != 0 ]; then
                            echo "The old helper rejects the new node names, so stopping here."
                            exit 1
                        fi
                    fi
                '''
            }
        }

        stage('Check AWS identity') {
            steps { sh 'aws sts get-caller-identity' }
        }

        stage('Initialize and validate') {
            steps {
                dir('terraform') {
                    sh '''
                        set -eu
                        mkdir -p "$TF_PLUGIN_CACHE_DIR"
                        terraform init -no-color
                        terraform fmt -check -diff -recursive -no-color
                        terraform validate -no-color
                    '''
                }
                sh '''
                    set -eu
                    python3 -m unittest discover -s tests
                    # Every playbook (files with a top-level play); included task files are checked through them.
                    for playbook in $(grep -l '^  hosts:' ansible/*.yml); do
                        "$ANSIBLE_PLAYBOOK" \
                            -i 'localhost,' "$playbook" --syntax-check
                    done
                '''
                archiveArtifacts(artifacts: 'terraform/.terraform.lock.hcl', fingerprint: true)
            }
        }

        stage('Start stopped lab servers') {
            when { expression { env.NEEDS_SERVERS == 'true' } }
            steps {
                sh '''
                    set -eu
                    python3 scripts/lab_power.py start
                    # A started server has a new public IP. A Terraform plan picks that up; otherwise refresh state for the inventory.
                    if [ -f instances-started ] && [ "$USES_PLAN" != true ]; then
                        terraform -chdir=terraform apply -refresh-only -auto-approve -no-color -lock-timeout=60s
                    fi
                '''
            }
        }

        stage('Plan infrastructure') {
            when { expression { env.USES_PLAN == 'true' } }
            steps {
                dir('terraform') {
                    sh '''
                        set -eu
                        PLAN_MODE=""
                        if [ "$LAB_ACTION" = "DESTROY" ]; then
                            PLAN_MODE="-destroy"
                        fi
                        terraform plan $PLAN_MODE -no-color \
                            -lock-timeout=60s -out=tfplan
                        terraform show -no-color tfplan > plan.txt
                        terraform show -json tfplan > tfplan.json
                    '''
                }
                sh 'python3 scripts/report_plan.py'
                archiveArtifacts artifacts: 'terraform/plan.txt,terraform/change-summary.txt'
            }
        }

        stage('Check for removals') {
            when { expression { env.USES_PLAN == 'true' && env.LAB_ACTION == 'APPLY' } }
            steps {
                script {
                    def removed = readFile('terraform/removals.txt').trim()
                    if (removed && params.ALLOW_REMOVALS != true) {
                        error("This plan deletes or replaces servers/disks:\n${removed}\nIf that is intended, rerun with ALLOW_REMOVALS ticked.")
                    }
                }
            }
        }

        stage('Approve changes') {
            when { expression { env.USES_PLAN == 'true' && env.LAB_ACTION != 'PLAN' } }
            steps {
                timeout(time: 30, unit: 'MINUTES') {
                    input(
                        message: "Review plan.txt and change-summary.txt. Execute ${env.LAB_ACTION} with ${params.REPLICA_COUNT ?: '2'} replicas? Removed replicas lose their disks and local snapshots. APPLY seeds from a healthy ZFS replica first. Every seed donor requires a separate explicit approval. Primary storage migrations can pause writes.",
                        ok: 'Execute approved plan', submitter: 'rob'
                    )
                }
            }
        }

        stage('Execute saved plan') {
            when { expression { env.USES_PLAN == 'true' && env.LAB_ACTION != 'PLAN' } }
            steps {
                dir('terraform') {
                    sh 'terraform apply -no-color -lock-timeout=60s tfplan && touch ../infrastructure-applied'
                }
            }
        }

        stage('Report instances') {
            when { expression { env.NEEDS_SERVERS == 'true' } }
            steps {
                dir('terraform') {
                    sh '''
                        set -eu
                        terraform output -no-color
                        terraform output -json nodes > ../nodes.json
                    '''
                }
                sh 'python3 scripts/make_inventory.py'
                archiveArtifacts artifacts: 'nodes.json,replicas.txt,database-order.txt'
            }
        }

        stage('Update Jenkins hosts entries') {
            when { expression { env.LAB_ACTION in ['APPLY', 'DESTROY'] } }
            steps {
                sh '''
                    set -eu
                    if [ "$LAB_ACTION" = "DESTROY" ]; then
                        printf '%s\n' '{}' | sudo -n /usr/local/sbin/update-mariadb-lab-hosts
                    else
                        sudo -n /usr/local/sbin/update-mariadb-lab-hosts < nodes.json
                    fi
                '''
            }
        }

        stage('Install database software') {
            when { expression { env.LAB_ACTION == 'APPLY' } }
            steps {
                sh '"$ANSIBLE_PLAYBOOK" -i inventory.json ansible/install.yml'
            }
        }

        stage('Prepare ZFS mirrors') {
            when { expression { env.LAB_ACTION == 'APPLY' } }
            steps {
                sh '"$ANSIBLE_PLAYBOOK" -i inventory.json ansible/prepare_zfs.yml'
            }
        }

        stage('Configure hostnames and SSH hopping') {
            when { expression { env.LAB_ACTION == 'APPLY' } }
            steps {
                sh '"$ANSIBLE_PLAYBOOK" -i inventory.json ansible/node_access.yml'
            }
        }

        stage('Configure, seed and verify topology') {
            when { expression { env.LAB_ACTION == 'APPLY' } }
            steps {
                withCredentials([
                    string(credentialsId: 'mariadb-lab-repl-password', variable: 'LAB_REPL_PASSWORD'),
                    string(credentialsId: 'mariadb-lab-monitor-password', variable: 'LAB_MONITOR_PASSWORD'),
                    string(credentialsId: 'mariadb-lab-service-password', variable: 'LAB_SERVICE_PASSWORD'),
                    string(credentialsId: 'mariadb-lab-app-password', variable: 'LAB_APP_PASSWORD')
                ]) {
                    sh '''
                        set +x
                        set -eu
                        SEED_PLAN_ONLY=true ALLOW_PRIMARY_SEED=false \
                            "$ANSIBLE_PLAYBOOK" \
                            -i inventory.json ansible/configure.yml --tags seed_plan
                    '''
                    script {
                        def primarySeedApproved = false
                        def seedFields = sh(
                            returnStdout: true,
                            script: '''
                                set +x
                                python3 - <<'PYSEED'
import json
import re
from pathlib import Path
plan = json.loads(Path('seed-plan.json').read_text())
needs_seed = plan.get('needs_seed')
if not isinstance(needs_seed, bool):
    raise SystemExit('Invalid seed decision')
source = plan.get('source') or '-'
method = plan.get('method') or '-'
targets = plan.get('targets', [])
if needs_seed:
    if not re.fullmatch(r'primary|replica[1-9][0-9]*', source):
        raise SystemExit('Invalid seed source')
    if method not in ('zfs', 'cold') or not isinstance(targets, list) or not targets:
        raise SystemExit('Invalid seed method or targets')
    if any(not isinstance(node, str) or not re.fullmatch(r'(?:replica|sdds|affiliate-feed|heron)[1-9][0-9]*', node) for node in targets):
        raise SystemExit('Invalid seed target')
else:
    source, method, targets = '-', '-', []
print('true' if needs_seed else 'false')
print(source)
print(method)
print(','.join(targets) if targets else '-')
PYSEED
                            '''
                        ).trim().split('\n')
                        def seedDecision = [
                            needs_seed: seedFields[0] == 'true',
                            source: seedFields[1],
                            method: seedFields[2],
                            targets: seedFields[3] == '-' ? [] : seedFields[3].split(',').toList()
                        ]
                        if (seedDecision.needs_seed && seedDecision.source == 'primary') {
                            echo "Primary seed requested for: ${seedDecision.targets.join(', ')}; method=${seedDecision.method}"
                            timeout(time: 30, unit: 'MINUTES') {
                                input(
                                    message: "No suitable replica donor is available. Seed ${seedDecision.targets.join(', ')} from PRIMARY using ${seedDecision.method}? This stops MariaDB on the primary and pauses application writes. A cold copy keeps it stopped during copying; a ZFS snapshot restarts it before transfer.",
                                    ok: 'Approve primary seeding',
                                    submitter: 'rob'
                                )
                            }
                            primarySeedApproved = true
                        } else if (seedDecision.needs_seed) {
                            timeout(time: 30, unit: 'MINUTES') {
                                input(
                                    message: "Approve ${seedDecision.source} as seed donor for ${seedDecision.targets.join(', ')}? This exact donor will be drained and placed into MaxScale maintenance before MariaDB is stopped for the ZFS snapshot.",
                                    ok: 'Approve selected donor', submitter: 'rob'
                                )
                            }
                        } else {
                            echo 'No fresh replicas require seeding.'
                        }
                        withEnv(["ALLOW_PRIMARY_SEED=${primarySeedApproved}", 'SEED_PLAN_ONLY=false',
                                 "APPROVED_SEED_SOURCE=${seedDecision.source}",
                                 "APPROVED_SEED_TARGETS=${seedDecision.targets.join(',')}"]) {
                            sh '''
                                set +x
                                set -eu
                                "$ANSIBLE_PLAYBOOK" \
                                    -i inventory.json ansible/configure.yml
                            '''
                        }
                    }
                }
            }
        }

        stage('Migrate MariaDB to ZFS') {
            when { expression { env.LAB_ACTION == 'APPLY' } }
            steps {
                // serial: 1 over the rollout_order group: one node at a time, children first, primary last.
                sh '"$ANSIBLE_PLAYBOOK" -i inventory.json ansible/migrate_mysql_zfs.yml'
            }
        }

        stage('Verify replicas after migration') {
            when { expression { env.LAB_ACTION == 'APPLY' } }
            steps {
                sh '"$ANSIBLE_PLAYBOOK" -i inventory.json ansible/verify_replication.yml --limit replicas'
            }
        }

        stage('Move logs and temporary files to ZFS datasets') {
            when { expression { env.LAB_ACTION == 'APPLY' } }
            steps {
                sh '''
                    set -eu
                    "$ANSIBLE_PLAYBOOK" \
                        -i inventory.json ansible/migrate_mysql_log_paths.yml
                    "$ANSIBLE_PLAYBOOK" \
                        -i inventory.json ansible/verify_replication.yml --limit replicas
                '''
            }
        }

        stage('Migrate InnoDB redo to ZFS') {
            when { expression { env.LAB_ACTION == 'APPLY' } }
            steps {
                sh '''
                    set -eu
                    "$ANSIBLE_PLAYBOOK" \
                        -i inventory.json ansible/migrate_mysql_redo.yml
                    "$ANSIBLE_PLAYBOOK" \
                        -i inventory.json ansible/verify_replication.yml --limit replicas
                '''
            }
        }


        stage('Configure replica snapshot retention') {
            when { expression { env.LAB_ACTION == 'APPLY' } }
            steps {
                sh '''
                    set -eu
                    "$ANSIBLE_PLAYBOOK" \
                        -i inventory.json ansible/configure_sanoid.yml
                '''
            }
        }

        stage('Configure multi-source replication') {
            when { expression { env.LAB_ACTION == 'APPLY' && params.MULTI_SOURCE == true } }
            steps {
                withCredentials([
                    string(credentialsId: 'mariadb-lab-repl-password', variable: 'LAB_REPL_PASSWORD'),
                    string(credentialsId: 'mariadb-lab-monitor-password', variable: 'LAB_MONITOR_PASSWORD'),
                    string(credentialsId: 'mariadb-lab-service-password', variable: 'LAB_SERVICE_PASSWORD'),
                    string(credentialsId: 'mariadb-lab-app-password', variable: 'LAB_APP_PASSWORD')
                ]) {
                    sh '''
                        set +x
                        set -eu
                        "$ANSIBLE_PLAYBOOK" -i inventory.json ansible/multi_source.yml
                    '''
                }
            }
        }

        stage('Configure load generators') {
            when { expression { env.LAB_ACTION == 'APPLY' && (params.LOADGEN_COUNT ?: '0') != '0' } }
            steps {
                withCredentials([string(credentialsId: 'mariadb-lab-app-password', variable: 'LAB_APP_PASSWORD')]) {
                    sh '''
                        set +x
                        set -eu
                        "$ANSIBLE_PLAYBOOK" -i inventory.json ansible/loadgen.yml --tags setup
                    '''
                }
            }
        }

        stage('Configure cluster monitoring') {
            when { expression { env.LAB_ACTION == 'APPLY' && params.CONFIGURE_MONITORING != false } }
            steps {
                sh '"$ANSIBLE_PLAYBOOK" -i inventory.json ansible/configure_monitoring.yml'
            }
        }


        stage('Publish HTTPS dashboard') {
            when { expression { env.LAB_ACTION == 'APPLY' && params.CONFIGURE_MONITORING != false } }
            steps {
                withCredentials([string(credentialsId: 'mariadb-lab-dashboard-password', variable: 'LAB_DASHBOARD_PASSWORD')]) {
                    sh '''
                        set +x
                        set -eu
                        "$ANSIBLE_PLAYBOOK" -i inventory.json ansible/public_dashboard.yml
                    '''
                }
            }
        }

        stage('Configure Shattered Silicon Monitoring') {
            when { expression { env.LAB_ACTION == 'APPLY' && params.CONFIGURE_MONITORING != false } }
            steps {
                withCredentials([
                    string(credentialsId: 'mariadb-lab-ssm-password', variable: 'LAB_SSM_PASSWORD'),
                    string(credentialsId: 'mariadb-lab-ssm-mysql-password', variable: 'LAB_SSM_MYSQL_PASSWORD')
                ]) {
                    sh '''
                        set +x
                        set -eu
                        "$ANSIBLE_PLAYBOOK" -i inventory.json ansible/configure_ssm_server.yml
                        "$ANSIBLE_PLAYBOOK" -i inventory.json ansible/public_ssm.yml
                        "$ANSIBLE_PLAYBOOK" -i inventory.json ansible/configure_ssm_clients.yml
                    '''
                }
            }
        }

        stage('Run load test') {
            when { expression { env.LAB_ACTION == 'LOAD_TEST' } }
            steps {
                withCredentials([string(credentialsId: 'mariadb-lab-app-password', variable: 'LAB_APP_PASSWORD')]) {
                    sh '''
                        set +x
                        set -eu
                        "$ANSIBLE_PLAYBOOK" -i inventory.json ansible/loadgen.yml --tags setup,run \
                            -e load_profile="$LOAD_PROFILE" -e load_minutes="$LOAD_MINUTES"
                    '''
                }
                archiveArtifacts artifacts: 'load_test-report.txt', allowEmptyArchive: true
            }
        }

        stage('Run drill') {
            when { expression { env.LAB_ACTION == 'DRILL' } }
            steps {
                timeout(time: 30, unit: 'MINUTES') {
                    input(
                        message: "Run ${params.DRILL} on ${params.DRILL_TARGET}? Switchover and failover drills briefly move or stop the primary; the others disturb one replica. Each drill puts things back and then verifies replication.",
                        ok: 'Run drill', submitter: 'rob'
                    )
                }
                withCredentials([
                    string(credentialsId: 'mariadb-lab-repl-password', variable: 'LAB_REPL_PASSWORD'),
                    string(credentialsId: 'mariadb-lab-monitor-password', variable: 'LAB_MONITOR_PASSWORD'),
                    string(credentialsId: 'mariadb-lab-service-password', variable: 'LAB_SERVICE_PASSWORD'),
                    string(credentialsId: 'mariadb-lab-app-password', variable: 'LAB_APP_PASSWORD')
                ]) {
                    sh '''
                        set +x
                        set -eu
                        "$ANSIBLE_PLAYBOOK" -i inventory.json ansible/drills.yml \
                            -e drill="$DRILL" -e drill_target="$DRILL_TARGET"
                    '''
                }
                archiveArtifacts artifacts: 'drill-report.txt', allowEmptyArchive: true
            }
        }

        stage('Back up to S3') {
            when { expression { env.LAB_ACTION == 'BACKUP' } }
            steps {
                sh 'python3 scripts/lab_backup.py backup --keep "$BACKUP_KEEP"'
                archiveArtifacts artifacts: 'backup-report.txt', allowEmptyArchive: true
            }
        }

        stage('Restore test') {
            when { expression { env.LAB_ACTION == 'RESTORE_TEST' } }
            steps {
                sh 'python3 scripts/lab_backup.py restore-test'
                archiveArtifacts artifacts: 'restore_test-report.txt', allowEmptyArchive: true
            }
        }

        stage('Stop lab servers') {
            when { expression { env.LAB_ACTION == 'STOP' } }
            steps {
                sh 'python3 scripts/lab_power.py stop'
            }
        }

        stage('Build server image') {
            when { expression { env.LAB_ACTION == 'BUILD_IMAGE' } }
            steps {
                sh '''
                    set -eu
                    if ! command -v packer >/dev/null; then
                        echo "Packer is not installed on this Jenkins host. Install it once, for example:"
                        echo "  sudo apt-get install packer   (after adding the HashiCorp apt repository)"
                        exit 1
                    fi
                    packer init packer
                    packer build -color=false -var "mariadb_version=$LAB_MARIADB_VERSION" packer
                    python3 scripts/lab_images.py report-and-prune --keep 2
                '''
                archiveArtifacts artifacts: 'build_image-report.txt', allowEmptyArchive: true
            }
        }

    }

    post {
        success {
            sh 'python3 scripts/build_summary.py --result SUCCESS'
            archiveArtifacts artifacts: 'lab-summary.txt', allowEmptyArchive: true
        }
        failure {
            sh 'python3 scripts/build_summary.py --result FAILURE'
            archiveArtifacts artifacts: 'lab-summary.txt', allowEmptyArchive: true
        }
        cleanup {
            sh 'rm -f terraform/tfplan terraform/tfplan.json .seed-transfer/seed.tar.gz .seed-transfer/seed.zfs seed-plan.json'
        }
    }
}
