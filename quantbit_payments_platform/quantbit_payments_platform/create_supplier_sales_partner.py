import frappe
import requests


@frappe.whitelist(allow_guest=True)
def create_partner(
    supplier_name=None,
    supplier_type=None,
    email=None,
    mobile=None,
    gst=None,
    territory=None,
    commission_rate=None,
    address=None,
    address_line2=None,
    city=None,
    state=None,
    pincode=None,
    country=None,
    address_type=None,
    customer_site=None
):

    try:

        # ---------------------------------------------------------
        # VALIDATE CUSTOMER SITE
        # ---------------------------------------------------------

        if not customer_site:
            return {
                "status": "error",
                "message": "customer_site is required"
            }

        target_site = customer_site.rstrip("/")

        frappe.logger().info(f"Creating Partner On Site: {target_site}")

        # ---------------------------------------------------------
        # GET BILLING SETTINGS
        # ---------------------------------------------------------

        settings = frappe.get_single("Billing Settings")

        billing_user = settings.billing_user
        billing_password = settings.get_password("billing_user_password")

        if not billing_user or not billing_password:
            return {
                "status": "error",
                "message": "Billing User credentials missing in Billing Settings"
            }

        if not commission_rate:
            commission_rate = settings.partners_commission_rate or 0

        # ---------------------------------------------------------
        # LOGIN TO CUSTOMER ERP
        # ---------------------------------------------------------

        session = requests.Session()

        login_response = session.post(
            f"{target_site}/api/method/login",
            data={
                "usr": billing_user,
                "pwd": billing_password
            },
            timeout=30
        )

        if login_response.status_code != 200:
            return {
                "status": "error",
                "message": f"Login Failed: {login_response.text}"
            }

        login_json = login_response.json()

        if login_json.get("message") != "Logged In":
            return {
                "status": "error",
                "message": "Invalid customer ERP credentials"
            }

        # ---------------------------------------------------------
        # CREATE SUPPLIER
        # ---------------------------------------------------------

        supplier_payload = {
            "doctype": "Supplier",
            "supplier_name": supplier_name,
            "supplier_type": supplier_type or "Company",
            "gstin": gst,
            "email_id": email,
            "mobile_no": mobile
        }

        supplier_response = session.post(
            f"{target_site}/api/resource/Supplier",
            json=supplier_payload,
            timeout=30
        )

        if supplier_response.status_code not in [200, 201]:
            return {
                "status": "error",
                "message": f"Supplier Creation Failed: {supplier_response.text}"
            }

        supplier_doc = supplier_response.json().get("data", {})
        supplier_created = supplier_doc.get("name")

        # ---------------------------------------------------------
        # CREATE SUPPLIER ADDRESS
        # ---------------------------------------------------------

        if address:

            supplier_address_payload = {
                "doctype": "Address",
                "address_title": supplier_name,
                "address_line1": address,
                "email_id": email,
                "phone": mobile,
                "links": [
                    {
                        "link_doctype": "Supplier",
                        "link_name": supplier_created
                    }
                ]
            }

            if address_line2:
                supplier_address_payload["address_line2"] = address_line2

            if city:
                supplier_address_payload["city"] = city

            if state:
                supplier_address_payload["state"] = state

            if pincode:
                supplier_address_payload["pincode"] = pincode

            if country:
                supplier_address_payload["country"] = country

            if address_type:
                supplier_address_payload["address_type"] = address_type

            supplier_address_response = session.post(
                f"{target_site}/api/resource/Address",
                json=supplier_address_payload,
                timeout=30
            )

            if supplier_address_response.status_code not in [200, 201]:
                frappe.log_error(
                    supplier_address_response.text,
                    "Supplier Address Creation Failed"
                )

        # ---------------------------------------------------------
        # CREATE SALES PARTNER
        # ---------------------------------------------------------

        sales_partner_payload = {
            "doctype": "Sales Partner",
            "partner_name": supplier_name,
            "email_id": email,
            "mobile_no": mobile,
            "commission_rate": float(commission_rate)
        }

        if territory:
            sales_partner_payload["territory"] = territory

        sales_partner_response = session.post(
            f"{target_site}/api/resource/Sales Partner",
            json=sales_partner_payload,
            timeout=30
        )

        if sales_partner_response.status_code not in [200, 201]:
            return {
                "status": "error",
                "message": f"Sales Partner Creation Failed: {sales_partner_response.text}"
            }

        sales_partner_doc = sales_partner_response.json().get("data", {})
        sales_partner_created = sales_partner_doc.get("name")

        # ---------------------------------------------------------
        # CREATE SALES PARTNER ADDRESS
        # ---------------------------------------------------------

        if address:

            sales_partner_address_payload = {
                "doctype": "Address",
                "address_title": supplier_name,
                "address_line1": address,
                "email_id": email,
                "phone": mobile,
                "links": [
                    {
                        "link_doctype": "Sales Partner",
                        "link_name": sales_partner_created
                    }
                ]
            }

            if address_line2:
                sales_partner_address_payload["address_line2"] = address_line2

            if city:
                sales_partner_address_payload["city"] = city

            if state:
                sales_partner_address_payload["state"] = state

            if pincode:
                sales_partner_address_payload["pincode"] = pincode

            if country:
                sales_partner_address_payload["country"] = country

            if address_type:
                sales_partner_address_payload["address_type"] = address_type

            sales_partner_address_response = session.post(
                f"{target_site}/api/resource/Address",
                json=sales_partner_address_payload,
                timeout=30
            )

            if sales_partner_address_response.status_code not in [200, 201]:
                frappe.log_error(
                    sales_partner_address_response.text,
                    "Sales Partner Address Creation Failed"
                )

        # ---------------------------------------------------------
        # SUCCESS RESPONSE
        # ---------------------------------------------------------

        return {
            "status": "success",
            "target_site": target_site,
            "supplier": supplier_created,
            "sales_partner": sales_partner_created,
            "message": "Partner created successfully !"
        }

    except Exception as e:

        frappe.log_error(
            frappe.get_traceback(),
            "Remote Partner Creation Error"
        )

        return {
            "status": "error",
            "message": str(e)
        }