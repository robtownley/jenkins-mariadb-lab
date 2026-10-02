pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }

    parameters {
        choice(
            name: 'ACTION',
            choices: ['PLAN', 'APPLY', 'DESTROY'],
            description: 'Preview, provision, or remove the database lab'
        )
    }

    environment {
        AWS_DEFAULT_REGION = 'eu-west-2'
        TF_IN_AUTOMATION = 'true'
        TF_INPUT = 'false'
    }

    stages {
        stage('Checkout') {
            steps {
                deleteDir()
                checkout scm
            }
        }

        stage('Check AWS identity') {
            steps {
                sh 'aws sts get-caller-identity'
            }
        }

        stage('Initialize and validate') {
            steps {
                dir('terraform') {
                    sh '''
                        terraform init -no-color
                        terraform fmt
                        terraform validate -no-color
                    '''
                }

                archiveArtifacts(
                    artifacts: 'terraform/.terraform.lock.hcl',
                    fingerprint: true
                )
            }
        }

        stage('Plan infrastructure') {
            steps {
                dir('terraform') {
                    sh '''
                        PLAN_MODE=""

                        if [ "$ACTION" = "DESTROY" ]; then
                            PLAN_MODE="-destroy"
                        fi

                        terraform plan $PLAN_MODE \
                            -no-color \
                            -lock-timeout=60s \
                            -out=tfplan

                        terraform show -no-color tfplan > plan.txt
                    '''
                }

                archiveArtifacts artifacts: 'terraform/plan.txt'
            }
        }

        stage('Approve changes') {
            when {
                expression { params.ACTION != 'PLAN' }
            }
            steps {
                timeout(time: 30, unit: 'MINUTES') {
                    input(
                        message: "Review plan.txt. Execute ${params.ACTION} for the MariaDB lab?",
                        ok: 'Execute approved plan',
                        submitter: 'rob'
                    )
                }
            }
        }

        stage('Execute saved plan') {
            when {
                expression { params.ACTION != 'PLAN' }
            }
            steps {
                dir('terraform') {
                    sh '''
                        terraform apply \
                            -no-color \
                            -lock-timeout=60s \
                            tfplan
                    '''
                }
            }
        }

        stage('Report instances') {
            when {
                expression { params.ACTION == 'APPLY' }
            }
            steps {
                dir('terraform') {
                    sh '''
                        terraform output -no-color
                        terraform output -json nodes > ../nodes.json
                    '''
                }

                archiveArtifacts artifacts: 'nodes.json'
            }
        }
        stage('Install database software') {
            when {
                expression { params.ACTION == 'APPLY' }
            }
            steps {
                sh '''
                    python3 scripts/make_inventory.py

                    /opt/jenkins-mariadb-venv/bin/ansible-playbook \
                        -i inventory.json \
                        ansible/install.yml
                '''
            }
        }
        stage('Configure and verify topology') {
            when {
                expression { params.ACTION == 'APPLY' }
            }
            steps {
                withCredentials([
                    string(
                        credentialsId: 'mariadb-lab-repl-password',
                        variable: 'LAB_REPL_PASSWORD'
                    ),
                    string(
                        credentialsId: 'mariadb-lab-monitor-password',
                        variable: 'LAB_MONITOR_PASSWORD'
                    ),
                    string(
                        credentialsId: 'mariadb-lab-service-password',
                        variable: 'LAB_SERVICE_PASSWORD'
                    ),
                    string(
                        credentialsId: 'mariadb-lab-app-password',
                        variable: 'LAB_APP_PASSWORD'
                    )
                ]) {
                    sh '''
                        set +x

                        /opt/jenkins-mariadb-venv/bin/ansible-playbook \
                            -i inventory.json \
                            ansible/configure.yml
                    '''
                }
            }
        }
    }

    post {
        success {
            echo "Infrastructure action completed: ${params.ACTION}"
        }
        always {
            sh 'rm -f terraform/tfplan'
        }
    }
}
