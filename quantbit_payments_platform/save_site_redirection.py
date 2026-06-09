import frappe
from frappe.utils import now
from datetime import datetime


@frappe.whitelist(allow_guest=True)
def save_site_redirect(site_name):
	
	if not site_name:
		return {"status": "error", "message": "Site name is required"}
	
	try:
		
		doc = None
		try:
			doc = frappe.get_doc("Sites Redirected", "Sites Redirected")
		except frappe.DoesNotExistError:
			
			doc = frappe.new_doc("Sites Redirected")
			doc.name = "Sites Redirected"
			doc.site_name = site_name
			doc.insert(ignore_permissions=True)
			return {"status": "success", "message": f"Site {site_name} recorded"}
		
		
		doc.site_name = site_name
		doc.save(ignore_permissions=True)
		
		return {"status": "success", "message": f"Site {site_name} recorded"}
	
	except Exception as e:
		frappe.log_error(f"Error saving site redirection: {str(e)}", "Save Site Redirection")
		return {"status": "error", "message": str(e)}
