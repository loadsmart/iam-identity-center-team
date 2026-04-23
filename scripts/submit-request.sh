#!/bin/bash
set -euo pipefail

# Load .env if present
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi

# Defaults
USERNAME="${USERNAME:-}"
EMAIL="${EMAIL:-}"
ACCOUNT_ID="${ACCOUNT_ID:-845156828388}"
ACCOUNT_NAME="${ACCOUNT_NAME:-Loadsmart Main}"
ROLE="${ROLE:-Backend}"
ROLE_ARN="${ROLE_ARN:-}"
DURATION="${DURATION:-8}"
JUSTIFICATION="${JUSTIFICATION:-test}"
DRY_RUN=false

usage() {
  cat <<EOF
Usage: $0 [OPTIONS]

Submit an access request via machine auth.

Required:
  --username USER       Username in Identity Center (e.g., raphael.costa)
  --email EMAIL         User's email (must match Identity Center)

Optional:
  --account-id ID       AWS account ID (default: 845156828388)
  --account-name NAME   Account name (default: Loadsmart Main)
  --role ROLE           Role name (default: PowerUserAccess)
  --role-arn ARN        Permission set ARN (required)
  --duration HOURS      Duration in hours (default: 8)
  --justification TEXT  Justification (default: FF operations)
  --dry-run             Show request without submitting

Environment variables (or .env file):
  TOKEN_ENDPOINT, GRAPH_ENDPOINT, CLIENT_ID, CLIENT_SECRET
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
  --username)
    USERNAME="$2"
    shift 2
    ;;
  --email)
    EMAIL="$2"
    shift 2
    ;;
  --account-id)
    ACCOUNT_ID="$2"
    shift 2
    ;;
  --account-name)
    ACCOUNT_NAME="$2"
    shift 2
    ;;
  --role)
    ROLE="$2"
    shift 2
    ;;
  --role-arn)
    ROLE_ARN="$2"
    shift 2
    ;;
  --duration)
    DURATION="$2"
    shift 2
    ;;
  --justification)
    JUSTIFICATION="$2"
    shift 2
    ;;
  --dry-run)
    DRY_RUN=true
    shift
    ;;
  --help | -h) usage ;;
  *)
    echo "Unknown option: $1"
    usage
    ;;
  esac
done

# Validate required
[[ -z "$USERNAME" ]] && {
  echo "Error: --username required"
  exit 1
}
[[ -z "$EMAIL" ]] && {
  echo "Error: --email required"
  exit 1
}
[[ -z "$ROLE_ARN" ]] && {
  echo "Error: --role-arn required"
  exit 1
}
[[ -z "$TOKEN_ENDPOINT" ]] && {
  echo "Error: TOKEN_ENDPOINT not set"
  exit 1
}
[[ -z "$GRAPH_ENDPOINT" ]] && {
  echo "Error: GRAPH_ENDPOINT not set"
  exit 1
}
[[ -z "$CLIENT_ID" ]] && {
  echo "Error: CLIENT_ID not set"
  exit 1
}
[[ -z "$CLIENT_SECRET" ]] && {
  echo "Error: CLIENT_SECRET not set"
  exit 1
}

# Calculate start time (now + 1 minute)
START_TIME=$(date -u -d '+1 minute' '+%Y-%m-%dT%H:%M:%S.000Z' 2>/dev/null || date -u -v+1M '+%Y-%m-%dT%H:%M:%S.000Z')

# Build request
REQUEST=$(
  cat <<EOF
{
  "query": "mutation CreateRequestOnBehalf(\$input: CreateRequestOnBehalfInput!) { createRequestOnBehalf(input: \$input) { id status } }",
  "variables": {
    "input": {
      "username": "$USERNAME",
      "email": "$EMAIL",
      "accountId": "$ACCOUNT_ID",
      "accountName": "$ACCOUNT_NAME",
      "role": "$ROLE",
      "roleId": "$ROLE_ARN",
      "startTime": "$START_TIME",
      "duration": "$DURATION",
      "justification": "$JUSTIFICATION"
    }
  }
}
EOF
)

if $DRY_RUN; then
  echo "=== DRY RUN ==="
  echo "$REQUEST" | jq .
  exit 0
fi

# Get token
echo "Getting access token..."
TOKEN_RESPONSE=$(curl -s -X POST "$TOKEN_ENDPOINT" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=$CLIENT_ID&client_secret=$CLIENT_SECRET&scope=api/admin")

ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token')
if [[ "$ACCESS_TOKEN" == "null" || -z "$ACCESS_TOKEN" ]]; then
  echo "Error getting token:"
  echo "$TOKEN_RESPONSE" | jq .
  exit 1
fi

# Submit request
echo "Submitting request..."
RESPONSE=$(curl -s -X POST "$GRAPH_ENDPOINT" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -d "$REQUEST")

echo "$RESPONSE" | jq .
