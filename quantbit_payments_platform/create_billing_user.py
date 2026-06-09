import frappe
from frappe.utils.password import update_password


def after_install():
    create_billing_user()


def create_billing_user():
    email = "billinguser@gmail.com"


    if frappe.db.exists("User", email):
        frappe.logger().info(f"User already exists: {email}")
        return


    roles = frappe.get_all(
        "Role",
        filters={"name": ["not in", ["Guest", "All"]]},
        pluck="name"
    )

   
    user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": "Billing",
        "last_name": "User",
        "enabled": 1,
        "user_type": "System User",
        "send_welcome_email": 0
    })

   
    for role in roles:
        user.append("roles", {
            "role": role
        })

    user.insert(ignore_permissions=True)

    
    update_password(email, "Admin@123")

    frappe.db.commit()

    frappe.logger().info("Billing user created successfully")