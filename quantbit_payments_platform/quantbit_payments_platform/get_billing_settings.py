import frappe


@frappe.whitelist(allow_guest=True)
def get_billing_settings():
    """Fetch Billing Settings for partner portal"""

    try:
        settings = frappe.get_single("Billing Settings")
        return {
            "status": "success",
            "data": {
                "partners_commission_rate": settings.partners_commission_rate or 0,
                "sys_url": settings.sys_url
            }
        }

    except Exception as e:

        frappe.log_error(
            frappe.get_traceback(),
            "Get Billing Settings Error"
        )

        return {
            "status": "error",
            "message": str(e)
        }