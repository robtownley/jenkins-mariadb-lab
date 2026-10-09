pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }

    parameters {
        choice(name: 'ACTION', choices: ['PLAN', 'APPLY', 'DESTROY'],
               description: 'Preview, provision, or remove the database lab')
        choice(name: 'REPLICA_COUNT', choices: ['2', '1', '3', '4', '5', '6'],
               description: 'Desired replicas. Reducing this destroys the highest numbered replicas and their data disks. Select the CURRENT count for DESTROY.')
    }

    environment {
        AWS_DEFAULT_REGION = 'eu-west-2'
        TF_IN_AUTOMATION = 'true'
        TF_INPUT = 'false'
        TF_VAR_replica_count = "${params.REPLICA_COUNT ?: '2'}"
    }

    stages {
        stage('Checkout') {
            steps {
                deleteDir()
                checkout scm
            }
        }

        stage('Check Jenkins hosts updater') {
            when { expression { params.ACTION != 'PLAN' } }
            steps {
                sh '''
                    set -eu
                    test -x /usr/local/sbin/update-mariadb-lab-hosts
                    sudo -n -l /usr/local/sbin/update-mariadb-lab-hosts >/dev/null
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
                        terraform init -no-color
                        terraform fmt
                        terraform validate -no-color
                    '''
                }
                sh '''
                    set -eu
                    /opt/jenkins-mariadb-venv/bin/ansible-playbook \
                        -i 'localhost,' ansible/configure.yml --syntax-check
                '''
                archiveArtifacts(artifacts: 'terraform/.terraform.lock.hcl', fingerprint: true)
            }
        }

        stage('Plan infrastructure') {
            steps {
                dir('terraform') {
                    sh '''
                        set -eu
                        PLAN_MODE=""
                        if [ "$ACTION" = "DESTROY" ]; then
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

        stage('Approve changes') {
            when { expression { params.ACTION != 'PLAN' } }
            steps {
                timeout(time: 30, unit: 'MINUTES') {
                    input(
                        message: "Review plan.txt and change-summary.txt. Execute ${params.ACTION} with ${params.REPLICA_COUNT ?: '2'} replicas? Removed replicas lose their disks and local snapshots. APPLY seeds from a healthy ZFS replica first. Primary seeding requires a separate approval if no suitable replica exists. Primary storage migrations can pause writes.",
                        ok: 'Execute approved plan', submitter: 'rob'
                    )
                }
            }
        }

        stage('Execute saved plan') {
            when { expression { params.ACTION != 'PLAN' } }
            steps {
                dir('terraform') {
                    sh 'terraform apply -no-color -lock-timeout=60s tfplan'
                }
            }
        }

        stage('Report instances') {
            when { expression { params.ACTION == 'APPLY' } }
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
            when { expression { params.ACTION != 'PLAN' } }
            steps {
                sh '''
                    set -eu
                    if [ "$ACTION" = "DESTROY" ]; then
                        printf '%s\n' '{}' | sudo -n /usr/local/sbin/update-mariadb-lab-hosts
                    else
                        sudo -n /usr/local/sbin/update-mariadb-lab-hosts < nodes.json
                    fi
                '''
            }
        }

        stage('Install database software') {
            when { expression { params.ACTION == 'APPLY' } }
            steps {
                sh '/opt/jenkins-mariadb-venv/bin/ansible-playbook -i inventory.json ansible/install.yml'
            }
        }

        stage('Prepare ZFS mirrors') {
            when { expression { params.ACTION == 'APPLY' } }
            steps {
                sh '/opt/jenkins-mariadb-venv/bin/ansible-playbook -i inventory.json ansible/prepare_zfs.yml'
            }
        }

        stage('Configure hostnames and SSH hopping') {
            when { expression { params.ACTION == 'APPLY' } }
            steps {
                sh '/opt/jenkins-mariadb-venv/bin/ansible-playbook -i inventory.json ansible/node_access.yml'
            }
        }

        stage('Configure, seed and verify topology') {
            when { expression { params.ACTION == 'APPLY' } }
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
                            /opt/jenkins-mariadb-venv/bin/ansible-playbook \
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
    if any(not isinstance(node, str) or not re.fullmatch(r'replica[1-9][0-9]*', node) for node in targets):
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
                        } else {
                            echo(seedDecision.needs_seed
                                ? "Replica donor selected: ${seedDecision.source}; primary approval is unnecessary."
                                : 'No fresh replicas require seeding.')
                        }
                        withEnv(["ALLOW_PRIMARY_SEED=${primarySeedApproved}", 'SEED_PLAN_ONLY=false']) {
                            sh '''
                                set +x
                                set -eu
                                /opt/jenkins-mariadb-venv/bin/ansible-playbook \
                                    -i inventory.json ansible/configure.yml
                            '''
                        }
                    }
                }
            }
        }

        stage('Migrate MariaDB to ZFS') {
            when { expression { params.ACTION == 'APPLY' } }
            steps {
                sh '''
                    set -eu
                    while IFS= read -r node; do
                        /opt/jenkins-mariadb-venv/bin/ansible-playbook \
                            -i inventory.json ansible/migrate_mysql_zfs.yml --limit "$node"
                    done < database-order.txt
                '''
            }
        }

        stage('Verify replicas after migration') {
            when { expression { params.ACTION == 'APPLY' } }
            steps {
                sh '/opt/jenkins-mariadb-venv/bin/ansible-playbook -i inventory.json ansible/verify_replication.yml --limit replicas'
            }
        }

        stage('Move logs and temporary files to ZFS datasets') {
            when { expression { params.ACTION == 'APPLY' } }
            steps {
                sh '''
                    set -eu
                    while IFS= read -r node; do
                        /opt/jenkins-mariadb-venv/bin/ansible-playbook \
                            -i inventory.json ansible/migrate_mysql_log_paths.yml --limit "$node"
                    done < database-order.txt
                    /opt/jenkins-mariadb-venv/bin/ansible-playbook \
                        -i inventory.json ansible/verify_replication.yml --limit replicas
                '''
            }
        }

        stage('Migrate InnoDB redo to ZFS') {
            when { expression { params.ACTION == 'APPLY' } }
            steps {
                sh '''
                    set -eu
                    while IFS= read -r node; do
                        /opt/jenkins-mariadb-venv/bin/ansible-playbook \
                            -i inventory.json ansible/migrate_mysql_redo.yml --limit "$node"
                    done < database-order.txt
                    /opt/jenkins-mariadb-venv/bin/ansible-playbook \
                        -i inventory.json ansible/verify_replication.yml --limit replicas
                '''
            }
        }


        stage('Configure replica snapshot retention') {
            when { expression { params.ACTION == 'APPLY' } }
            steps {
                sh '''
                    set -eu
                    /opt/jenkins-mariadb-venv/bin/ansible-playbook \
                        -i inventory.json ansible/configure_sanoid.yml
                '''
            }
        }

    }

    post {
        success { echo "Lab action completed: ${params.ACTION}, replicas=${params.REPLICA_COUNT ?: '2'}" }
        always {
            sh 'rm -f terraform/tfplan terraform/tfplan.json .seed-transfer/seed.tar.gz .seed-transfer/seed.zfs seed-plan.json'
        }
    }
}
