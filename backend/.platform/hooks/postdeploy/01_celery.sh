#!/bin/bash
set -e

echo "=== [01_celery.sh] Starting Celery Postdeploy Hook ==="

# 1. Ensure runtime directories exist with webapp ownership
mkdir -p /var/log/celery /var/run/celery
chown -R webapp:webapp /var/log/celery /var/run/celery
chmod 755 /var/log/celery /var/run/celery

# 2. Reload systemd daemon to pick up updated unit configurations
echo "[01_celery.sh] Running systemctl daemon-reload..."
systemctl daemon-reload

# 3. Enable and restart Celery Worker on every EB instance
echo "[01_celery.sh] Enabling and restarting celery-worker.service..."
systemctl enable celery-worker.service
systemctl restart celery-worker.service

# 4. Manage Celery Beat singleton
# In Elastic Beanstalk multi-instance environments, EB_IS_COMMAND_LEADER is 'true' on the leader
# and 'false' on followers. In single-instance environments, it is typically unset or 'true'.
echo "[01_celery.sh] Checking leader status (EB_IS_COMMAND_LEADER='${EB_IS_COMMAND_LEADER:-unset}')..."

if [ "${EB_IS_COMMAND_LEADER}" = "false" ]; then
    echo "[01_celery.sh] Non-leader instance. Ensuring celery-beat.service is stopped and disabled."
    systemctl stop celery-beat.service 2>/dev/null || true
    systemctl disable celery-beat.service 2>/dev/null || true
else
    echo "[01_celery.sh] Leader instance (or single-instance environment). Enabling and restarting celery-beat.service."
    # Remove stale pidfile if left behind by previous process or crash
    rm -f /var/run/celery/beat.pid
    systemctl enable celery-beat.service
    systemctl restart celery-beat.service
fi

echo "=== [01_celery.sh] Celery Postdeploy Hook Completed Successfully ==="
