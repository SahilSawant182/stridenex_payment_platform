import frappe
from frappe.utils import getdate, today


@frappe.whitelist(allow_guest=True)
def sync_coupon_code(**data):

    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw("Authentication required")

    if not data:
        data = frappe.request.json or {}

    coupon_code = data.get("coupon_code")

    if not coupon_code:
        frappe.throw("Coupon Code missing")

    existing = frappe.db.exists("Coupon Code", coupon_code)

    if existing:

        doc = frappe.get_doc("Coupon Code", coupon_code)
        doc.update(data)
        doc.save(ignore_permissions=True)

    else:

        doc = frappe.get_doc({
            "doctype": "Coupon Code",
            "name": coupon_code,
            **data
        })

        doc.insert(ignore_permissions=True)

    return "success"


@frappe.whitelist(allow_guest=True)
def delete_coupon_code(**data):

    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw("Authentication required")

    if not data:
        data = frappe.request.json or {}

    coupon_code = data.get("coupon_code")

    if not coupon_code:
        frappe.throw("Coupon Code missing")

    existing = frappe.db.exists("Coupon Code", coupon_code)

    if existing:

        frappe.delete_doc(
            "Coupon Code",
            coupon_code,
            ignore_permissions=True
        )

    return "success"


@frappe.whitelist(allow_guest=True)
def sync_pricing_rule(**kwargs):

    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw("Authentication required")

    data = kwargs or frappe.request.json or {}

    name = data.get("name") or data.get("title")

    if not name:
        frappe.throw("Pricing Rule name missing")

    existing = frappe.db.exists("Pricing Rule", name)

    if existing:

        doc = frappe.get_doc("Pricing Rule", name)
        doc.update(data)
        doc.save(ignore_permissions=True)

    else:

        doc = frappe.get_doc({
            "doctype": "Pricing Rule",
            "name": name,
            **data
        })

        doc.insert(ignore_permissions=True)

    return "success"


@frappe.whitelist(allow_guest=True)
def delete_pricing_rule(**data):

    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw("Authentication required")

    if not data:
        data = frappe.request.json or {}

    pricing_rule = data.get("pricing_rule")

    if not pricing_rule:
        frappe.throw("Pricing Rule name missing")

    existing = frappe.db.exists("Pricing Rule", pricing_rule)

    if existing:

        frappe.delete_doc(
            "Pricing Rule",
            pricing_rule,
            ignore_permissions=True
        )

    return "success"


@frappe.whitelist(allow_guest=True)
def validate_coupon_code(coupon_code, site):

    coupon = frappe.db.get_value(
        "Coupon Code",
        coupon_code,
        [
            "name",
            "pricing_rule",
            "custom_site",
            "valid_from",
            "valid_upto"
        ],
        as_dict=True
    )

    if not coupon:
        return {
            "status": "invalid",
            "message": "Coupon not found"
        }

    if coupon.custom_site != site:
        return {
            "status": "invalid",
            "message": "Coupon not valid for this site"
        }

    # Check valid date range
    today_date = getdate(today())

    if coupon.valid_from and today_date < getdate(coupon.valid_from):
        return {
            "status": "invalid",
            "message": f"Coupon is not yet valid. Valid from {coupon.valid_from}"
        }

    if coupon.valid_upto and today_date > getdate(coupon.valid_upto):
        return {
            "status": "invalid",
            "message": f"Coupon has expired. Valid until {coupon.valid_upto}"
        }

    rule = frappe.get_doc(
        "Pricing Rule",
        coupon.pricing_rule
    )

    return {
        "status": "valid",
        "discount_percentage": rule.discount_percentage
    }


@frappe.whitelist(allow_guest=True)
def validate_referral_code(referral_code, site):
    """Validate if referral code exists as a Sales Partner"""

    sales_partner = frappe.get_all(
        "Sales Partner",
        filters={
            "name": referral_code
        },
        fields=[
            "name",
            "partner_name",
        ],
        limit=1
    )

    if not sales_partner:
        return {
            "status": "invalid",
            "message": "Invalid referral code"
        }

    partner = sales_partner[0]

    return {
        "status": "valid",
        "partner_name": partner.get("partner_name"),
        "referral_code": partner.get("name")
    }



@frappe.whitelist(allow_guest=True)
def get_partner_portal_settings():

    settings = frappe.get_single("Billing Settings")

    return {
        "sys_url": settings.sys_url
    }