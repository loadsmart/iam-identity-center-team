# © 2023 Amazon Web Services, Inc. or its affiliates. All Rights Reserved.
# This AWS Content is provided subject to the terms of the AWS Customer Agreement available at
# http: // aws.amazon.com/agreement or other written agreement between Customer and either
# Amazon Web Services, Inc. or Amazon Web Services EMEA SARL or both.
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


def get_sso_instance():
    """Get Identity Store ID from SSO Admin"""
    client = boto3.client('sso-admin')
    response = client.list_instances()
    return response['Instances'][0]


def get_user_from_identity_center(identity_store_id, username):
    """Look up user in Identity Center, return UserId or None"""
    client = boto3.client('identitystore')
    try:
        response = client.get_user_id(
            IdentityStoreId=identity_store_id,
            AlternateIdentifier={
                'UniqueAttribute': {
                    'AttributePath': 'userName',
                    'AttributeValue': username
                }
            }
        )
        return response['UserId']
    except client.exceptions.ResourceNotFoundException:
        return None


def get_user_email(identity_store_id, user_id):
    """Get email from Identity Center user.
    
    Note: AWS Identity Center only supports 1 email per user.
    See: https://docs.aws.amazon.com/singlesignon/latest/IdentityStoreAPIReference/API_User.html
    """
    client = boto3.client('identitystore')
    response = client.describe_user(
        IdentityStoreId=identity_store_id,
        UserId=user_id
    )
    emails = response.get('Emails', [])
    if emails and emails[0].get('Value'):
        return emails[0]['Value']
    return None


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
        errors.append('Invalid role format (max 32 chars, allowed: letters, digits, _ + = , . @ -)')

    account_name = input_data.get('accountName', '')
    if not account_name:
        errors.append('Missing required field: accountName')
    elif len(account_name) > 50:
        errors.append('accountName too long (max 50)')

    if not input_data.get('startTime'):
        errors.append('Missing required field: startTime')

    # Justification must start with alphanumeric if provided (same as VTL)
    justification = input_data.get('justification', '')
    if justification and not re.match(r'^[\w]', justification):
        errors.append('justification must start with alphanumeric')

    # ticketNo must start with alphanumeric if provided (same as VTL)
    ticket_no = input_data.get('ticketNo', '')
    if ticket_no and not re.match(r'^[A-Za-z0-9]', ticket_no):
        errors.append('ticketNo must start with alphanumeric')

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

    # Validate user exists in Identity Center and email matches
    sso_instance = get_sso_instance()
    identity_store_id = sso_instance['IdentityStoreId']

    user_id = get_user_from_identity_center(identity_store_id, input_data['username'])
    if not user_id:
        error_msg = f"User '{input_data['username']}' not found in Identity Center"
        print(f"Validation failed: {error_msg}")
        raise Exception(error_msg)

    actual_email = get_user_email(identity_store_id, user_id)
    if not actual_email:
        error_msg = f"User '{input_data['username']}' has no email in Identity Center"
        print(f"Validation failed: {error_msg}")
        raise Exception(error_msg)

    if actual_email.lower() != input_data['email'].lower():
        error_msg = f"Email mismatch: provided '{input_data['email']}' does not match Identity Center"
        print(f"Validation failed: {error_msg}")
        raise Exception(error_msg)

    # Generate request ID and timestamps
    request_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + 'Z'

    # Transform username: "alice" → "idc_alice"
    username = f"idc_{input_data['username']}"

    # Get machine client ID for audit (from Cognito token claims)
    # For client-credentials tokens, client_id is in claims, not sub/username
    identity = event.get('identity', {}) or {}
    claims = identity.get('claims', {}) or {}
    requested_by = (
        claims.get('client_id')
        or identity.get('sub')
        or identity.get('username')
        or 'unknown'
    )

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
        'justification': input_data.get('justification') or None,
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
