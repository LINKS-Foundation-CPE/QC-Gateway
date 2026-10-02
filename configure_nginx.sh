#!/usr/bin/env bash
# Render this deployment's nginx vhost files from the templates in
# config/nginx/site-confs/.
#
# The templates are named after the service they front, not after a domain:
# every host name comes from .env, so the same templates work for any
# deployment. Each rendered file is named after the host it answers on, which is
# what SWAG picks up from config/nginx/site-confs/.
#
# A deployment that does not run one of these upstreams can drop the
# corresponding template (and its line below); nothing else depends on it.

set -a && source .env

TPL=./config/nginx/site-confs
DOMAINS='$FRONTEND_URL $JOB_PORTAL_API_URL $DASHBOARD_URL'

# Vhosts whose host name is configured explicitly.
envsubst "$DOMAINS" < $TPL/api.conf.template > $TPL/$JOB_PORTAL_API_URL.conf
envsubst "$DOMAINS" < $TPL/dashboard.conf.template > $TPL/$DASHBOARD_URL.conf
envsubst "$DOMAINS \$MACHINE_URL \$AUTH_URL" < $TPL/frontend.conf.template > $TPL/$FRONTEND_URL.conf

# Vhosts published as <service>.$BASE_DOMAIN.
for service in grafana prometheus store status jobs rng; do
  envsubst '$BASE_DOMAIN' < $TPL/$service.conf.template > $TPL/$service.$BASE_DOMAIN.conf
done

# The overview vhost fronts a service this deployment runs beside the gateway,
# the way grafana is fronted, so it is rendered only when there is somewhere to
# send the traffic. OVERVIEW_UPSTREAM is a full upstream URL — with SWAG on the
# host network a local service is http://127.0.0.1:<port>.
if [ -n "${OVERVIEW_UPSTREAM:-}" ]; then
  envsubst '$BASE_DOMAIN $OVERVIEW_UPSTREAM' < $TPL/overview.conf.template > $TPL/overview.$BASE_DOMAIN.conf
fi

# The docs vhost only issues redirects, so it is rendered only when this
# deployment has somewhere to redirect to (DOCS_URL, and optionally
# INTERNAL_DOCS_URL served under /private).
if [ -n "${DOCS_URL:-}" ]; then
  # An empty substitution would render `return 301 ;`, which nginx rejects, so
  # /private falls back to the public docs when there is no internal site.
  INTERNAL_DOCS_URL=${INTERNAL_DOCS_URL:-$DOCS_URL}
  envsubst '$BASE_DOMAIN $DOCS_URL $INTERNAL_DOCS_URL' < $TPL/docs.conf.template > $TPL/docs.$BASE_DOMAIN.conf
fi

# fail2ban's ignoreip list carries this deployment's own trusted ranges, so it
# is rendered here too rather than committed. The deploy copies the rendered
# file, so this must run before the sync (the CI pipeline already does).
envsubst '$FAIL2BAN_IGNOREIP_EXTRA' \
  < ./config/fail2ban/jail.local.template > ./config/fail2ban/jail.local
