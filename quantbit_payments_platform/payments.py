import frappe
import json
from datetime import date, datetime
from frappe.utils import flt, today, getdate
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from payments.utils import get_payment_gateway_controller
from frappe.integrations.utils import create_request_log

def create_razorpay_order(doc, application_fee=0):
    """
    Creates Razorpay order for Sales Invoice (or Fees if extended later).
    Returns dict with order details + token for frontend.
    """
    try:
        controller = get_payment_gateway_controller("Razorpay")

        if not controller:
            frappe.throw("Razorpay payment gateway is not configured")

        # Use rounded_total if available, else grand_total
        amount = int(doc.rounded_total or doc.grand_total or 0)
        frappe.log_error(
            title="Razorpay Order Creation Debug",
            message={
                "invoice": doc.name,
                "rounded_total": doc.rounded_total,
                "grand_total": doc.grand_total,
                "currency": doc.currency,
                "amount_in_rupees": amount
            }
        )
        if amount <= 0:
            frappe.throw("Invoice has no payable amount")

        # Convert to paise (Razorpay expects amount in smallest currency unit)
        amount = int(amount)
        frappe.log_error(
            title="Razorpay Amount in Paise",
            message={
                "invoice": doc.name,
                "amount_in_paise": amount
            }
        )

        # Determine payer info dynamically
        payer_name = (
            doc.get("customer_name")
            or doc.get("customer")
            or frappe.db.get_value("Customer", doc.customer, "customer_name")
        )
        payer_email = (
            doc.get("contact_email")
            or frappe.db.get_value("Customer", doc.customer, "email_id")
            or ""
        )

        title = f"Payment for Invoice {doc.name}"
        ref_doctype = doc.doctype
        ref_name = doc.name

        if application_fee == 1:
            title = f"Payment for Application Invoice {doc.name}"

        payment_details = {
            "amount": amount,
            "title": title,
            "description": "Payment via Razorpay",
            "reference_doctype": ref_doctype,
            "reference_docname": ref_name,
            "payer_name": payer_name,
            "payer_email": payer_email,
            "order_id": ref_name,
            "currency": doc.currency or "INR",
            "payment_gateway": "Razorpay"
        }

        frappe.log_error(
            title="Razorpay Payment Details",
            message={
                "amount": amount,
                "currency": doc.currency or "INR",
                "title": title
            }
        )

        razorpay_order = controller.create_order(**payment_details)

        integration_log = frappe.get_doc("Integration Request", razorpay_order.get("integration_request"))
        payment_data = json.loads(integration_log.data or "{}")

        expected_keys = (
            "amount", "title", "description", "reference_doctype",
            "reference_docname", "payer_name", "payer_email",
            "order_id", "currency"
        )

        data = {k: payment_data.get(k) for k in expected_keys}
        data["token"]          = integration_log.name
        data["order_id"]       = razorpay_order.get("id")
        data["amount"]         = flt(data["amount"])
        data["subscription_id"] = payment_data.get("subscription_id", "")
        data["api_key"]        = controller.get("api_key")  # public key

        return data

    except Exception as e:
        frappe.log_error(
            title="Razorpay Order Creation Failed",
            message={
                "invoice": doc.name if doc else "Unknown",
                "error": str(e),
                "traceback": frappe.get_traceback()
            }
        )
        raise


@frappe.whitelist(allow_guest=True)
def razorpay_callback(
    options,
    razorpay_payment_id,
    reference_docname,
    reference_doctype,
    razorpay_order_id,
    token,
    from_site=None,
    package_name=None,
    customer_email=None,
    app_name=None
):

    frappe.local.flags.ignore_csrf = True

    try:
        privileged_user = "Administrator"
        original_user = frappe.session.user

        frappe.set_user(privileged_user)

        # ---------------- Razorpay Verification ----------------
        data = options.copy() if isinstance(options, dict) else {}

        data.update({
            "razorpay_payment_id": razorpay_payment_id,
            "reference_docname": reference_docname,
            "reference_doctype": reference_doctype,
            "razorpay_order_id": razorpay_order_id,
            "token": token,
        })



        razorpay_settings = frappe.get_doc("Razorpay Settings")

        response = razorpay_settings.create_request(data)

        frappe.log_error(
            title="Razorpay Verification Response",
            message=response
        )

        status = response.get("status")

        if str(status).lower() in [
            "authorized",
            "captured",
            "completed",
            "200",
            200
        ]:

            if reference_doctype == "Sales Invoice":

                # ---------------- Duplicate Payment Check ----------------
                existing_pe = frappe.db.exists(
                    "Payment Entry Reference",
                    {
                        "reference_doctype": "Sales Invoice",
                        "reference_name": reference_docname
                    }
                )

                if not existing_pe:

                    frappe.log_error(
                        title="Creating Payment Entry",
                        message=reference_docname
                    )

                    pe = get_payment_entry(
                        dt="Sales Invoice",
                        dn=reference_docname,
                        party_type="Customer",
                        payment_type="Receive"
                    )

                    pe.mode_of_payment = "wire transfer"
                    pe.reference_no = razorpay_payment_id
                    pe.reference_date = today()

                    pe.save(ignore_permissions=True)
                    pe.submit()

                    frappe.db.commit()

                    frappe.log_error(
                        title="✅ Payment Entry Created",
                        message=pe.name
                    )

                else:

                    frappe.log_error(
                        title="⚠ Payment Entry Already Exists",
                        message=reference_docname
                    )

                # ---------------- Invoice ----------------
                inv = frappe.get_doc(
                    "Sales Invoice",
                    reference_docname
                )

                payment_details_name = None

                # ---------------- Billing Account Update ----------------
                if from_site:
                    try:
                        result = update_billing_account_on_source(
                            from_site=from_site,
                            customer_email=customer_email,
                            package_name=package_name,
                            sales_invoice_name=reference_docname,
                            app_name=app_name
                        )
                    except Exception:
                        frappe.log_error(
                            title="❌ Billing Account Update Failed — Exception in callback",
                            message=frappe.get_traceback()
                        )

                # ---------------- Payment Invoice Details ----------------
                try:

                    payment_details_name = (
                        update_payment_invoice_details_on_source(
                            from_site=from_site,
                            sales_invoice=inv
                        )
                    )

                    frappe.log_error(
                        title="✅ Payment Invoice Details Created",
                        message={
                            "payment_details_name": payment_details_name
                        }
                    )

                except Exception:

                    frappe.log_error(
                        title="❌ Payment Invoice Details Update Failed",
                        message=frappe.get_traceback()
                    )

                # ---------------- Subscription History Sync ----------------
                if from_site:
                    try:
                        # Resolve Payment Entry name regardless of whether this is a
                        # first callback or a Razorpay retry (where pe is never set).
                        if 'pe' in locals() and pe:
                            # Freshly created in this callback run
                            payment_entry_name = pe.name
                        else:
                            # PE already existed — fetch it from the reference table
                            payment_entry_name = frappe.db.get_value(
                                "Payment Entry Reference",
                                {
                                    "reference_doctype": "Sales Invoice",
                                    "reference_name": reference_docname
                                },
                                "parent"
                            )

                        frappe.log_error(
                            title="Subscription History — Payment Entry Resolved",
                            message={
                                "payment_entry_name": payment_entry_name,
                                "reference_docname": reference_docname
                            }
                        )

                        create_subscription_history_on_source(
                            from_site=from_site,
                            sales_invoice=inv,
                            payment_entry=payment_entry_name,
                            razorpay_payment_id=razorpay_payment_id
                        )
                    except Exception:
                        frappe.log_error(
                            title="❌ Subscription History Sync Failed — Exception in callback",
                            message=frappe.get_traceback()
                        )


                # ---------------- Send Payment Success Email ----------------
                try:
                    send_payment_success_email(inv, razorpay_payment_id, customer_email)
                except Exception:
                    frappe.log_error(
                        title="❌ Payment Email Exception in Callback",
                        message=frappe.get_traceback()
                    )

                # ---------------- Final Success Response ----------------
                success_response = {
                    "status": "success",
                    "message": "Payment processed successfully",
                    "sales_invoice": reference_docname,
                    "payment_status": "Paid",
                    "payment_invoice_details": payment_details_name,
                    "data": {
                        "sales_invoice": reference_docname,
                        "status": "Paid",
                        "payment_invoice_details": payment_details_name
                    }
                }

                frappe.log_error(
                    title="✅ Razorpay Callback Success",
                    message=success_response
                )

                return success_response

            return {
                "status": "success",
                "message": "Payment processed"
            }

        else:

            frappe.log_error(
                title="❌ Payment Not Authorized",
                message=response
            )

            return {
                "status": "failed",
                "message": "Payment not authorized"
            }

    except Exception as e:

        frappe.log_error(
            title="❌ Razorpay Callback Exception",
            message={
                "error": str(e),
                "traceback": frappe.get_traceback(),
                "reference_docname": reference_docname,
                "reference_doctype": reference_doctype,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_order_id": razorpay_order_id,
                "from_site": from_site
            }
        )

        return {
            "status": "error",
            "message": "Payment verification failed",
            "error": str(e)
        }

    finally:

        try:

            if (
                'original_user' in locals()
                and original_user
                and original_user != "Guest"
            ):
                frappe.set_user(original_user)

        except Exception:

            frappe.log_error(
                title="❌ Failed Restoring Original User",
                message=frappe.get_traceback()
            )


def update_billing_account_on_source(from_site, customer_email, package_name=None, sales_invoice_name=None, app_name=None):
    import requests
    import json

    if not from_site or not customer_email:
        return

    settings = frappe.get_single("Billing Settings")
    user = settings.get("billing_user")
    pwd = settings.get_password("billing_user_password")

    if not user or not pwd:
        return

    # Force requests to resolve IPv4 addresses to bypass the IPv6 loopback routing issue in /etc/hosts
    from urllib3.util import connection
    import socket
    orig_allowed_gai_family = connection.allowed_gai_family
    connection.allowed_gai_family = lambda: socket.AF_INET

    try:
        session = requests.Session()

        login_url = f"{from_site}/api/method/login"

        login_resp = session.post(
            login_url,
            json={"usr": user, "pwd": pwd},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            timeout=10,
            verify=False
        )

        login_failed = (
            login_resp.status_code != 200
            or "Invalid login" in login_resp.text
            or "incorrect" in login_resp.text.lower()
            or "error" in login_resp.text.lower() and "full_name" not in login_resp.text.lower()
        )

        if login_failed:
            return

        api_base = f"{from_site}/api/method/quantbit_billing_platform.quantbit_billing_platform.api.update_billing_account_package"
        api_params = {
            "email": customer_email,
            "package_name": package_name or "Standard",
            "app_name": app_name or "",
            "sales_invoice_name": sales_invoice_name or ""
        }

        # API accepts query parameters (same as browser URL format):
        # ?email=...&package_name=...
        update_resp = session.get(
            api_base,
            params=api_params,
            headers={
                "Accept": "application/json"
            },
            timeout=15,
            verify=False
        )

        update_resp.raise_for_status()

        result = update_resp.json()

        return result

    except requests.exceptions.RequestException as req_err:
        frappe.log_error(
            title="Request Exception in update_billing_account_on_source",
            message={
                "error": str(req_err),
                "traceback": frappe.get_traceback()
            }
        )

    except Exception as e:
        frappe.log_error(
            title="Unexpected Exception in update_billing_account_on_source",
            message={
                "error": str(e),
                "traceback": frappe.get_traceback()
            }
        )
    finally:
        connection.allowed_gai_family = orig_allowed_gai_family


def update_payment_invoice_details_on_source(from_site, sales_invoice):
    """
    Creates Payment Invoice Details on source site
    """
    import requests
    from frappe.utils import flt
    from urllib.parse import urlparse

    frappe.log_error(
        title="🚀 update_payment_invoice_details_on_source CALLED",
        message=f"""
        From Site: {from_site}
        Sales Invoice: {sales_invoice.name if sales_invoice else 'None'}
        """
    )

    if not from_site:
        frappe.log_error(
            title="❌ Payment Invoice Details - Missing from_site",
            message="from_site parameter is not provided"
        )
        return None

    settings = frappe.get_single("Billing Settings")

    user = settings.get("billing_user")
    pwd = settings.get_password("billing_user_password")

    if not user or not pwd:
        frappe.log_error(
            title="❌ Payment Invoice Details - Missing Credentials",
            message={
                "user_exists": bool(user),
                "password_exists": bool(pwd)
            }
        )
        return None

    # Force requests to resolve IPv4 addresses to bypass the IPv6 loopback routing issue in /etc/hosts
    from urllib3.util import connection
    import socket
    orig_allowed_gai_family = connection.allowed_gai_family
    connection.allowed_gai_family = lambda: socket.AF_INET

    try:
        site_value = sales_invoice.get("custom_site")

        if not site_value:
            parsed = urlparse(from_site)
            site_value = (
                parsed.netloc.split('.')[0]
                if parsed.netloc else from_site
            )

        customer_email = (
            sales_invoice.get("contact_email")
            or frappe.db.get_value("Customer", sales_invoice.get("customer"), "email_id")
            or ""
            )

        payload = {
            "site": site_value,
            "package_name": sales_invoice.get("custom_package_name") or "",
            "package_type": sales_invoice.get("custom_package_type") or "",
            "app_name": sales_invoice.get("custom_app_name") or "",
            "customer": sales_invoice.get("customer") or "",
            "discount_amount": flt(sales_invoice.get("discount_amount")),
            "grand_total_inr": flt(sales_invoice.get("grand_total")),
            "rounded_total_inr": flt(sales_invoice.get("rounded_total")),
            "rounding_adjustment_inr": flt(sales_invoice.get("rounding_adjustment")),
            "duration": sales_invoice.get("custom_duration") or 0,
            "invoice_id": sales_invoice.name,
            "total": flt(sales_invoice.get("total")),
            "customer_email": customer_email,
            "email": customer_email,                    # Try 2
            "email_id": customer_email,                 # Try 3
            "customer_email_id": customer_email,
            
        }

        frappe.log_error(
            title="Payment Invoice Payload",
            message={
                "from_site": from_site,
                "payload": payload
            }
        )

        session = requests.Session()

        # ---------------- LOGIN ----------------
        login_url = f"{from_site}/api/method/login"

        frappe.log_error(
            title="Billing Login Debug",
            message={
                "login_url": login_url,
                "user": user,
                "password_exists": bool(pwd)
            }
        )

        login_resp = session.post(
            login_url,
            json={
                "usr": user,
                "pwd": pwd
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            timeout=15,
            verify=False
        )

        frappe.log_error(
            title="Billing Login Response",
            message={
                "status_code": login_resp.status_code,
                "response": login_resp.text[:1000]
            }
        )

        if (
            login_resp.status_code != 200
            or "Invalid login credentials" in login_resp.text
        ):
            frappe.log_error(
                title="Login Failed",
                message={
                    "status_code": login_resp.status_code,
                    "response": login_resp.text
                }
            )
            return None

        # ---------------- CREATE PAYMENT INVOICE DETAILS ----------------
        create_url = (
            f"{from_site}/api/resource/Payment%20Invoice%20Details"
        )

        frappe.log_error(
            title="Creating Payment Invoice Details",
            message={
                "url": create_url,
                "payload": payload
            }
        )

        create_resp = session.post(
            create_url,
            json=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            timeout=20,
            verify=False
        )

        frappe.log_error(
            title="Payment Invoice Create Response",
            message={
                "status_code": create_resp.status_code,
                "response": create_resp.text[:1000]
            }
        )

        create_resp.raise_for_status()

        result = create_resp.json()

        name = result.get("data", {}).get("name")

        if name:
            frappe.log_error(
                title="✅ Payment Invoice Details Created",
                message=name
            )
            return name

    except Exception:
        frappe.log_error(
            title="Payment Invoice Details Exception",
            message=frappe.get_traceback()
        )

    finally:
        connection.allowed_gai_family = orig_allowed_gai_family

    return None


def create_subscription_history_on_source(
    from_site,
    sales_invoice,
    payment_entry,
    razorpay_payment_id
):
    """
    Calls the dedicated whitelisted API on the StrideNex site to create a
    Subscription History record.  Uses the same login/session approach as
    update_billing_account_on_source() and update_payment_invoice_details_on_source().

    Failures are logged but never raised so they cannot break the payment flow.
    """
    import requests
    from frappe.utils import flt, today
    from urllib.parse import urlparse

    frappe.log_error(
        title="create_subscription_history_on_source CALLED",
        message=f"""
        From Site: {from_site}
        Sales Invoice: {sales_invoice.name if sales_invoice else 'None'}
        Payment Entry: {payment_entry}
        Razorpay Payment ID: {razorpay_payment_id}
        """
    )

    if not from_site or not sales_invoice:
        frappe.log_error(
            title="Subscription History - Missing Required Params",
            message={
                "from_site": from_site,
                "sales_invoice": sales_invoice.name if sales_invoice else None
            }
        )
        return None

    settings = frappe.get_single("Billing Settings")
    user = settings.get("billing_user")
    pwd = settings.get_password("billing_user_password")

    if not user or not pwd:
        frappe.log_error(
            title="Subscription History - Missing Credentials",
            message={
                "user_exists": bool(user),
                "password_exists": bool(pwd)
            }
        )
        return None

    # Force IPv4 to bypass the IPv6 loopback routing issue in /etc/hosts
    from urllib3.util import connection
    import socket
    orig_allowed_gai_family = connection.allowed_gai_family
    connection.allowed_gai_family = lambda: socket.AF_INET

    try:
        # ---- Build payload from Sales Invoice & Payment Entry ----
        customer_email = (
            sales_invoice.get("contact_email")
            or frappe.db.get_value("Customer", sales_invoice.get("customer"), "email_id")
            or ""
        )

        customer_name = (
            sales_invoice.get("customer_name")
            or frappe.db.get_value("Customer", sales_invoice.get("customer"), "customer_name")
            or ""
        )

        site_value = sales_invoice.get("custom_site") or ""
        if not site_value:
            parsed = urlparse(from_site)
            site_value = (
                parsed.netloc.split('.')[0]
                if parsed.netloc else from_site
            )

        # Use actual invoice date; fall back to creation date if posting_date is absent
        purchase_date = (
            str(sales_invoice.get("posting_date") or "")
            or str(sales_invoice.get("creation") or "")[:10]
            or today()
        )

        payload = {
            "customer_email": customer_email,
            "customer_name": customer_name,
            "package_name": sales_invoice.get("custom_package_name") or "",
            "package_type": sales_invoice.get("custom_package_type") or "",
            "app_name": sales_invoice.get("custom_app_name") or "",
            "duration": sales_invoice.get("custom_duration") or 0,
            "amount": flt(sales_invoice.get("rounded_total") or sales_invoice.get("grand_total")),
            "currency": sales_invoice.get("currency") or "INR",
            "discount": flt(sales_invoice.get("discount_amount")),
            "payment_status": "Paid",
            "purchase_date": purchase_date,
            "sales_invoice_no": sales_invoice.name,
            "payment_entry_no": payment_entry or "",
            "razorpay_payment_id": razorpay_payment_id or "",
            "site": site_value
        }

        frappe.log_error(
            title="Subscription History Payload",
            message={
                "from_site": from_site,
                "payload": payload
            }
        )

        # ---- Session & Login ----
        session = requests.Session()
        login_url = f"{from_site}/api/method/login"

        login_resp = session.post(
            login_url,
            json={"usr": user, "pwd": pwd},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            timeout=15,
            verify=False
        )

        frappe.log_error(
            title="Subscription History Login Response",
            message={
                "status_code": login_resp.status_code,
                "response": login_resp.text[:1000]
            }
        )

        if (
            login_resp.status_code != 200
            or "Invalid login credentials" in login_resp.text
        ):
            frappe.log_error(
                title="Subscription History Login Failed",
                message={
                    "status_code": login_resp.status_code,
                    "response": login_resp.text
                }
            )
            return None

        # ---- Call the dedicated whitelisted API on StrideNex ----
        api_url = (
            f"{from_site}/api/method/"
            "quantbit_billing_platform.quantbit_billing_platform.api.create_subscription_history"
        )

        frappe.log_error(
            title="Subscription History API URL",
            message={"api_url": api_url}
        )

        api_resp = session.post(
            api_url,
            json=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            timeout=20,
            verify=False
        )

        frappe.log_error(
            title="Subscription History API Response",
            message={
                "status_code": api_resp.status_code,
                "response": api_resp.text[:1000]
            }
        )

        api_resp.raise_for_status()

        result = api_resp.json()

        # ---- Validate the response body (HTTP 200 alone is not sufficient) ----
        message_data = result.get("message") or {}
        api_status = message_data.get("status") if isinstance(message_data, dict) else None

        if api_status == "success":
            frappe.log_error(
                title="Subscription History Created",
                message={
                    "name": message_data.get("name"),
                    "message": message_data.get("message"),
                    "result": result
                }
            )
        else:
            frappe.log_error(
                title="Subscription History API Reported Non-Success",
                message={
                    "api_status": api_status,
                    "result": result
                }
            )

        return result

    except requests.exceptions.RequestException as req_err:
        frappe.log_error(
            title="Subscription History Request Exception",
            message={
                "error": str(req_err),
                "traceback": frappe.get_traceback()
            }
        )

    except Exception:
        frappe.log_error(
            title="Subscription History Exception",
            message=frappe.get_traceback()
        )

    finally:
        connection.allowed_gai_family = orig_allowed_gai_family

    return None


import frappe
from frappe.utils import flt, today
import re

@frappe.whitelist(allow_guest=True)
def create_direct_sales_invoice(invoice_details=None):

    """
    Creates Sales Invoice from frontend data with GSTIN support.
    Also creates/updates Customer Address with GSTIN.
    """

    if invoice_details is None:
        invoice_details = {}
    
    # Extract referral code if provided
    referral_code = invoice_details.get("referral_code")
    from_site = invoice_details.get("from_site")
    package_name = invoice_details.get("package_name")
    package_type = invoice_details.get("package_type")
    app_name = invoice_details.get("app_name")
    duration = invoice_details.get("duration")

    frappe.local.flags.ignore_csrf = True

    try:
        res = frappe._dict()
        frappe.log_error(
            title="Invoice Creation Started",
            message={
                "invoice_details": invoice_details,
                "extracted_fields": {
                    "referral_code": referral_code,
                    "from_site": from_site,
                    "package_name": package_name,
                    "package_type": package_type,
                    "app_name": app_name,
                    "duration": duration
                }
            }
        )

        # ── Extract input ───────────────────────────────────────────
        full_name = invoice_details.get("customer_name") or ""
        email = invoice_details.get("customer_email") or ""
        organisation = (invoice_details.get("company") or "").strip()

        company_address = invoice_details.get("company_address")
        state = invoice_details.get("state") or ""
        city = invoice_details.get("city") or ""
        pincode = invoice_details.get("pincode") or ""
        address_type = invoice_details.get("address_type") or "Billing"
        gstin = invoice_details.get("gstin")  # ← EXTRACT GSTIN (can be None)

        frappe.log_error(
            title="Invoice Details Received",
            message={
                "customer": full_name,
                "email": email,
                "company": organisation,
                "address": f"{company_address}, {city}, {state} {pincode}",
                "gstin": gstin if gstin else "(Not provided)"
            }
        )

        mode_of_payment = invoice_details.get("mode_of_payment")
        items_list = invoice_details.get("items", [])
        taxes_list = invoice_details.get("taxes", [])
        description = invoice_details.get("description", "Payment for Services")
        original_amount = flt(invoice_details.get("original_amount", 0))
        discount_amount = flt(invoice_details.get("discount_amount", 0))
        final_amount = flt(invoice_details.get("final_amount", 0))

        amount = original_amount if original_amount > 0 else final_amount

        frappe.log_error(
            title="Invoice Amount Received",
            message={
                "amount": amount,
                "mode_of_payment": mode_of_payment,
                "items_count": len(items_list) if items_list else 0
            }
        )

        # ── Validations ─────────────────────────────────────────────
        if not full_name and not email:
            frappe.throw("Full name or email is required")

        if original_amount <= 0 and final_amount <= 0 and not items_list:
            frappe.throw("Amount must be greater than 0")

        # For Indian addresses, state is mandatory if address is provided
        if company_address and not state:
            frappe.throw("State is required for the billing address")

        # GSTIN Validation - if provided, validate format
        if gstin:
            gstin = gstin.strip()
            if gstin and not re.match(r'^[0-9A-Z]{15}$', gstin):
                frappe.throw("Invalid GSTIN format. GSTIN must be 15 alphanumeric characters (uppercase)")
            frappe.log_error(
                title="GSTIN Validation Passed",
                message={"gstin": gstin}
            )
        else:
            gstin = None
            frappe.log_error(
                title="GSTIN Optional",
                message={"note": "GSTIN not provided"}
            )

        # ── 1. Create / Find Customer ──────────────────────────────
        customer_id = None

        # Try to find existing customer by email
        if email:
            existing_customer = frappe.db.get_value(
                "Customer",
                {"email_id": email},
                "name"
            )
            if existing_customer:
                customer_id = existing_customer
                frappe.log_error(
                    title="Existing Customer Found",
                    message={"customer_id": customer_id, "email": email}
                )

        # Create new customer if not found
        if not customer_id:
            cust = frappe.new_doc("Customer")

            if organisation:
                cust.customer_name = organisation
                cust.customer_type = "Company"
            else:
                cust.customer_name = full_name or "Guest Customer"
                cust.customer_type = "Individual"

            cust.customer_group = "Individual"
            cust.email_id = email

            cust.insert(ignore_permissions=True)
            customer_id = cust.name
            frappe.log_error(
                title="New Customer Created",
                message={"customer_id": customer_id, "name": cust.customer_name}
            )

        # ── 2. Create / Update Address ─────────────────────────────
        if company_address:
            if not state:
                frappe.throw("State is required for the billing address")

            # Look for existing address
            existing_address = frappe.db.exists("Address", {
                "address_title": customer_id,
                "address_type": address_type
            })

            if existing_address:
                frappe.log_error(
                    title="Updating Existing Address",
                    message={"address": existing_address, "customer": customer_id}
                )
                address_doc = frappe.get_doc("Address", existing_address)
                address_doc.address_line1 = company_address
                address_doc.city = city
                address_doc.state = state
                address_doc.pincode = pincode
                address_doc.country = "India"

                # SET GSTIN
                if gstin:
                    address_doc.gstin = gstin
                    frappe.log_error(
                        title="Saving GSTIN to Address",
                        message={"gstin": gstin}
                    )
                else:
                    # Clear GSTIN if not provided
                    address_doc.gstin = None
                    frappe.log_error(
                        title="Clearing GSTIN",
                        message={"note": "GSTIN not provided"}
                    )

                address_doc.save(ignore_permissions=True)
                frappe.log_error(
                    title="Address Updated",
                    message={"address": existing_address}
                )

            else:
                frappe.log_error(
                    title="Creating New Address",
                    message={"customer": customer_id}
                )
                address_doc = frappe.new_doc("Address")
                address_doc.address_title = customer_id
                address_doc.address_type = address_type
                address_doc.address_line1 = company_address
                address_doc.city = city
                address_doc.state = state
                address_doc.pincode = pincode
                address_doc.country = "India"

                #  SET GSTIN
                if gstin:
                    address_doc.gstin = gstin
                    frappe.log_error(
                        title="Setting GSTIN on New Address",
                        message={"gstin": gstin}
                    )

                address_doc.append("links", {
                    "link_doctype": "Customer",
                    "link_name": customer_id
                })
                address_doc.insert(ignore_permissions=True)
                frappe.log_error(
                    title="New Address Created",
                    message={"customer": customer_id}
                )

        # ── 3. Get Company ─────────────────────────────────────────
        accounting_company = (
            frappe.db.get_single_value("Global Defaults", "default_company")
            or frappe.defaults.get_global_default("company")
        )

        if not accounting_company:
            frappe.throw("No default company configured")

        company_currency = frappe.db.get_value(
            "Company", accounting_company, "default_currency"
        )

        if not company_currency:
            frappe.throw("Company has no default currency")

        frappe.log_error(
            title="Accounting Company",
            message={
                "company": accounting_company,
                "currency": company_currency
            }
        )

        # ── 4. Create Sales Invoice ────────────────────────────────
        si = frappe.new_doc("Sales Invoice")

        si.company = accounting_company
        si.customer = customer_id
        si.currency = invoice_details.get("currency") or company_currency

        # Set sales partner if referral code is provided
        if referral_code:
            si.sales_partner = referral_code

        # Set custom fields
        if from_site:
            si.custom_site = from_site
        if package_name:
            si.custom_package_name = package_name
        if package_type:
            si.custom_package_type = package_type
        if app_name:
            si.custom_app_name = app_name
        if duration:
            si.custom_duration = duration

        frappe.log_error(
            title="Sales Invoice Custom Fields Set",
            message={
                "custom_site": si.custom_site,
                "custom_package_name": si.custom_package_name,
                "custom_package_type": si.custom_package_type,
                "custom_app_name": si.custom_app_name,
                "custom_duration": si.custom_duration,
                "duration_input": duration
            }
        )

        frappe.log_error(
            title="Invoice Currency Setup",
            message={
                "invoice_currency": si.currency,
                "company_currency": company_currency,
                "requested_currency": invoice_details.get("currency")
            }
        )

        # Conversion rate
        if si.currency == company_currency:
            si.conversion_rate = 1
        else:
            from erpnext.setup.utils import get_exchange_rate
            si.conversion_rate = flt(get_exchange_rate(
                si.currency, company_currency, today()
            ))

        frappe.log_error(
            title="Conversion Rate",
            message={
                "conversion_rate": si.conversion_rate,
                "invoice_currency": si.currency,
                "company_currency": company_currency
            }
        )

        si.posting_date = today()
        si.due_date = today()
        si.update_stock = 0
        si.set_posting_time = 1
        si.ignore_pricing_rule = 1  # Prevent automatic pricing rules from applying discounts

        # Custom field for mode of payment (if exists)
        if hasattr(si, 'custom_mode_of_payment'):
            si.custom_mode_of_payment = mode_of_payment

        # ── Items ────────────────────────────────────────────────
        # ── Items ────────────────────────────────────────────────
        if items_list:
            frappe.log_error(
                title="Using Items List",
                message={"items_count": len(items_list)}
            )

            for item in items_list:
                frappe.log_error(
                    title="Adding Item",
                    message={
                        "item_code": item.get("item_code"),
                        "qty": item.get("qty"),
                        "rate": item.get("rate")
                    }
                )

                si.append("items", {
                    "item_code": item.get("item_code"),
                    "qty": flt(item.get("qty", 1)),
                    "rate": original_amount if original_amount > 0 else flt(item.get("rate")),
                    "price_list_rate": original_amount if original_amount > 0 else flt(item.get("rate")),
                    "description": item.get("description")
                })

        else:
            default_item_code = "Miscellaneous Service"

            if not frappe.db.exists("Item", default_item_code):
                frappe.throw(f"Item '{default_item_code}' not found")

            frappe.log_error(
                title="Creating Default Item",
                message={
                    "item_code": default_item_code,
                    "rate": original_amount,
                    "description": description
                }
            )

            si.append("items", {
                "item_code": default_item_code,
                "qty": 1,
                "rate": original_amount if original_amount else amount,
                "price_list_rate": original_amount if original_amount else amount,
                "description": description
            })


        # ── Apply Discount ────────────────────────────────────────
        if discount_amount > 0:
            si.apply_discount_on = "Grand Total"
            si.discount_amount = discount_amount

            si.calculate_taxes_and_totals()

        # ── Taxes ────────────────────────────────────────────────
        for tax in taxes_list:
            si.append("taxes", {
                "charge_type": tax.get("charge_type", "Actual"),
                "account_head": tax.get("account_head"),
                "tax_amount": flt(tax.get("tax_amount", 0)),
                "description": tax.get("description")
            })

        frappe.log_error(
            title="Before Save - Invoice Totals",
            message={
                "invoice": si.name,
                "net_total": si.net_total,
                "total": si.total,
                "items_count": len(si.items)
            }
        )

        # ── Save & Submit ────────────────────────────────────────
        si.save(ignore_permissions=True)
        si.submit()
        frappe.db.commit()
        frappe.log_error(
            title="Sales Invoice Created",
            message={"invoice": si.name}
        )

        # Reload to get calculated totals after submission
        si.reload()

        frappe.log_error(
            title="Invoice After Reload",
            message={
                "invoice": si.name,
                "grand_total": si.grand_total,
                "rounded_total": si.rounded_total,
                "net_total": si.net_total,
                "total": si.total,
                "discount_amount": si.discount_amount,
                "items": [
                    {
                        "item_code": i.item_code,
                        "qty": i.qty,
                        "rate": i.rate,
                        "amount": i.amount
                    } for i in si.items
                ],
                "taxes": [
                    {
                        "description": t.description,
                        "tax_amount": t.tax_amount
                    } for t in si.taxes
                ] if si.taxes else []
            }
        )

        # ── Razorpay ─────────────────────────────────────────────
        razorpay_payload = None
        online_modes = ["Razorpay", "Online", "UPI", "Card"]

        if mode_of_payment and any(m.lower() in mode_of_payment.lower() for m in online_modes):
            razorpay_payload = create_razorpay_order(si, application_fee=0)
            res["razorpay_details"] = razorpay_payload

        # ── Response ─────────────────────────────────────────────
        res["message"] = "success"
        res["sales_invoice"] = {
            "name": si.name,
            "customer": si.customer,
            "grand_total": si.grand_total,
            "status": si.status
        }

        frappe.log_error(
            title="Invoice Creation Complete",
            message={"invoice": si.name}
        )
        return res

    except Exception as e:
        error_msg = frappe.get_traceback()
        frappe.log_error(
            title="Direct Sales Invoice Creation Failed",
            message={
                "invoice_details": invoice_details,
                "error": str(e),
                "traceback": error_msg
            }
        )
        return {"error": str(e)}


def send_payment_success_email(invoice_doc, razorpay_payment_id, customer_email=None):
    if not customer_email:
        customer_email = (
            invoice_doc.get("contact_email")
            or frappe.db.get_value("Customer", invoice_doc.customer, "email_id")
        )

    if not customer_email:
        frappe.log_error(
            title="Payment Email Skipped",
            message=f"No email found for Invoice {invoice_doc.name}"
        )
        return

    try:
        import copy

        subject  = f"Payment Received – Invoice {invoice_doc.name}"
        message  = _build_email_html(invoice_doc, razorpay_payment_id)

        # Generate the standard ERPNext invoice PDF from an in-memory doc copy
        # with custom_site blanked so the Site field never appears in the PDF.
        doc_for_print = copy.deepcopy(invoice_doc)
        doc_for_print.custom_site = ""
        pdf_bytes = frappe.get_print(
            "Sales Invoice",
            invoice_doc.name,
            print_format="Standard",
            doc=doc_for_print,
            as_pdf=True
        )

        # Build a plain-text fallback so spam filters don't flag an HTML-only email
        customer_name_txt = invoice_doc.customer_name or invoice_doc.customer
        currency_txt      = invoice_doc.currency or "INR"
        plain_text = (
            f"Payment Successful\n\n"
            f"Dear {customer_name_txt},\n\n"
            f"We have successfully received your payment of "
            f"{currency_txt} {invoice_doc.grand_total} against Invoice {invoice_doc.name}.\n\n"
            f"Transaction ID: {razorpay_payment_id}\n\n"
            f"Please find your invoice attached.\n\n"
            f"Thank you!\n"
        )

        # Fetch the configured outgoing reply-to address (falls back to sender)
        try:
            reply_to_addr = (
                frappe.db.get_single_value("Email Account", "email_id")
                or frappe.db.get_single_value("System Settings", "email_footer_address")
                or ""
            )
        except Exception:
            reply_to_addr = ""

        frappe.sendmail(
            recipients=[customer_email],
            subject=subject,
            message=message,
            text_content=plain_text,
            reply_to=reply_to_addr or None,
            reference_doctype="Sales Invoice",
            reference_name=invoice_doc.name,
            attachments=[{
                "fname": f"{invoice_doc.name}.pdf",
                "fcontent": pdf_bytes
            }],
            add_unsubscribe_link=0,
            headers={
                "X-Mailer": "StrideNex Payment System",
                "X-Entity-Ref-ID": invoice_doc.name,
                "X-Priority": "1",
            },
            now=True
        )
        frappe.log_error(
            title="✅ Payment Email Sent",
            message=f"Sent to {customer_email} for {invoice_doc.name}"
        )
    except Exception as e:
        frappe.log_error(
            title="❌ Payment Email Failed",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────────

def _build_email_html(invoice_doc, razorpay_payment_id):
    """Returns a polished, inline-CSS transactional email body."""
    customer_name = invoice_doc.customer_name or invoice_doc.customer
    currency      = invoice_doc.currency or "INR"
    grand_total   = invoice_doc.grand_total or 0
    invoice_name  = invoice_doc.name

    try:
        formatted_total = f"{currency} {float(grand_total):,.2f}"
    except Exception:
        formatted_total = f"{currency} {grand_total}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f8;padding:40px 16px;">
  <tr><td align="center">
  <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.10);">

    <!-- ── Header ── -->
    <tr>
      <td style="background:linear-gradient(135deg,#1a237e 0%,#1565c0 100%);padding:36px 40px;text-align:center;">
        <p style="margin:0;font-size:26px;font-weight:800;color:#fff;letter-spacing:-0.5px;">StrideNex</p>
        <p style="margin:6px 0 0;font-size:12px;font-weight:600;color:#90caf9;text-transform:uppercase;letter-spacing:2px;">Payment Confirmation</p>
      </td>
    </tr>

    <!-- ── Success badge ── -->
    <tr>
      <td style="background:#ffffff;padding:36px 40px 20px;text-align:center;">
        <div style="display:inline-block;width:68px;height:68px;background:#e8f5e9;border-radius:50%;line-height:68px;font-size:34px;margin-bottom:18px;">&#10003;</div>
        <h2 style="margin:0 0 8px;font-size:22px;color:#1a237e;font-weight:700;">Payment Successful!</h2>
        <p style="margin:0;font-size:15px;color:#607d8b;">Your payment has been received and confirmed.</p>
      </td>
    </tr>

    <!-- ── Amount card ── -->
    <tr>
      <td style="background:#ffffff;padding:4px 40px 28px;">
        <div style="background:linear-gradient(135deg,#e8eaf6,#e3f2fd);border-radius:12px;padding:22px;text-align:center;">
          <p style="margin:0 0 6px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#607d8b;">Amount Paid</p>
          <p style="margin:0;font-size:36px;font-weight:800;color:#1a237e;">{formatted_total}</p>
        </div>
      </td>
    </tr>

    <!-- ── Greeting ── -->
    <tr>
      <td style="background:#ffffff;padding:0 40px 24px;">
        <p style="margin:0;font-size:15px;color:#37474f;line-height:1.7;">
          Dear <strong>{customer_name}</strong>,<br>
          We have successfully received your payment. Your invoice is attached to this email for your records.
        </p>
      </td>
    </tr>

    <!-- ── Details table ── -->
    <tr>
      <td style="background:#ffffff;padding:0 40px 36px;">
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border:1px solid #e3e8ef;border-radius:10px;overflow:hidden;font-size:14px;">
          <tr style="background:#f5f7fa;">
            <td colspan="2" style="padding:11px 18px;font-size:10px;font-weight:700;letter-spacing:1.5px;
                                    text-transform:uppercase;color:#607d8b;">Payment Details</td>
          </tr>
          <tr>
            <td style="padding:13px 18px;border-top:1px solid #f0f0f0;color:#78909c;">Invoice Number</td>
            <td style="padding:13px 18px;border-top:1px solid #f0f0f0;color:#1a237e;font-weight:600;">{invoice_name}</td>
          </tr>
          <tr style="background:#fafbfc;">
            <td style="padding:13px 18px;border-top:1px solid #f0f0f0;color:#78909c;">Transaction ID</td>
            <td style="padding:13px 18px;border-top:1px solid #f0f0f0;color:#1a237e;font-weight:600;
                        font-family:Consolas,monospace;font-size:13px;">{razorpay_payment_id}</td>
          </tr>
          <tr>
            <td style="padding:13px 18px;border-top:1px solid #f0f0f0;color:#78909c;">Status</td>
            <td style="padding:13px 18px;border-top:1px solid #f0f0f0;">
              <span style="background:#e8f5e9;color:#2e7d32;font-size:11px;font-weight:700;
                            padding:4px 12px;border-radius:20px;letter-spacing:0.5px;">&#10003; PAID</span>
            </td>
          </tr>
        </table>
      </td>
    </tr>

    <!-- ── Footer ── -->
    <tr>
      <td style="background:#1a237e;padding:24px 40px;text-align:center;">
        <p style="margin:0 0 6px;font-size:14px;color:#90caf9;font-weight:600;">Thank you for choosing StrideNex!</p>
        <p style="margin:0;font-size:11px;color:#5c6bc0;">This is an automated message — please do not reply directly.</p>
      </td>
    </tr>

  </table>
  </td></tr>
</table>
</body>
</html>"""

