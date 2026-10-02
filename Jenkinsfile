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
