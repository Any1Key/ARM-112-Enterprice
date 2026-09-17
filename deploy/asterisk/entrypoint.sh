#!/bin/sh
set -eu
mkdir -p /provision /media /run/asterisk
[ -f /provision/users.conf ] || touch /provision/users.conf
cat > /etc/asterisk/manager.conf <<CONF
[general]
enabled=yes
port=5038
bindaddr=0.0.0.0
[arm112]
secret=${ASTERISK_AMI_SECRET:?AMI secret required}
read=system,call,reporting
write=system,call,originate,command
CONF
exec asterisk -f -vvv
