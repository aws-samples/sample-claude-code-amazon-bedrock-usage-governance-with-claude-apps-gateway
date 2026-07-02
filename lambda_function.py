import json

def lambda_handler(event, context):
    """
    Cognito Pre-Token Generation Lambda Trigger (V2_0).
    Injects groups claim from SAML-mapped custom:groups attribute
    into the Cognito id_token. The Claude Apps Gateway reads the
    groups claim to enforce per-group policies.
    Also injects email_verified: true for federated SAML users.
    """
    user_attributes = event.get("request", {}).get("userAttributes", {})
    groups_raw = user_attributes.get("custom:groups", "")

    if groups_raw:
        groups_raw = groups_raw.strip()
        if groups_raw.startswith("["):
            try:
                groups = json.loads(groups_raw)
            except Exception:
                groups = []
        else:
            groups = [g.strip() for g in groups_raw.split(",") if g.strip()]
    else:
        groups = []

    print(f"DEBUG: groups_raw={groups_raw}, parsed groups={groups}")

    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "idTokenGeneration": {
                "claimsToAddOrOverride": {
                    "groups": json.dumps(groups),
                    "email_verified": "true"
                }
            },
            "accessTokenGeneration": {
                "claimsToAddOrOverride": {
                    "groups": json.dumps(groups)
                }
            }
        }
    }
    return event
