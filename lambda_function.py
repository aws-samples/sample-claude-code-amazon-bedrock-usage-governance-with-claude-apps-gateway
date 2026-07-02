import json
import boto3

# Initialize Identity Store client
identitystore = boto3.client("identitystore", region_name=<aws-region>)
IDENTITY_STORE_ID = "d-xxx"  # Replace with your actual ID

def lambda_handler(event, context):
    """
    Cognito Pre-Token Generation V2_0:
    1. Overrides 'sub' with IAM Identity Center UserId
    2. Injects groups from Identity Center
    3. Sets email_verified = true
    """
    print(f"Event received: {json.dumps(event)}")

    user_attributes = event.get("request", {}).get("userAttributes", {})
    email = user_attributes.get("email", "")

    # --- Look up Identity Center UserId by email ---
    idc_user_id = None
    groups = []

    if email:
        try:
            # Find user in Identity Center by email
            response = identitystore.list_users(
                IdentityStoreId=IDENTITY_STORE_ID,
                Filters=[{
                    "AttributePath": "UserName",
                    "AttributeValue": email
                }]
            )
            users = response.get("Users", [])
            if users:
                idc_user_id = users["UserId"]
                print(f"Found IDC UserId: {idc_user_id} for email: {email}")

                # Get user's groups
                memberships = identitystore.list_group_memberships_for_member(
                    IdentityStoreId=IDENTITY_STORE_ID,
                    MemberId={"UserId": idc_user_id}
                ).get("GroupMemberships", [])

                for membership in memberships:
                    group_id = membership.get("GroupId", "")
                    if group_id:
                        group_detail = identitystore.describe_group(
                            IdentityStoreId=IDENTITY_STORE_ID,
                            GroupId=group_id
                        )
                        group_name = group_detail.get("DisplayName", "")
                        if group_name:
                            groups.append(group_name)

                print(f"User groups: {groups}")
            else:
                print(f"No IDC user found for email: {email}")
        except Exception as e:
            print(f"IDC lookup error: {str(e)}")

    # --- Build token overrides ---
    claims_to_add = {
        "email_verified": "true"
    }

    # Override sub with Identity Center UserId
    if idc_user_id:
        claims_to_add["sub"] = idc_user_id
        print(f"Overriding sub with IDC UserId: {idc_user_id}")

    # --- Build response ---
    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "idTokenGeneration": {
                "claimsToAddOrOverride": claims_to_add,
                "claimsToSuppress": []
            },
            "accessTokenGeneration": {
                "claimsToAddOrOverride": {"email_verified": "true"},
                "claimsToSuppress": []
            },
            "groupOverrideDetails": {
                "groupsToOverride": groups if groups else None
            }
        }
    }

    print(f"Response: {json.dumps(event['response'])}")
    return event
