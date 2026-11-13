#!/bin/bash
# Runs as root inside the Packer build instance. Keep the pinned URLs and checksum in step with
# ansible/install.yml and ansible/configure_ssm_clients.yml.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

cloud-init status --wait
apt-get update
apt-get install -y ca-certificates curl gnupg apt-transport-https python3-pymysql zfsutils-linux rsync sysbench

# MariaDB repository, exactly as install.yml configures it.
curl -fsSL -o /usr/local/sbin/mariadb_repo_setup https://r.mariadb.com/downloads/mariadb_repo_setup
echo "b54c87edfe81b9837ef44a4a4f39383dd8df32776e6a18c0743a5d3ece044ac3  /usr/local/sbin/mariadb_repo_setup" | sha256sum -c -
chmod 0755 /usr/local/sbin/mariadb_repo_setup
/usr/local/sbin/mariadb_repo_setup --mariadb-server-version="mariadb-${MARIADB_VERSION}" --skip-maxscale --skip-tools
apt-get install -y mariadb-server mariadb-client mariadb-backup
# Database nodes enable it in install.yml; MaxScale and load generators must not run a server.
systemctl disable --now mariadb

# Shattered Silicon Monitoring client, as configure_ssm_clients.yml configures it.
curl -fsSL -o /usr/share/keyrings/SS-SSM-9.5-archive-keyring.pgp \
    https://dl.shatteredsilicon.net/ssm/9.5/DEBS/SS-SSM-9.5-archive-keyring.pgp
echo "deb [signed-by=/usr/share/keyrings/SS-SSM-9.5-archive-keyring.pgp] https://dl.shatteredsilicon.net/ssm/9.5/DEBS/ stable main" \
    > /etc/apt/sources.list.d/ss-ssm-9.5.list
apt-get update
apt-get install -y ssm-client

mariadbd --version
apt-get clean
# Each new server regenerates host keys and runs first-boot setup.
cloud-init clean --logs
rm -f /etc/ssh/ssh_host_*
