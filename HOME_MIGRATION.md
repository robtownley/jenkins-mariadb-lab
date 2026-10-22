# Move the MariaDB lab Jenkins controller home

Prepared for home public IPv4 89.127.224.199. Run installation commands as root on home Ubuntu.

## What changed

- Four existing security groups gain a separate TCP/22 rule for 89.127.224.199/32 only. Existing EC2 security-group rules are preserved.
- Inventory keeps ansible_host as the private IP used by replication, monitoring and node-to-node SSH. Per-host SSH options connect to the public IPv4 instead. The same options apply to SSH/SCP/SFTP.
- jenkins.roblabb.com uses home_jenkins_ipv4 instead of looking up the old EC2 instance. This changes DNS only; it does not install HTTPS or configure router forwarding.
- Existing topology and S3 backend are unchanged.

## Install and commit

Keep old EC2 Jenkins stopped. Do not run a build until the changes are pushed to the branch configured in the Jenkins job.

Save the downloaded archive to /root/mariadb-lab-home-ready.tar.gz. Back up the current checkout, then extract:

```bash
cd /var/lib/jenkins/workspace/mariadb-lab
tar --exclude=.git --exclude=.terraform -czf /root/mariadb-lab-before-home-changes.tar.gz .
tar -xzf /root/mariadb-lab-home-ready.tar.gz -C /var/lib/jenkins/workspace/mariadb-lab
chown -R jenkins:jenkins /var/lib/jenkins/workspace/mariadb-lab
sudo -u jenkins -H git diff --stat
sudo -u jenkins -H git add terraform/main.tf terraform/dns.tf terraform/home_jenkins.tf scripts/make_inventory.py tests/test_topology.py HOME_MIGRATION.md
sudo -u jenkins -H git commit -m 'Support home Jenkins SSH access and remove EC2 DNS dependency'
sudo -u jenkins -H git push
```

If Git requests an author identity, configure the Jenkins user's Git identity before committing. If push fails, retain this checkout and fix authentication or push these changes from your normal development clone. A build's deleteDir()/checkout scm will discard unpushed workspace changes.

## Validate locally

```bash
cd /var/lib/jenkins/workspace/mariadb-lab
sudo -u jenkins -H test -r /var/lib/jenkins/.ssh/mariadb-lab
sudo -u jenkins -H test -r /var/lib/jenkins/.ssh/mariadb-lab.pub
sudo -u jenkins -H aws sts get-caller-identity
sudo -u jenkins -H python3 -m unittest discover -s tests -v
sudo -u jenkins -H /opt/jenkins-mariadb-venv/bin/ansible-playbook -i localhost, ansible/configure.yml --syntax-check
sudo -u jenkins -H terraform -chdir=terraform init
sudo -u jenkins -H terraform -chdir=terraform fmt -check
sudo -u jenkins -H terraform -chdir=terraform validate
```

If Terraform formatting differs, run terraform fmt, commit and push its changes too. Do not regenerate the existing SSH key: its public half is managed by Terraform and the nodes already trust the original key.

The copied Jenkins home includes credentials and keys held there. Ansible, Terraform, AWS CLI and the root-owned hosts helper are installed separately as discussed. If syntax-check reports a missing community.mysql collection, install it as Jenkins with /opt/jenkins-mariadb-venv/bin/ansible-galaxy collection install community.mysql and repeat the check.

## First build

Start home Jenkins with systemctl start jenkins and access http://HOME-LAN-IP:8080 using your existing login. Set Manage Jenkins > System > Jenkins URL to the LAN URL you use. Keep the EC2 Jenkins service stopped.

Run ACTION=PLAN with the ACTUAL current REPLICA_COUNT. Check plan.txt: expected migration changes are four security-group updates and the Jenkins DNS record. Investigate unexpected instance/disk replacements or deletions before applying. Initial SSH access will not work until the security-group rules have been applied.

Then run ACTION=APPLY with the same current count, review the saved plan and approve it. Existing seed approvals still apply; the migration does not bypass them. An APPLY also runs the existing configuration playbooks, so it is more than a networking change. Latest Ansible may expose compatibility issues in existing playbooks; no live AWS apply has been tested here.

After the security-group changes, a direct transport test is possible in the checkout:

```bash
sudo -u jenkins -H bash -c 'cd /var/lib/jenkins/workspace/mariadb-lab && terraform -chdir=terraform output -json nodes > nodes.json && python3 scripts/make_inventory.py && /opt/jenkins-mariadb-venv/bin/ansible all -i inventory.json -m ansible.builtin.ping'
```

If SSH times out, check home IPv4, public instance addresses, subnet internet routes and network ACLs. Permission denied usually means the SSH user/key needs checking.

## Operational notes

- If the home public IPv4 changes, update home_jenkins_ipv4 (its default is in terraform/home_jenkins.tf), commit/push and apply. It controls both SSH /32 rules and Jenkins DNS.
- No database port has been opened to home or the public internet.
- /etc/hosts aliases installed by the existing helper still resolve to private IPs; plain ssh replica1 from home needs a separate SSH config or VPN. The Ansible public-IP override does not change those aliases.
- Seed files are fetched to Jenkins and uploaded to targets by the existing playbooks. Those transfers will now traverse your home connection, so allow disk space and transfer time.
- Keep the old Jenkins security group sg-0243f521e59c4d971: the Terraform configuration still references it. The old EC2 instance itself is no longer looked up for DNS, but leave it available until home builds work.
- Jenkins webhooks using the old public URL will need a separate connectivity solution; LAN-only Jenkins can use manual builds or SCM polling.

## Verification performed on this package

Four offline unit tests passed, including a new check that every inventory host retains its private replication IP while SSH uses its public IP. Python source compilation and Ansible YAML parsing passed. Terraform/Ansible executables and AWS access were unavailable in the preparation environment: run the local validation commands above before applying.
