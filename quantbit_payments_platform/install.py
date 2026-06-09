import frappe
from quantbit_payments_platform.create_billing_user import after_install as create_billing_user


def after_install():
    create_miscellaneous_service_item()
    create_billing_user()

def create_miscellaneous_service_item():
    if not frappe.db.exists("Item", "Miscellaneous Service"):
        item = frappe.new_doc("Item")
        item.item_code = "Miscellaneous Service"
        item.item_name = "Miscellaneous Service"
        item.item_group = "Services"
        item.stock_uom = "Nos"
        item.gst_hsn_code="998311"
        
        item.insert(ignore_permissions=True)
        frappe.db.commit()