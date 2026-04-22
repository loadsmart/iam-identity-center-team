import os
import json
import uuid
import re
import boto3
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
requests_table = dynamodb.Table(os.environ['REQUESTS_TABLE_NAME'])

# Validation patterns (same as createRequests VTL)
ACCOUNT_ID_PATTERN = re.compile(r'^\d{12}$')
ROLE_ID_PATTERN = re.compile(r'^arn:aws:sso:::permissionSet/ssoins-[a-zA-Z0-9-.]{16}/ps-[a-zA-Z0-9-.]{16}$')
EMAIL_PATTERN = re.compile(r'^[^@]+@[^@]+\.[^@]+$')


def validate_input(input_data):
    """Validate input format (same rules as createRequests)"""
    errors = []

    if not input_data.get('username'):
        errors.append('Missing required field: username')

    if not input_data.get('email'):
        errors.append('Missing required field: email')
    elif not EMAIL_PATTERN.match(input_data.get('email', '')):
        errors.append('Invalid email format')

    if not ACCOUNT_ID_PATTERN.match(input_data.get('accountId', '')):
        errors.append('Invalid accountId format (must be 12 digits)')

    if not ROLE_ID_PATTERN.match(input_data.get('roleId', '')):
        errors.append('Invalid roleId format (must be permission set ARN)')

    duration = input_data.get('duration', '')
    if not duration or not duration.isdigit() or int(duration) < 1 or int(duration) > 8000:
        errors.append('Invalid duration (must be 1-8000)')

    role = input_data.get('role', '')
    if not role:
        errors.append('Missing required field: role')
    elif len(role) > 32 or not re.match(r'^[\w+=,.@-]+$', role):
        errors.append('Invalid role format (max 32 chars, alphanumeric)')

    account_name = input_data.get('accountName', '')
    if not account_name:
        errors.append('Missing required field: accountName')
    elif len(account_name) > 50:
        errors.append('accountName too long (max 50)')

    if not input_data.get('startTime'):
        errors.append('Missing required field: startTime')

    return errors


def handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    input_data = event.get('arguments', {}).get('input', {})

    # Validate input format
    errors = validate_input(input_data)
    if errors:
        error_msg = f"Invalid input: {', '.join(errors)}"
        print(f"Validation failed: {error_msg}")
        raise Exception(error_msg)

    # Generate request ID and timestamps
    request_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + 'Z'

    # Transform username: "alice" → "idc_alice"
    username = f"idc_{input_data['username']}"

    # Get machine client ID for audit (from Cognito token claims)
    identity = event.get('identity', {})
    requested_by = identity.get('sub') or identity.get('username') or 'unknown'

    # Build request item
    item = {
        'id': request_id,
        'username': username,
        'email': input_data['email'],
        'accountId': input_data['accountId'],
        'accountName': input_data['accountName'],
        'role': input_data['role'],
        'roleId': input_data['roleId'],
        'startTime': input_data['startTime'],
        'duration': input_data['duration'],
        'justification': input_data.get('justification', ''),
        'ticketNo': input_data.get('ticketNo'),
        'session_duration': input_data.get('session_duration'),
        'status': 'pending',
        'requestedBy': requested_by,
        'createdAt': now,
        'updatedAt': now,
        '__typename': 'requests',
        'owner': username,  # Required for @auth owner rules
    }

    # Remove None values (DynamoDB doesn't accept None)
    item = {k: v for k, v in item.items() if v is not None}

    print(f"Writing request to DynamoDB: {json.dumps(item)}")

    # Write to DynamoDB
    requests_table.put_item(Item=item)

    print(f"Request created successfully: {request_id}")

    return item
