#!/bin/bash

# Check if an argument is provided
if [ -z "$1" ]; then
  echo "Usage: $0 <MIDDLEWARE_MODE>"
  echo "Allowed values: maintenance | production | authentication | reporting"
  exit 1
fi

# Assign the argument
MIDDLEWARE_MODE="$1"

# Validate the argument
if [[ "$MIDDLEWARE_MODE" != "maintenance" && "$MIDDLEWARE_MODE" != "production" && "$MIDDLEWARE_MODE" != "authentication" && "$MIDDLEWARE_MODE" != "reporting" ]]; then
  echo "Error: Invalid MIDDLEWARE_MODE '$MIDDLEWARE_MODE'"
  echo "Allowed values: maintenance | production | authentication | reporting"
  exit 1
fi

# Run the docker compose command with the validated environment variable
MIDDLEWARE_MODE="$MIDDLEWARE_MODE" docker compose up -d fastapi-proxy

echo "Docker compose started with MIDDLEWARE_MODE=$MIDDLEWARE_MODE"