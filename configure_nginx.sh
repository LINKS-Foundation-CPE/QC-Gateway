set -a && source .env
envsubst '$FRONTEND_URL $JOB_PORTAL_API_URL $DASHBOARD_URL' < ./config/nginx/site-confs/api.linksfoundation.com.conf.template > ./config/nginx/site-confs/$JOB_PORTAL_API_URL.conf
envsubst '$FRONTEND_URL $JOB_PORTAL_API_URL $DASHBOARD_URL' < ./config/nginx/site-confs/dashboard.linksfoundation.com.conf.template > ./config/nginx/site-confs/$DASHBOARD_URL.conf
envsubst '$FRONTEND_URL $JOB_PORTAL_API_URL $DASHBOARD_URL $MACHINE_URL $AUTH_URL' < ./config/nginx/site-confs/spark.linksfoundation.com.conf.template > ./config/nginx/site-confs/$FRONTEND_URL.conf
envsubst '$BASE_DOMAIN' < ./config/nginx/site-confs/grafana.linksfoundation.com.conf.template > ./config/nginx/site-confs/grafana.$BASE_DOMAIN.conf
envsubst '$BASE_DOMAIN' < ./config/nginx/site-confs/prometheus.linksfoundation.com.conf.template > ./config/nginx/site-confs/prometheus.$BASE_DOMAIN.conf
envsubst '$BASE_DOMAIN' < ./config/nginx/site-confs/store.linksfoundation.com.conf.template > ./config/nginx/site-confs/store.$BASE_DOMAIN.conf
envsubst '$BASE_DOMAIN' < ./config/nginx/site-confs/docs.linksfoundation.com.conf.template > ./config/nginx/site-confs/docs.$BASE_DOMAIN.conf
envsubst '$BASE_DOMAIN' < ./config/nginx/site-confs/status.linksfoundation.com.conf.template > ./config/nginx/site-confs/status.$BASE_DOMAIN.conf
envsubst '$BASE_DOMAIN' < ./config/nginx/site-confs/jobs.linksfoundation.com.conf.template > ./config/nginx/site-confs/jobs.$BASE_DOMAIN.conf
envsubst '$BASE_DOMAIN' < ./config/nginx/site-confs/rng.linksfoundation.com.conf.template > ./config/nginx/site-confs/rng.$BASE_DOMAIN.conf