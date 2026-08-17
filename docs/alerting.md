# Alerting Configuration

## Overview

RetailMind uses Prometheus for metrics collection and alert evaluation, and
Alertmanager for alert routing, grouping, and notification delivery.

**Architecture:**
```
Prometheus → evaluates alert rules → sends to Alertmanager
Alertmanager → groups, routes, inhibits → sends notifications (email/Slack)
```

## Current Alerts

Defined in `infra/monitoring/alerts.yml`:

### Availability Alerts

**ApiDown** (critical)
- **Condition:** API has not responded to scrape for 2 minutes
- **Action:** Check `docker compose ps` and API container logs
- **Severity:** Critical
- **Repeat:** Every 1 hour until resolved

**HighErrorRate** (critical)
- **Condition:** >5% of requests return 5xx errors (5-minute window)
- **Action:** Read recent error-level logs with request_id
- **Severity:** Critical
- **Repeat:** Every 1 hour until resolved
- **Note:** Only counts 5xx errors (not 403s, which indicate probing)

**SlowRequests** (warning)
- **Condition:** P95 latency >5 seconds (5-minute window, sustained for 10 minutes)
- **Action:** Check if slowness is on investigation/report endpoints (expected)
- **Severity:** Warning
- **Repeat:** Every 12 hours until resolved

### Work Alerts

**NoDetectionSweep** (critical)
- **Condition:** No detection sweep completed in 2 hours
- **Action:** Check beat and worker containers
- **Severity:** Critical
- **Repeat:** Every 1 hour until resolved
- **Why it matters:** Silence looks exactly like "nothing wrong", which is why it pages

## Configuring Alert Routing

### Email Alerts

1. **Set SMTP credentials:**
   ```bash
   # infra/secrets/smtp_password
   echo "your_smtp_password" > infra/secrets/smtp_password
   chmod 600 infra/secrets/smtp_password
   ```

2. **Set environment variables in `.env` or `compose.prod.yml`:**
   ```bash
   RM_SMTP_HOST=smtp.example.com
   RM_SMTP_USER=alerts
   RM_ALERT_EMAIL=ops@example.com
   RM_ALERT_EMAIL_CRITICAL=oncall@example.com
   RM_ALERT_EMAIL_DBA=dba@example.com
   RM_ALERT_EMAIL_DATA=data@example.com
   RM_DOMAIN=retailmind.example.com
   ```

3. **Restart Alertmanager:**
   ```bash
   docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml \
     restart alertmanager
   ```

### Slack Alerts

1. **Create a Slack Incoming Webhook:**
   - Go to https://api.slack.com/apps
   - Create new app → "From scratch"
   - Add "Incoming Webhooks" feature
   - Create webhook for your channel (e.g., `#alerts-critical`)
   - Copy the webhook URL

2. **Save webhook URL to secrets:**
   ```bash
   echo "https://hooks.slack.com/services/YOUR/WEBHOOK/URL" > infra/secrets/slack_webhook
   chmod 600 infra/secrets/slack_webhook
   ```

3. **Set channel name:**
   ```bash
   RM_SLACK_CHANNEL_CRITICAL=#alerts-critical
   ```

4. **Restart Alertmanager:**
   ```bash
   docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml \
     restart alertmanager
   ```

### PagerDuty Integration (Optional)

Add to `alertmanager.yml` receivers:

```yaml
  - name: 'critical-alerts'
    pagerduty_configs:
      - service_key_file: '/run/secrets/pagerduty_key'
        severity: 'critical'
        description: '{{ .GroupLabels.alertname }}: {{ (index .Alerts 0).Annotations.summary }}'
        details:
          firing: '{{ .Alerts.Firing | len }}'
          action: '{{ (index .Alerts 0).Annotations.action }}'
```

Add secret:

```bash
echo "your_pagerduty_integration_key" > infra/secrets/pagerduty_key
chmod 600 infra/secrets/pagerduty_key
```

Update `compose.prod.yml`:

```yaml
  alertmanager:
    secrets: [smtp_password, slack_webhook, pagerduty_key]
```

## Alert Routing Rules

Alerts are routed based on labels:

### By Severity

- **critical** → `critical-alerts` receiver (email + Slack, 1-hour repeat)
- **warning** → `warning-alerts` receiver (email only, 12-hour repeat)
- **info** → `default` receiver (email only, 4-hour repeat)

### By Component

- **postgres/database** → `dba-team` receiver
- **pipeline/etl/ingestion** → `data-team` receiver
- **default** → `default` receiver

### Inhibition (Alert Suppression)

Some alerts suppress others to reduce noise:

- **ApiDown** suppresses **HighErrorRate** (errors are expected when API is down)
- **Critical database alerts** suppress **warning database alerts**

## Alert Grouping

Alerts are grouped by:
- `alertname` (e.g., all `ApiDown` alerts together)
- `severity` (critical vs warning)
- `component` (api, postgres, worker, etc.)

**Group Wait Times:**
- Critical: 10 seconds (fast notification)
- Warning: 5 minutes (batch similar warnings)
- Default: 30 seconds

## Testing Alerts

### Manual Test: Fire a Fake Alert

```bash
# Simulate ApiDown alert
curl -X POST http://localhost:9093/api/v1/alerts \
  -H 'Content-Type: application/json' \
  -d '[{
    "labels": {
      "alertname": "TestAlert",
      "severity": "warning",
      "component": "test"
    },
    "annotations": {
      "summary": "This is a test alert",
      "action": "No action required, this is a test"
    },
    "startsAt": "'$(date --rfc-3339=seconds | sed 's/ /T/')'",
    "endsAt": "'$(date -d '+5 minutes' --rfc-3339=seconds | sed 's/ /T/')'"
  }]'
```

Expected result: Notification sent to `default` receiver within 30 seconds.

### Trigger Real Alert: Slow Down API

```bash
# Stop API to trigger ApiDown
docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml stop api

# Wait 2 minutes (alert `for` duration)
# Check Alertmanager UI: http://localhost:9093
# Check email/Slack

# Restore API
docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml start api
```

Expected:
1. After 2 minutes: `ApiDown` alert fires
2. Within 10 seconds: Notification sent (critical group_wait)
3. After API starts: Alert resolves
4. Resolved notification sent

## Viewing Active Alerts

### Alertmanager UI

Access at http://localhost:9093 (via SSH tunnel in production):

```bash
# Create SSH tunnel
ssh -L 9093:localhost:9093 your-server

# Open in browser
open http://localhost:9093
```

**UI Features:**
- View active alerts
- Silence alerts temporarily
- View alert history
- Test receivers

### Prometheus UI

Access at http://localhost:9090 (via SSH tunnel):

```bash
ssh -L 9090:localhost:9090 your-server
open http://localhost:9090/alerts
```

**Prometheus shows:**
- Alert rules and their current state
- How long until alert fires (`for` duration)
- Alert query results (PromQL)

### CLI

```bash
# List active alerts
docker compose exec prometheus \
  wget -qO- http://alertmanager:9093/api/v1/alerts | jq

# Check Prometheus alert state
docker compose exec prometheus \
  wget -qO- http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | {alert: .labels.alertname, state: .state}'
```

## Silencing Alerts

**Use cases:**
- Planned maintenance
- Known issue being worked on
- Noisy alert during investigation

**Via UI:**

1. Go to http://localhost:9093
2. Find the alert
3. Click "Silence"
4. Set duration and reason
5. Submit

**Via CLI:**

```bash
# Silence ApiDown for 2 hours
docker compose exec alertmanager \
  amtool silence add \
    alertname=ApiDown \
    --duration=2h \
    --author="ops" \
    --comment="Planned maintenance"

# List active silences
docker compose exec alertmanager amtool silence query

# Expire a silence
docker compose exec alertmanager amtool silence expire <silence-id>
```

## Adding New Alerts

1. **Define the alert rule** in `infra/monitoring/alerts.yml`:

```yaml
  - name: your_group
    interval: 60s
    rules:
      - alert: YourAlertName
        expr: your_promql_expression > threshold
        for: 5m
        labels:
          severity: warning
          component: your_component
        annotations:
          summary: "Brief description of what's wrong"
          action: "What to do about it"
```

2. **Test the PromQL expression** in Prometheus UI:
   - Go to http://localhost:9090
   - Enter your expression
   - Verify it returns expected results

3. **Reload Prometheus configuration:**
   ```bash
   docker compose exec prometheus \
     wget --post-data='' -qO- http://localhost:9090/-/reload
   ```

4. **Verify the alert appears:**
   - Go to http://localhost:9090/alerts
   - Check that your alert is listed (state: inactive/pending/firing)

5. **Wait for alert to fire** (or trigger it manually)

6. **Check notification delivery**

7. **Document the alert** (add to this file)

## Alert Best Practices

### Writing Alert Rules

**DO:**
- Use `for` duration to avoid flapping (5m minimum for most alerts)
- Include actionable `action` annotation (what to do when it fires)
- Use meaningful `summary` (what's wrong, in plain English)
- Set appropriate severity (critical = page, warning = ticket, info = log)
- Add `component` label for routing

**DON'T:**
- Alert on things nobody can act on
- Use very short `for` durations (<1m) - causes noise
- Create alerts without `action` annotations
- Alert on symptoms of other alerts (use inhibition instead)
- Page for warnings (use email/Slack)

### Alert Fatigue

**Signs of alert fatigue:**
- People ignore notifications
- Alerts are silenced immediately without investigation
- High volume of "false positives"

**Solutions:**
- Increase `for` duration to reduce flapping
- Adjust thresholds based on actual behavior
- Add inhibition rules to suppress redundant alerts
- Delete alerts that never lead to action
- Use warning severity for non-urgent issues

### On-Call Runbook

For each critical alert, document:

1. **What it means** (root cause)
2. **How to investigate** (specific commands)
3. **How to fix** (step-by-step)
4. **How to prevent** (long-term fix)

Example: `ApiDown`

```
What: API container is not responding to Prometheus scrapes
Investigate:
  docker compose ps api
  docker compose logs api --tail 100
Fix:
  docker compose restart api
Prevent:
  - Check recent deployments
  - Review API error logs for crashes
  - Check resource limits (memory OOM?)
```

## Monitoring Alertmanager

Alertmanager itself should be monitored:

```yaml
  - alert: AlertmanagerDown
    expr: up{job="alertmanager"} == 0
    for: 2m
    labels:
      severity: critical
    annotations:
      summary: "Alertmanager is down"
      action: "Check alertmanager container. Alerts are not being routed."
```

**Metrics to track:**
- `alertmanager_alerts` - Number of active alerts
- `alertmanager_notifications_total` - Notification delivery count
- `alertmanager_notifications_failed_total` - Failed notification count
- `up{job="alertmanager"}` - Alertmanager availability

## Troubleshooting

### Alerts Not Firing

1. **Check Prometheus alert state:**
   ```bash
   # Should show your alert in 'firing' state
   curl -s http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.alertname=="YourAlert")'
   ```

2. **Check Prometheus is sending to Alertmanager:**
   ```bash
   # Should list alerts
   curl -s http://localhost:9093/api/v1/alerts | jq
   ```

3. **Check Prometheus alertmanager config:**
   ```bash
   curl -s http://localhost:9090/api/v1/alertmanagers | jq
   ```

### Notifications Not Sent

1. **Check Alertmanager logs:**
   ```bash
   docker compose logs alertmanager --tail 50
   ```

2. **Check receiver configuration:**
   ```bash
   docker compose exec alertmanager cat /etc/alertmanager/alertmanager.yml
   ```

3. **Test SMTP connection:**
   ```bash
   docker compose exec alertmanager sh
   / # apk add curl
   / # curl -v telnet://your-smtp-host:587
   ```

4. **Check secrets are mounted:**
   ```bash
   docker compose exec alertmanager ls -la /run/secrets/
   ```

### Duplicate Notifications

**Cause:** Multiple Prometheus instances sending to same Alertmanager.

**Fix:** Use external labels to deduplicate:
```yaml
# prometheus.yml
global:
  external_labels:
    cluster: 'prod'
    replica: 'A'  # Different for each Prometheus instance
```

### Notification Delay

**Expected behavior:**
- Critical alerts: 10-second group_wait
- Warning alerts: 5-minute group_wait

If longer:
- Check `group_interval` and `repeat_interval` in alertmanager.yml
- Check Alertmanager is running: `docker compose ps alertmanager`
- Check for silences: http://localhost:9093/#/silences

## Next Steps

1. **Configure SMTP** (email notifications)
2. **Configure Slack** (critical alerts)
3. **Test alert delivery** (fire test alert, verify receipt)
4. **Document on-call runbook** (what to do for each alert)
5. **Add more alerts** (ETL failures, database issues, forecast staleness)
6. **Monitor alert fatigue** (track silence rate, response time)

---

**Last Updated:** 2026-08-14
**Owner:** Infrastructure Team
**Review Schedule:** Monthly
