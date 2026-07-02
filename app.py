
import streamlit as st
import requests
import json
import os
import boto3
import psycopg2
import urllib3
from datetime import datetime, timezone

# Suppress SSL warnings for self-signed cert
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)




# =============================================================
# ADD THIS AFTER YOUR CONFIGURATION SECTION
# =============================================================
# Admin password passed via env var ADMIN_PASSWORD (plain text)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# =============================================================
# LOGIN FUNCTION - ADD BEFORE YOUR MAIN APP CODE
# =============================================================
def login_page():
    """Simple password-based admin login"""
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("---")
        st.markdown("## 🔐 Claude Gateway Admin")
        st.markdown("Enter admin credentials to continue")
        st.markdown("")
        
        with st.form("login_form"):
            password = st.text_input("Admin Password", type="password", placeholder="Enter admin password")
            submitted = st.form_submit_button("Login", use_container_width=True)
            
            if submitted:
                if password == ADMIN_PASSWORD:
                    st.session_state["authenticated"] = True
                    st.rerun()
                else:
                    st.error("❌ Invalid password. Please try again.")
        
        st.markdown("---")
        st.caption("Claude Apps Gateway • Authorized personnel only")


def check_auth():
    """Check if user is authenticated"""
    if not ADMIN_PASSWORD:
        return  # Skip auth if no password set
    if "authenticated" not in st.session_state or not st.session_state["authenticated"]:
        login_page()
        st.stop()


# =============================================================
# ADD THIS AS THE FIRST LINE IN YOUR MAIN APP ENTRY POINT
# (before any tabs/dashboard code runs)
# =============================================================
check_auth()

# =============================================================
# ADD LOGOUT BUTTON IN YOUR SIDEBAR
# =============================================================
# Inside your sidebar section, add:
if ADMIN_PASSWORD:
    if st.button("🚪 Logout", use_container_width=True):
        st.session_state["authenticated"] = False
        st.rerun()


# ============================================================
# Configuration
# ============================================================
GATEWAY_URL = os.getenv("GATEWAY_URL", "https://internal-claude-gateway-internal-alb-1030190838.ap-south-1.elb.amazonaws.com")
ADMIN_WRITE_KEY = os.getenv("GATEWAY_ADMIN_WRITE_KEY", "")
ADMIN_READ_KEY = os.getenv("GATEWAY_ADMIN_READ_KEY", "")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
IDENTITY_STORE_ID = os.getenv("IDENTITY_STORE_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
CW_LOG_GROUP = "/claude-gateway/admin-audit"
CW_LOG_STREAM = "actions"

PERIODS = ["daily", "weekly", "monthly"]

# ============================================================
# Database + Audit
# ============================================================
def get_db():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    except Exception as e:
        st.error(f"DB connection failed: {e}")
        return None

def log_audit(action, target_type, target_id, target_name, details=""):
    ts = datetime.now(timezone.utc)
    try:
        conn = get_db()
        if conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO audit_log (timestamp, action, target_type, target_id, target_name, details)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (ts, action, target_type, target_id, target_name, details))
            conn.commit()
            cur.close()
            conn.close()
    except Exception as e:
        print(f"RDS audit write failed: {e}")
    try:
        cw = boto3.client("logs", region_name=AWS_REGION)
        message = json.dumps({
            "timestamp": ts.isoformat(), "action": action,
            "target_type": target_type, "target_id": target_id,
            "target_name": target_name, "details": details
        })
        cw.put_log_events(
            logGroupName=CW_LOG_GROUP, logStreamName=CW_LOG_STREAM,
            logEvents=[{"timestamp": int(ts.timestamp() * 1000), "message": message}]
        )
    except Exception as e:
        print(f"CloudWatch audit write failed: {e}")

def load_audit_logs(limit=100, action_filter=None, target_filter=None):
    try:
        conn = get_db()
        if not conn:
            return []
        cur = conn.cursor()
        query = "SELECT timestamp, action, target_type, target_id, target_name, details FROM audit_log WHERE 1=1"
        params = []
        if action_filter and action_filter != "All":
            query += " AND action = %s"
            params.append(action_filter)
        if target_filter and target_filter != "All":
            query += " AND target_type = %s"
            params.append(target_filter)
        query += " ORDER BY timestamp DESC LIMIT %s"
        params.append(limit)
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{"timestamp": r[0].isoformat() if r[0] else "", "action": r[1], "target_type": r[2],
                 "target_id": r[3], "target_name": r[4], "details": r[5]} for r in rows]
    except:
        return []

def get_audit_actions():
    try:
        conn = get_db()
        if not conn: return []
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT action FROM audit_log ORDER BY action")
        actions = [r[0] for r in cur.fetchall()]
        cur.close(); conn.close()
        return actions
    except: return []

def get_audit_target_types():
    try:
        conn = get_db()
        if not conn: return []
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT target_type FROM audit_log ORDER BY target_type")
        types = [r[0] for r in cur.fetchall()]
        cur.close(); conn.close()
        return types
    except: return []

# ============================================================
# IAM Identity Center
# ============================================================
@st.cache_resource
def get_idc_client():
    return boto3.client("identitystore", region_name=AWS_REGION)

def idc_list_users():
    try:
        client = get_idc_client()
        users = []
        paginator = client.get_paginator("list_users")
        for page in paginator.paginate(IdentityStoreId=IDENTITY_STORE_ID):
            users.extend(page.get("Users", []))
        return users
    except Exception as e:
        st.error(f"IDC list users failed: {e}")
        return []

def idc_list_groups():
    try:
        client = get_idc_client()
        groups = []
        paginator = client.get_paginator("list_groups")
        for page in paginator.paginate(IdentityStoreId=IDENTITY_STORE_ID):
            groups.extend(page.get("Groups", []))
        return groups
    except Exception as e:
        st.error(f"IDC list groups failed: {e}")
        return []

def idc_get_group_members(group_id):
    try:
        client = get_idc_client()
        members = []
        paginator = client.get_paginator("list_group_memberships")
        for page in paginator.paginate(IdentityStoreId=IDENTITY_STORE_ID, GroupId=group_id):
            members.extend(page.get("GroupMemberships", []))
        return members
    except: return []

def idc_get_user_groups(user_id):
    try:
        client = get_idc_client()
        memberships = []
        paginator = client.get_paginator("list_group_memberships_for_member")
        for page in paginator.paginate(IdentityStoreId=IDENTITY_STORE_ID, MemberId={"UserId": user_id}):
            memberships.extend(page.get("GroupMemberships", []))
        return memberships
    except: return []

def idc_create_user(username, email, first_name, last_name):
    try:
        client = get_idc_client()
        return client.create_user(
            IdentityStoreId=IDENTITY_STORE_ID, UserName=username,
            Name={"GivenName": first_name, "FamilyName": last_name},
            DisplayName=f"{first_name} {last_name}",
            Emails=[{"Value": email, "Type": "Work", "Primary": True}])
    except Exception as e:
        return {"error": str(e)}

def idc_delete_user(user_id):
    try:
        client = get_idc_client()
        client.delete_user(IdentityStoreId=IDENTITY_STORE_ID, UserId=user_id)
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

def idc_create_group(name, description=""):
    try:
        client = get_idc_client()
        return client.create_group(IdentityStoreId=IDENTITY_STORE_ID, DisplayName=name,
                                   Description=description or f"Claude Gateway group: {name}")
    except Exception as e:
        return {"error": str(e)}

def idc_delete_group(group_id):
    try:
        client = get_idc_client()
        client.delete_group(IdentityStoreId=IDENTITY_STORE_ID, GroupId=group_id)
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

def idc_add_user_to_group(user_id, group_id):
    try:
        client = get_idc_client()
        return client.create_group_membership(IdentityStoreId=IDENTITY_STORE_ID, GroupId=group_id,
                                              MemberId={"UserId": user_id})
    except Exception as e:
        return {"error": str(e)}

def idc_remove_user_from_group(membership_id):
    try:
        client = get_idc_client()
        client.delete_group_membership(IdentityStoreId=IDENTITY_STORE_ID, MembershipId=membership_id)
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

# ============================================================
# Gateway Admin API
# ============================================================
def gw_get(endpoint, params=None):
    try:
        resp = requests.get(f"{GATEWAY_URL}{endpoint}", headers={"x-api-key": ADMIN_READ_KEY},
                            params=params, verify=False, timeout=10)
        return resp.json() if resp.status_code == 200 else {"error": resp.text, "status": resp.status_code}
    except Exception as e:
        return {"error": str(e)}

def gw_post(endpoint, data):
    try:
        resp = requests.post(f"{GATEWAY_URL}{endpoint}", headers={"x-api-key": ADMIN_WRITE_KEY, "Content-Type": "application/json"},
                             json=data, verify=False, timeout=10)
        return resp.json() if resp.status_code in [200, 201] else {"error": resp.text, "status": resp.status_code}
    except Exception as e:
        return {"error": str(e)}

def gw_delete(endpoint):
    try:
        resp = requests.delete(f"{GATEWAY_URL}{endpoint}", headers={"x-api-key": ADMIN_WRITE_KEY},
                               verify=False, timeout=10)
        return resp.json() if resp.status_code == 200 else {"error": resp.text, "status": resp.status_code}
    except Exception as e:
        return {"error": str(e)}

# ============================================================
# Spend Helpers
# ============================================================
def get_all_spend():
    data = gw_get("/v1/organizations/spend_limits/effective", params={"limit": 500})
    if "error" in data:
        return {}
    user_spend = {}
    for item in data.get("data", []):
        user_id = item.get("scope", {}).get("user_id", "")
        period = item.get("period", "unknown")
        actor = item.get("actor", {})
        if user_id not in user_spend:
            user_spend[user_id] = {
                "periods": {"daily": {}, "weekly": {}, "monthly": {}},
                "name": actor.get("name", "Unknown"),
                "email": actor.get("email_address", ""),
                "groups": item.get("groups", [])
            }
        # API returns cents — convert to dollars
        raw_spend = float(item.get("period_to_date_spend", "0") or "0")
        user_spend[user_id]["periods"][period] = {
            "spend_dollars": raw_spend / 100,
            "cap_cents": item.get("amount"),
            "source": item.get("source"),
            "spend_limit_id": item.get("spend_limit_id")
        }
    return user_spend

def get_all_limits():
    data = gw_get("/v1/organizations/spend_limits", params={"limit": 100})
    if "error" in data:
        return []
    return data.get("data", [])

def reset_spend(scope_type, scope_id, period):
    all_limits = get_all_limits()
    existing = None
    for lim in all_limits:
        scope = lim.get("scope", {})
        if scope.get("type") == scope_type and lim.get("period") == period:
            if scope_type == "user" and scope.get("user_id") == scope_id:
                existing = lim
                break
            elif scope_type == "rbac_group" and scope.get("rbac_group_id") == scope_id:
                existing = lim
                break
    if existing:
        gw_delete(f"/v1/organizations/spend_limits/{existing['id']}")
        payload = {"scope": existing["scope"], "amount": existing.get("amount"), "period": period}
        return gw_post("/v1/organizations/spend_limits", payload)
    return {"error": "No limit found for this scope/period. Set a limit first."}

# ============================================================
# Display Helpers
# ============================================================
def calc_usage_pct(spend_dollars, cap_cents):
    if cap_cents is None:
        return None
    try:
        cap_dollars = int(cap_cents) / 100
        if cap_dollars <= 0:
            return 100.0 if spend_dollars > 0 else 0.0
        return (spend_dollars / cap_dollars) * 100
    except (ValueError, TypeError):
        return None

def usage_bar(pct):
    if pct is None:
        return "No cap"
    pct_clamped = min(pct, 100)
    filled = int(pct_clamped / 5)
    bar = "█" * filled + "░" * (20 - filled)
    if pct >= 90:
        return f"🔴 [{bar}] {pct:.1f}%"
    elif pct >= 70:
        return f"🟡 [{bar}] {pct:.1f}%"
    return f"🟢 [{bar}] {pct:.1f}%"

def cents_to_display(cents_str):
    if cents_str is None:
        return "∞ Unlimited"
    try:
        return f"${int(cents_str) / 100:.2f}"
    except (ValueError, TypeError):
        return "N/A"

def dollars_to_cents_str(dollars):
    return str(int(float(dollars) * 100))

def period_emoji(period):
    return {"daily": "📅", "weekly": "📆", "monthly": "🗓️"}.get(period, "⏰")

def period_reset_info(period):
    return {
        "daily": "Resets daily @ 00:00 UTC",
        "weekly": "Resets Monday @ 00:00 UTC",
        "monthly": "Resets 1st @ 00:00 UTC"
    }.get(period, "")

def render_period_cards(periods_data):
    cols = st.columns(3)
    for i, period in enumerate(PERIODS):
        p_data = periods_data.get(period, {})
        spend = p_data.get("spend_dollars", 0.0)
        cap = p_data.get("cap_cents")
        pct = calc_usage_pct(spend, cap)
        with cols[i]:
            st.markdown(f"**{period_emoji(period)} {period.capitalize()}**")
            st.write(f"Spend: **${spend:.4f}**")
            st.write(f"Cap: **{cents_to_display(cap)}**")
            st.write(usage_bar(pct))
            st.caption(period_reset_info(period))

def get_most_restrictive(periods_data):
    worst_pct, worst_period = -1, None
    for period in PERIODS:
        p_data = periods_data.get(period, {})
        pct = calc_usage_pct(p_data.get("spend_dollars", 0.0), p_data.get("cap_cents"))
        if pct is not None and pct > worst_pct:
            worst_pct = pct
            worst_period = period
    return worst_period, worst_pct

# ============================================================
# APP LAYOUT
# ============================================================
st.set_page_config(page_title="Claude Gateway Admin", page_icon="🛡️", layout="wide")
st.title("🛡️ Claude Gateway — Admin Console")

page = st.sidebar.radio("Navigation", [
    "📊 Dashboard",
    "👥 Users",
    "📁 Groups",
    "💰 Spend Limits",
    "🔄 Reset Spend",
    "📋 Audit Log"
])

# ============================================================
# PAGE 1: DASHBOARD (Default/First)
# ============================================================
if page == "📊 Dashboard":
    st.header("Spend Dashboard")
    st.info("💡 Auto-resets: Daily @ 00:00 UTC | Weekly @ Monday 00:00 UTC | Monthly @ 1st 00:00 UTC")

    spend_data = get_all_spend()
    idc_users = idc_list_users()
    all_limits = get_all_limits()

    # Organization spend by period
    st.subheader("📈 Organization Spend by Period")
    period_cols = st.columns(3)
    for i, period in enumerate(PERIODS):
        total = sum(d["periods"].get(period, {}).get("spend_dollars", 0.0) for d in spend_data.values())
        org_cap = None
        for lim in all_limits:
            if lim.get("scope", {}).get("type") == "organization" and lim.get("period") == period:
                org_cap = lim.get("amount")
                break
        with period_cols[i]:
            st.metric(f"{period_emoji(period)} {period.capitalize()} Total", f"${total:.4f}")
            st.write(f"Org Cap: {cents_to_display(org_cap)}")
            st.write(usage_bar(calc_usage_pct(total, org_cap)))
            st.caption(period_reset_info(period))

    st.divider()

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Active Users", len(spend_data))
    with col2:
        st.metric("IDC Users", len(idc_users))
    with col3:
        st.metric("Limits Set", len(all_limits))
    with col4:
        pc = {"daily": 0, "weekly": 0, "monthly": 0}
        for lim in all_limits:
            p = lim.get("period", "")
            if p in pc: pc[p] += 1
        st.metric("By Period", f"D:{pc['daily']} W:{pc['weekly']} M:{pc['monthly']}")

    st.divider()

    # Top spenders
    st.subheader("🏆 Top Spenders")
    spenders = []
    for gw_id, data in spend_data.items():
        worst_period, worst_pct = get_most_restrictive(data["periods"])
        monthly = data["periods"].get("monthly", {}).get("spend_dollars", 0.0)
        spenders.append({"name": data["name"], "email": data["email"], "gw_id": gw_id,
                         "periods": data["periods"], "monthly": monthly,
                         "worst_period": worst_period, "worst_pct": worst_pct})
    spenders.sort(key=lambda x: x["monthly"], reverse=True)

    if spenders:
        for s in spenders[:15]:
            wp = s["worst_pct"]
            status = "🔴" if wp and wp >= 90 else "🟡" if wp and wp >= 70 else "🟢" if wp else "⚪"
            with st.expander(f"{status} {s['name']} ({s['email']}) — Monthly: ${s['monthly']:.4f}"):
                render_period_cards(s["periods"])
                if s["worst_period"] and wp:
                    st.caption(f"⚡ Tightest: **{s['worst_period']}** at {wp:.1f}%")
    else:
        st.info("No spend data yet.")

    st.divider()

    # At-risk users
    st.subheader("⚠️ At-Risk (≥ 80% any period)")
    at_risk = [s for s in spenders if s["worst_pct"] and s["worst_pct"] >= 80]
    if at_risk:
        for s in at_risk:
            p = s["worst_period"]
            pd = s["periods"].get(p, {})
            st.write(f"🔴 **{s['name']}** — {period_emoji(p)} {p}: "
                     f"${pd.get('spend_dollars', 0):.4f} / {cents_to_display(pd.get('cap_cents'))} ({s['worst_pct']:.1f}%)")
    else:
        st.success("✅ No users at risk.")

    st.divider()

    # Inactive users
    st.subheader("⚪ Not Yet Active")
    active_emails = set(info.get("email", "").lower() for info in spend_data.values())
    inactive = [u for u in idc_users
                if next((e["Value"] for e in u.get("Emails", []) if e.get("Primary")), "").lower() not in active_emails]
    if inactive:
        for u in inactive[:20]:
            email = next((e["Value"] for e in u.get("Emails", []) if e.get("Primary")), "")
            st.write(f"  ⚪ {u.get('DisplayName', 'N/A')} — {email}")
        st.caption(f"{len(inactive)} user(s) haven't used the gateway yet.")
    else:
        st.success("✅ All users active!")

# ============================================================
# PAGE 2: USERS (View + Onboard + Remove only — NO limits here)
# ============================================================
elif page == "👥 Users":
    st.header("User Management")
    tab1, tab2, tab3 = st.tabs(["All Users", "Onboard", "Remove"])

    idc_users = idc_list_users()
    idc_groups = idc_list_groups()
    group_id_to_name = {g["GroupId"]: g["DisplayName"] for g in idc_groups}
    spend_data = get_all_spend()
    all_limits = get_all_limits()

    email_to_gw_id = {}
    for gw_id, info in spend_data.items():
        if info.get("email"):
            email_to_gw_id[info["email"].lower()] = gw_id

    with tab1:
        st.subheader(f"Users ({len(idc_users)})")
        st.caption("To set/modify limits → go to 💰 Spend Limits tab. To reset → go to 🔄 Reset Spend tab.")

        for user in idc_users:
            idc_uid = user.get("UserId", "")
            name = user.get("DisplayName", "N/A")
            email = next((e["Value"] for e in user.get("Emails", []) if e.get("Primary")), "N/A")
            gw_uid = email_to_gw_id.get(email.lower())
            memberships = idc_get_user_groups(idc_uid)
            user_groups = [group_id_to_name.get(m.get("GroupId", ""), "?") for m in memberships]

            # Build period data
            if gw_uid and gw_uid in spend_data:
                periods_data = spend_data[gw_uid].get("periods", {})
                active = True
            else:
                periods_data = {"daily": {}, "weekly": {}, "monthly": {}}
                active = False

            # Merge limits
            effective_uid = gw_uid or idc_uid
            for lim in all_limits:
                scope = lim.get("scope", {})
                if scope.get("type") == "user" and scope.get("user_id") == effective_uid:
                    p = lim.get("period", "monthly")
                    if p not in periods_data or not periods_data.get(p):
                        periods_data[p] = {"spend_dollars": 0.0, "cap_cents": lim.get("amount")}
                    elif periods_data[p].get("cap_cents") is None:
                        periods_data[p]["cap_cents"] = lim.get("amount")

            worst_period, worst_pct = get_most_restrictive(periods_data)
            if not active:
                status = "⚪"
            elif worst_pct and worst_pct >= 90:
                status = "🔴"
            elif worst_pct and worst_pct >= 70:
                status = "🟡"
            else:
                status = "🟢"

            monthly = periods_data.get("monthly", {}).get("spend_dollars", 0.0)
            with st.expander(f"{status} {name} | {email} | Monthly: ${monthly:.4f}"):
                render_period_cards(periods_data)
                st.divider()
                st.write(f"**Groups:** {', '.join(user_groups) if user_groups else 'None'}")
                st.write(f"**IDC ID:** `{idc_uid}`")
                st.write(f"**Gateway ID:** `{gw_uid}`" if gw_uid else "**Gateway ID:** ⚪ Not yet active")
                st.write(f"**Status:** {'✅ Active' if active else '⚪ Not yet active'}")

    with tab2:
        st.subheader("Onboard New User")
        col1, col2 = st.columns(2)
        with col1:
            new_username = st.text_input("Username", placeholder="johndoe")
            new_first = st.text_input("First Name", placeholder="John")
        with col2:
            new_email = st.text_input("Email", placeholder="john@company.com")
            new_last = st.text_input("Last Name", placeholder="Doe")

        group_choices = ["-- None --"] + [g["DisplayName"] for g in idc_groups]
        add_to_group = st.selectbox("Assign to Group", group_choices)

        if st.button("✅ Create User", type="primary"):
            if all([new_username, new_email, new_first, new_last]):
                result = idc_create_user(new_username, new_email, new_first, new_last)
                if "error" in result:
                    st.error(f"❌ {result['error']}")
                else:
                    new_id = result.get("UserId", "")
                    log_audit("CREATE_USER", "user", new_id, f"{new_first} {new_last}", f"Email: {new_email}")
                    st.success(f"✅ Created: `{new_id}`")
                    if add_to_group != "-- None --":
                        gid = next((g["GroupId"] for g in idc_groups if g["DisplayName"] == add_to_group), None)
                        if gid:
                            idc_add_user_to_group(new_id, gid)
                            log_audit("ADD_TO_GROUP", "user", new_id, f"{new_first} {new_last}", f"Group: {add_to_group}")
                            st.success(f"✅ Added to: {add_to_group}")
                    st.info("💡 Set their spend limits in the 💰 Spend Limits tab.")
                    st.rerun()
            else:
                st.warning("All fields required.")

    with tab3:
        st.subheader("Remove User")
        st.warning("⚠️ Permanently deletes user from Identity Center and removes all spend limits.")
        if idc_users:
            user_options = {
                f"{u.get('DisplayName', '')} ({next((e['Value'] for e in u.get('Emails', []) if e.get('Primary')), '')})": u
                for u in idc_users
            }
            selected = st.selectbox("Select User", list(user_options.keys()))
            confirm = st.checkbox("I confirm permanent deletion")
            if st.button("🗑️ Delete User", disabled=not confirm):
                user = user_options[selected]
                uid = user["UserId"]
                uemail = next((e["Value"] for e in user.get("Emails", []) if e.get("Primary")), "").lower()
                result = idc_delete_user(uid)
                if "error" in result:
                    st.error(f"Failed: {result['error']}")
                else:
                    log_audit("DELETE_USER", "user", uid, user.get("DisplayName", ""), "Permanent deletion")
                    gw_uid = email_to_gw_id.get(uemail)
                    for lim in all_limits:
                        scope = lim.get("scope", {})
                        if scope.get("user_id") in [uid, gw_uid]:
                            gw_delete(f"/v1/organizations/spend_limits/{lim['id']}")
                    st.success("✅ User deleted + all limits removed.")
                    st.rerun()

# ============================================================
# PAGE 3: GROUPS (View + Create + Members + Delete — NO limits here)
# ============================================================
elif page == "📁 Groups":
    st.header("Group Management")
    tab1, tab2, tab3, tab4 = st.tabs(["All Groups", "Create", "Members", "Delete"])

    idc_groups = idc_list_groups()
    idc_users = idc_list_users()
    user_id_map = {u["UserId"]: u for u in idc_users}
    all_limits = get_all_limits()
    spend_data = get_all_spend()

    group_limits = {}
    for lim in all_limits:
        scope = lim.get("scope", {})
        if scope.get("type") == "rbac_group":
            gname = scope.get("rbac_group_id", "")
            if gname not in group_limits:
                group_limits[gname] = {}
            group_limits[gname][lim.get("period")] = lim

    with tab1:
        st.subheader(f"Groups ({len(idc_groups)})")
        st.caption("To set/modify limits → go to 💰 Spend Limits tab. To reset → go to 🔄 Reset Spend tab.")

        for g in idc_groups:
            gid = g["GroupId"]
            gname = g.get("DisplayName", "N/A")
            members = idc_get_group_members(gid)
            g_lims = group_limits.get(gname, {})

            # Aggregate member spend
            group_period_spend = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}
            for m in members:
                uid = m.get("MemberId", {}).get("UserId", "")
                minfo = user_id_map.get(uid, {})
                memail = next((e["Value"] for e in minfo.get("Emails", []) if e.get("Primary")), "").lower()
                for gw_id, gw_info in spend_data.items():
                    if gw_info.get("email", "").lower() == memail:
                        for period in PERIODS:
                            group_period_spend[period] += gw_info.get("periods", {}).get(period, {}).get("spend_dollars", 0.0)
                        break

            caps_str = " | ".join([f"{period_emoji(p)} {cents_to_display(g_lims.get(p, {}).get('amount'))}" for p in PERIODS])

            with st.expander(f"📁 {gname} | {len(members)} members | {caps_str}"):
                # Period view
                gcols = st.columns(3)
                for pi, period in enumerate(PERIODS):
                    with gcols[pi]:
                        cap = g_lims.get(period, {}).get("amount")
                        total = group_period_spend[period]
                        avg = total / max(len(members), 1)
                        pct = calc_usage_pct(avg, cap)
                        st.markdown(f"**{period_emoji(period)} {period.capitalize()}**")
                        st.write(f"Cap/member: {cents_to_display(cap)}")
                        st.write(f"Total: ${total:.4f}")
                        st.write(f"Avg: ${avg:.4f}")
                        st.write(usage_bar(pct))

                # Members list
                if members:
                    st.divider()
                    st.write(f"**Members ({len(members)}):**")
                    for m in members:
                        uid = m.get("MemberId", {}).get("UserId", "")
                        info = user_id_map.get(uid, {})
                        mname = info.get("DisplayName", uid)
                        memail = next((e["Value"] for e in info.get("Emails", []) if e.get("Primary")), "").lower()
                        mperiods = {}
                        for gw_id, gw_info in spend_data.items():
                            if gw_info.get("email", "").lower() == memail:
                                mperiods = gw_info.get("periods", {})
                                break
                        daily_s = mperiods.get("daily", {}).get("spend_dollars", 0.0)
                        monthly_s = mperiods.get("monthly", {}).get("spend_dollars", 0.0)
                        st.write(f"  • {mname}: 📅 ${daily_s:.4f}/day | 🗓️ ${monthly_s:.4f}/mo")

    with tab2:
        st.subheader("Create Group")
        new_name = st.text_input("Group Name", placeholder="engineering-team")
        new_desc = st.text_input("Description", placeholder="Engineering team")
        if st.button("✅ Create Group", type="primary"):
            if new_name:
                result = idc_create_group(new_name, new_desc)
                if "error" in result:
                    st.error(f"Failed: {result['error']}")
                else:
                    log_audit("CREATE_GROUP", "group", result.get("GroupId", ""), new_name, new_desc)
                    st.success(f"✅ Group `{new_name}` created!")
                    st.info("💡 Set limits in 💰 Spend Limits tab. Add to gateway.yaml: `groups: [\"{new_name}\"]`")
                    st.rerun()

    with tab3:
        st.subheader("Manage Members")
        if idc_groups and idc_users:
            group_options = {g["DisplayName"]: g["GroupId"] for g in idc_groups}
            selected_group = st.selectbox("Group", list(group_options.keys()))
            gid = group_options[selected_group]

            st.write("**➕ Add User:**")
            user_labels = {
                f"{u.get('DisplayName', '')} ({next((e['Value'] for e in u.get('Emails', []) if e.get('Primary')), '')})": u["UserId"]
                for u in idc_users
            }
            sel_user = st.selectbox("User", list(user_labels.keys()))
            if st.button("➕ Add", type="primary"):
                result = idc_add_user_to_group(user_labels[sel_user], gid)
                if "error" in result:
                    st.error(f"Failed: {result['error']}")
                else:
                    log_audit("ADD_TO_GROUP", "user", user_labels[sel_user], sel_user, f"Group: {selected_group}")
                    st.success("✅ Added!")
                    st.rerun()

            st.divider()
            st.write("**➖ Remove User:**")
            members = idc_get_group_members(gid)
            if members:
                mlabels = {}
                for m in members:
                    uid = m.get("MemberId", {}).get("UserId", "")
                    info = user_id_map.get(uid, {})
                    mlabels[info.get("DisplayName", uid)] = m.get("MembershipId", "")
                sel_m = st.selectbox("Member", list(mlabels.keys()))
                if st.button("➖ Remove"):
                    result = idc_remove_user_from_group(mlabels[sel_m])
                    if "error" in result:
                        st.error(f"Failed: {result['error']}")
                    else:
                        log_audit("REMOVE_FROM_GROUP", "user", "", sel_m, f"Group: {selected_group}")
                        st.success("✅ Removed.")
                        st.rerun()
            else:
                st.info("No members.")

    with tab4:
        st.subheader("Delete Group")
        if idc_groups:
            group_options = {g["DisplayName"]: g["GroupId"] for g in idc_groups}
            sel = st.selectbox("Group", list(group_options.keys()), key="del_grp")
            confirm = st.checkbox("Confirm deletion")
            if st.button("🗑️ Delete", disabled=not confirm):
                result = idc_delete_group(group_options[sel])
                if "error" in result:
                    st.error(f"Failed: {result['error']}")
                else:
                    log_audit("DELETE_GROUP", "group", group_options[sel], sel, "")
                    for lim in all_limits:
                        if lim.get("scope", {}).get("rbac_group_id") == sel:
                            gw_delete(f"/v1/organizations/spend_limits/{lim['id']}")
                    st.success(f"✅ Deleted `{sel}` + all limits")
                    st.rerun()

# ============================================================
# PAGE 4: SPEND LIMITS (SINGLE place to set/view/delete)
# ============================================================
elif page == "💰 Spend Limits":
    st.header("Spend Limits — Single Control Panel")
    st.info("💡 Resolution order: User override → Most restrictive group → Org default → Unlimited. "
            "You can set all three periods — the tightest one blocks first.")

    tab1, tab2, tab3 = st.tabs(["Set Limit", "View All", "Delete"])

    idc_groups = idc_list_groups()
    idc_users = idc_list_users()
    spend_data = get_all_spend()
    all_limits = get_all_limits()

    email_to_gw_id = {}
    for gw_id, info in spend_data.items():
        if info.get("email"):
            email_to_gw_id[info["email"].lower()] = gw_id

    with tab1:
        st.subheader("Set Limit")
        scope_type = st.selectbox("Scope", ["🏢 Organization", "📁 Group", "👤 User"])
        scope_payload = None
        target_display = ""

        if "Organization" in scope_type:
            scope_payload = {"type": "organization"}
            target_display = "Organization"
        elif "Group" in scope_type:
            if idc_groups:
                grp_names = [g["DisplayName"] for g in idc_groups]
                sel_grp = st.selectbox("Group", grp_names)
                scope_payload = {"type": "rbac_group", "rbac_group_id": sel_grp}
                target_display = sel_grp
        elif "User" in scope_type:
            if idc_users:
                ulabels = {}
                for u in idc_users:
                    email = next((e["Value"] for e in u.get("Emails", []) if e.get("Primary")), "")
                    ulabels[f"{u.get('DisplayName', '')} ({email})"] = {"idc_id": u["UserId"], "email": email}
                sel_u = st.selectbox("User", list(ulabels.keys()))
                uinfo = ulabels[sel_u]
                gw_uid = email_to_gw_id.get(uinfo["email"].lower())
                effective_uid = gw_uid if gw_uid else uinfo["idc_id"]
                if not gw_uid:
                    st.warning("⚠️ User hasn't logged in. Using IDC ID — may need re-set after first login.")
                scope_payload = {"type": "user", "user_id": effective_uid}
                target_display = sel_u

        st.divider()
        st.write("**Set limits per period:**")
        lcols = st.columns(3)
        period_enabled = {}
        period_amounts = {}
        for pi, period in enumerate(PERIODS):
            with lcols[pi]:
                st.markdown(f"**{period_emoji(period)} {period.capitalize()}**")
                period_enabled[period] = st.checkbox("Enable", value=(period == "monthly"), key=f"en_{period}")
                period_amounts[period] = st.number_input(
                    "Cap ($)", min_value=0.0, step=5.0,
                    value={"daily": 10.0, "weekly": 50.0, "monthly": 200.0}[period],
                    key=f"la_{period}", disabled=not period_enabled[period])
                st.caption(period_reset_info(period))

        if st.button("💾 Set All Enabled Limits", type="primary"):
            if scope_payload:
                ok = 0
                for period in PERIODS:
                    if period_enabled[period]:
                        result = gw_post("/v1/organizations/spend_limits", {
                            "scope": scope_payload,
                            "amount": dollars_to_cents_str(period_amounts[period]),
                            "period": period
                        })
                        if "error" not in result:
                            ok += 1
                        else:
                            st.error(f"❌ {period}: {json.dumps(result)}")
                if ok:
                    details = " | ".join([f"{p}:${period_amounts[p]}" for p in PERIODS if period_enabled[p]])
                    log_audit("SET_LIMIT", scope_payload.get("type", ""), target_display, target_display, details)
                    st.success(f"✅ {ok} limit(s) set!")
                    st.rerun()

        # Quick actions
        st.divider()
        st.write("**⚡ Quick Actions:**")
        qcols = st.columns(2)
        with qcols[0]:
            if st.button("🚫 Block (set $0 all periods)"):
                if scope_payload:
                    for period in PERIODS:
                        gw_post("/v1/organizations/spend_limits", {
                            "scope": scope_payload, "amount": "0", "period": period
                        })
                    log_audit("BLOCK", scope_payload.get("type", ""), target_display, target_display, "All periods $0")
                    st.success("✅ Blocked! ($0 all periods)")
                    st.rerun()
        with qcols[1]:
            if st.button("♾️ Remove all limits (unlimited)"):
                if scope_payload:
                    for lim in all_limits:
                        scope = lim.get("scope", {})
                        match = False
                        if scope_payload.get("type") == "organization" and scope.get("type") == "organization":
                            match = True
                        elif scope_payload.get("type") == "user" and scope.get("user_id") == scope_payload.get("user_id"):
                            match = True
                        elif scope_payload.get("type") == "rbac_group" and scope.get("rbac_group_id") == scope_payload.get("rbac_group_id"):
                            match = True
                        if match:
                            gw_delete(f"/v1/organizations/spend_limits/{lim['id']}")
                    log_audit("REMOVE_LIMITS", scope_payload.get("type", ""), target_display, target_display, "All limits removed")
                    st.success("✅ All limits removed (unlimited).")
                    st.rerun()

    with tab2:
        st.subheader("All Active Limits")
        if st.button("🔄 Refresh"):
            st.rerun()
        if all_limits:
            by_scope = {}
            for lim in all_limits:
                scope = lim.get("scope", {})
                stype = scope.get("type", "?")
                target = scope.get("user_id") or scope.get("rbac_group_id") or "organization"
                key = f"{stype}:{target}"
                if key not in by_scope:
                    by_scope[key] = {"type": stype, "target": target, "periods": {}}
                display_target = target
                for gw_id, info in spend_data.items():
                    if gw_id == target:
                        display_target = f"{info.get('name', '')} ({info.get('email', '')})"
                        break
                by_scope[key]["display"] = display_target
                by_scope[key]["periods"][lim.get("period")] = {"amount": lim.get("amount"), "id": lim.get("id")}

            for key, data in by_scope.items():
                icon = {"organization": "🏢", "rbac_group": "📁", "user": "👤"}.get(data["type"], "?")
                period_str = " | ".join([
                    f"{period_emoji(p)} {p}: {cents_to_display(pd.get('amount'))}"
                    for p, pd in sorted(data["periods"].items())
                ])
                st.write(f"{icon} **{data.get('display', data['target'])}** — {period_str}")
                st.divider()
        else:
            st.info("No limits set yet.")

    with tab3:
        st.subheader("Delete Limit")
        if all_limits:
            llabels = {}
            for lim in all_limits:
                scope = lim.get("scope", {})
                target = scope.get("user_id") or scope.get("rbac_group_id") or "organization"
                display = target
                for gw_id, info in spend_data.items():
                    if gw_id == target:
                        display = info.get("name", target)
                        break
                label = f"{scope.get('type')} → {display} ({cents_to_display(lim.get('amount'))}/{lim.get('period')})"
                llabels[label] = lim["id"]
            sel_lim = st.selectbox("Limit to delete", list(llabels.keys()))
            if st.button("🗑️ Delete"):
                result = gw_delete(f"/v1/organizations/spend_limits/{llabels[sel_lim]}")
                if "error" in result:
                    st.error(f"Failed: {result}")
                else:
                    log_audit("DELETE_LIMIT", "limit", llabels[sel_lim], sel_lim, "")
                    st.success("✅ Deleted.")
                    st.rerun()
        else:
            st.info("No limits to delete.")

# ============================================================
# PAGE 5: RESET SPEND (SINGLE place to reset)
# ============================================================
elif page == "🔄 Reset Spend":
    st.header("Reset Spend")
    st.info("💡 Gateway auto-resets at period boundaries. Use this for mid-cycle exceptions only.")

    tab1, tab2 = st.tabs(["Reset User", "Reset Group"])

    spend_data = get_all_spend()
    idc_groups = idc_list_groups()
    all_limits = get_all_limits()

    with tab1:
        st.subheader("Reset User Spend")
        active_users = [
            {"label": f"{info['name']} ({info['email']})", "gw_id": gw_id, "name": info["name"], "periods": info["periods"]}
            for gw_id, info in spend_data.items()
        ]
        if active_users:
            ulabels = {u["label"]: u for u in active_users}
            selected = st.selectbox("User", list(ulabels.keys()))
            uinfo = ulabels[selected]

            st.write("**Current spend:**")
            render_period_cards(uinfo["periods"])

            st.divider()
            reset_period = st.selectbox("Period to reset", PERIODS, key="rst_u_period")
            reason = st.text_input("Reason (required for audit)", placeholder="Manager approved mid-cycle reset")

            if st.button("🔄 Reset User Spend", type="primary"):
                if not reason:
                    st.warning("Reason required.")
                else:
                    prev_spend = uinfo["periods"].get(reset_period, {}).get("spend_dollars", 0.0)
                    result = reset_spend("user", uinfo["gw_id"], reset_period)
                    if "error" in result:
                        st.error(f"❌ {result['error']}")
                    else:
                        log_audit("RESET_USER_SPEND", "user", uinfo["gw_id"], uinfo["name"],
                                  f"{reset_period}: ${prev_spend:.4f} → $0. Reason: {reason}")
                        st.success(f"✅ {reset_period} spend reset for {uinfo['name']}!")
                        st.rerun()
        else:
            st.info("No active users to reset.")

    with tab2:
        st.subheader("Reset Group Spend")
        group_limits_map = {}
        for lim in all_limits:
            scope = lim.get("scope", {})
            if scope.get("type") == "rbac_group":
                gname = scope.get("rbac_group_id", "")
                if gname not in group_limits_map:
                    group_limits_map[gname] = {}
                group_limits_map[gname][lim.get("period")] = lim

        groups_with_limits = list(group_limits_map.keys())
        if groups_with_limits:
            sel_grp = st.selectbox("Group", groups_with_limits, key="rst_g_grp")
            g_periods = group_limits_map.get(sel_grp, {})

            st.write("**Current group limits:**")
            gcols = st.columns(3)
            for pi, period in enumerate(PERIODS):
                with gcols[pi]:
                    cap = g_periods.get(period, {}).get("amount")
                    st.markdown(f"**{period_emoji(period)} {period.capitalize()}**")
                    st.write(f"Cap: {cents_to_display(cap)}")

            reset_period = st.selectbox("Period to reset", PERIODS, key="rst_g_period")
            reason = st.text_input("Reason (required for audit)", placeholder="Quarter end reset", key="rst_g_reason")

            if st.button("🔄 Reset Group Spend", type="primary"):
                if not reason:
                    st.warning("Reason required.")
                else:
                    result = reset_spend("rbac_group", sel_grp, reset_period)
                    if "error" in result:
                        st.error(f"❌ {result['error']}")
                    else:
                        log_audit("RESET_GROUP_SPEND", "group", sel_grp, sel_grp,
                                  f"{reset_period} reset. Reason: {reason}")
                        st.success(f"✅ {reset_period} spend reset for group `{sel_grp}`!")
                        st.rerun()
        else:
            st.info("No groups with limits to reset. Set group limits first in 💰 Spend Limits.")

# ============================================================
# PAGE 6: AUDIT LOG
# ============================================================
elif page == "📋 Audit Log":
    st.header("Audit Log")

    col1, col2, col3 = st.columns(3)
    with col1:
        actions = ["All"] + get_audit_actions()
        action_filter = st.selectbox("Action", actions)
    with col2:
        targets = ["All"] + get_audit_target_types()
        target_filter = st.selectbox("Target Type", targets)
    with col3:
        limit = st.number_input("Show last N entries", value=50, min_value=10, max_value=500)

    logs = load_audit_logs(limit=limit, action_filter=action_filter, target_filter=target_filter)

    if logs:
        for log in logs:
            ts = log["timestamp"][:19].replace("T", " ")
            action = log["action"]
            icon = {"SET_LIMIT": "💰", "RESET_USER_SPEND": "🔄", "RESET_GROUP_SPEND": "🔄",
                    "CREATE_USER": "👤", "DELETE_USER": "🗑️", "CREATE_GROUP": "📁",
                    "DELETE_GROUP": "🗑️", "ADD_TO_GROUP": "➕", "REMOVE_FROM_GROUP": "➖",
                    "BLOCK": "🚫", "REMOVE_LIMITS": "♾️", "DELETE_LIMIT": "🗑️"}.get(action, "📝")
            st.write(f"`{ts}` {icon} **{action}** → {log['target_type']}: {log['target_name']}")
            if log.get("details"):
                st.caption(f"    Details: {log['details']}")
    else:
        st.info("No audit logs yet.")

