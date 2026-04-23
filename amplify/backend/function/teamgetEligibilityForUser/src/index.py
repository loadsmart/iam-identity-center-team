# © 2023 Amazon Web Services, Inc. or its affiliates. All Rights Reserved.
# This AWS Content is provided subject to the terms of the AWS Customer Agreement available at
# http: // aws.amazon.com/agreement or other written agreement between Customer and either
# Amazon Web Services, Inc. or Amazon Web Services EMEA SARL or both.
import os
import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource('dynamodb')
policy_table = dynamodb.Table(os.environ['POLICY_TABLE_NAME'])


def get_sso_instance():
    """Get SSO instance details"""
    client = boto3.client('sso-admin')
    response = client.list_instances()
    return response['Instances'][0]


def get_user_by_email(identity_store_id, email):
    """Look up user in Identity Center by email, return UserId or None"""
    client = boto3.client('identitystore')
    try:
        response = client.get_user_id(
            IdentityStoreId=identity_store_id,
            AlternateIdentifier={
                'UniqueAttribute': {
                    'AttributePath': 'emails.value',
                    'AttributeValue': email
                }
            }
        )
        return response['UserId']
    except client.exceptions.ResourceNotFoundException:
        return None


def list_group_memberships(identity_store_id, user_id):
    """Get all group IDs for a user"""
    client = boto3.client('identitystore')
    try:
        paginator = client.get_paginator('list_group_memberships_for_member')
        pages = paginator.paginate(
            IdentityStoreId=identity_store_id,
            MemberId={'UserId': user_id}
        )
        group_ids = []
        for page in pages:
            for membership in page.get('GroupMemberships', []):
                group_ids.append(membership['GroupId'])
        return group_ids
    except ClientError as e:
        print(f"Error listing group memberships: {e}")
        return []


def get_entitlements(entity_id):
    """Get entitlements for a user or group ID from policy table"""
    try:
        response = policy_table.get_item(Key={'id': entity_id})
        return response.get('Item')
    except ClientError as e:
        print(f"Error getting entitlements for {entity_id}: {e}")
        return None


def list_accounts_for_ou(ou_id):
    """List all accounts in an OU"""
    client = boto3.client('organizations')
    accounts = []
    try:
        paginator = client.get_paginator('list_accounts_for_parent')
        pages = paginator.paginate(ParentId=ou_id)
        for page in pages:
            for account in page.get('Accounts', []):
                accounts.append({
                    'id': account['Id'],
                    'name': account['Name']
                })
    except ClientError as e:
        print(f"Error listing accounts for OU {ou_id}: {e}")
    return accounts


def merge_eligibility(entitlements_list):
    """Merge multiple entitlements into a single eligibility result"""
    accounts_map = {}  # id -> name (dedup)
    permissions_map = {}  # id -> name (dedup)
    max_duration = 0
    approval_required = True  # Default to requiring approval

    for entitlement in entitlements_list:
        if not entitlement:
            continue

        # Track max duration
        duration = int(entitlement.get('duration', 0))
        if duration > max_duration:
            max_duration = duration

        # If any entitlement doesn't require approval, set to False
        if not entitlement.get('approvalRequired', True):
            approval_required = False

        # Collect accounts
        for account in entitlement.get('accounts', []):
            accounts_map[account['id']] = account['name']

        # Expand OUs to accounts
        for ou in entitlement.get('ous', []):
            ou_accounts = list_accounts_for_ou(ou['id'])
            for account in ou_accounts:
                accounts_map[account['id']] = account['name']

        # Collect permissions
        for permission in entitlement.get('permissions', []):
            permissions_map[permission['id']] = permission['name']

    return {
        'accounts': [{'id': k, 'name': v} for k, v in sorted(accounts_map.items(), key=lambda x: x[1])],
        'permissions': [{'id': k, 'name': v} for k, v in sorted(permissions_map.items(), key=lambda x: x[1])],
        'maxDuration': max_duration,
        'approvalRequired': approval_required
    }


def handler(event, context):
    """Get eligibility for a user by email"""
    print(f"Received event: {event}")

    # Extract email from arguments
    email = event.get('arguments', {}).get('email')
    if not email:
        raise Exception("Email is required")

    # Get SSO instance
    sso_instance = get_sso_instance()
    identity_store_id = sso_instance['IdentityStoreId']

    # Look up user by email
    user_id = get_user_by_email(identity_store_id, email)
    if not user_id:
        raise Exception(f"User with email '{email}' not found in Identity Center")

    # Get user's group memberships
    group_ids = list_group_memberships(identity_store_id, user_id)
    print(f"User {email} is in groups: {group_ids}")

    # Collect entitlements for user and all their groups
    entitlements = []

    # Check user-level entitlements
    user_entitlement = get_entitlements(user_id)
    if user_entitlement:
        entitlements.append(user_entitlement)

    # Check group-level entitlements
    for group_id in group_ids:
        group_entitlement = get_entitlements(group_id)
        if group_entitlement:
            entitlements.append(group_entitlement)

    if not entitlements:
        # Return empty eligibility if no entitlements found
        return {
            'accounts': [],
            'permissions': [],
            'maxDuration': 0,
            'approvalRequired': True
        }

    # Merge all entitlements
    result = merge_eligibility(entitlements)
    print(f"Eligibility result: {result}")

    return result
