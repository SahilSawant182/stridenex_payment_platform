import frappe

def get_context(context):
    """Pass API credentials from Billing Settings to template context"""
    try:
        billing_settings = frappe.get_doc("Billing Settings", "Billing Settings")
        context.api_key = billing_settings.api_key
        context.api_secret = billing_settings.api_secret
    except Exception as e:
        frappe.log_error(f"Error fetching Billing Settings: {str(e)}")
        context.api_key = ""
        context.api_secret = ""
    
    return context
