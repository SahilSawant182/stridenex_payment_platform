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


@frappe.whitelist()
def uat_create_supplier(supplier_email, supplier_name=None, gstin=None, tax_withholding_category=None):
    """
    Creates a Supplier on the UAT site if it doesn't already exist.
    """
    supplier_name_val = frappe.db.get_value("Supplier", {"email_id": supplier_email}, "name")
    if not supplier_name_val:
        supplier_name_val = frappe.db.get_value("Supplier", {"supplier_name": supplier_email}, "name")
    
    twc = tax_withholding_category or "Professional Fees - Individual"
    if twc and not frappe.db.exists("Tax Withholding Category", twc):
        twc = None

    if not supplier_name_val:
        s_dict = {
            "doctype": "Supplier",
            "supplier_name": supplier_name or supplier_email,
            "supplier_group": "All Supplier Groups",
            "supplier_type": "Company",
            "email_id": supplier_email
        }
        if gstin:
            s_dict["gstin"] = gstin
        if twc:
            s_dict["tax_withholding_category"] = twc
        s = frappe.get_doc(s_dict)
        s.insert(ignore_permissions=True)
        supplier_name_val = s.name
    else:
        if twc:
            frappe.db.set_value("Supplier", supplier_name_val, "tax_withholding_category", twc)
            frappe.db.commit()
            
    return {"supplier_name": supplier_name_val}


@frappe.whitelist()
def uat_create_paid_sales_invoice(student_email, student_name, amount, offering_type, payment_ref, company=None):
    """
    Creates a Customer, Item (if needed), Sales Invoice, and Payment Entry on UAT.
    """
    original_user = frappe.session.user
    try:
        frappe.set_user("Administrator")
        
        company = company or frappe.defaults.get_global_default("company") or "Quantbit Technologies Pvt Ltd"
        
        # 1. Get or create Customer
        customer = frappe.db.get_value("Customer", {"email_id": student_email}, "name")
        if not customer:
            if frappe.db.exists("Customer", student_email):
                customer = student_email
            else:
                cust_group = "Individual" if frappe.db.exists("Customer Group", "Individual") else (frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or "All Customer Groups")
                terr = "India" if frappe.db.exists("Territory", "India") else (frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories")
                cust_doc = frappe.get_doc({
                    "doctype": "Customer",
                    "customer_name": student_name or student_email,
                    "customer_type": "Individual",
                    "email_id": student_email,
                    "customer_group": cust_group,
                    "territory": terr
                })
                cust_doc.insert(ignore_permissions=True)
                customer = cust_doc.name
                
        # 2. Get or create Item
        item_code = offering_type
        if not frappe.db.exists("Item", item_code):
            item_group = "Services" if frappe.db.exists("Item Group", "Services") else "All Item Groups"
            uom = "Nos" if frappe.db.exists("UOM", "Nos") else "Unit"
            
            hsn_code = frappe.db.get_value("GST HSN Code", {"hsn_code": ["like", "99%"]}, "name")
            if not hsn_code:
                hsn_code = frappe.db.get_value("GST HSN Code", {}, "name")
                
            item_dict = {
                "doctype": "Item",
                "item_code": item_code,
                "item_name": item_code,
                "item_group": item_group,
                "stock_uom": uom,
                "is_stock_item": 0
            }
            if hsn_code:
                item_dict["gst_hsn_code"] = hsn_code
                
            item = frappe.get_doc(item_dict)
            if frappe.db.exists("Item Tax Template", "GST 18% - QTPL"):
                item.append("taxes", {"item_tax_template": "GST 18% - QTPL"})
            item.insert(ignore_permissions=True)
        else:
            item = frappe.get_doc("Item", item_code)
            if not item.taxes and frappe.db.exists("Item Tax Template", "GST 18% - QTPL"):
                item.append("taxes", {"item_tax_template": "GST 18% - QTPL"})
                item.save(ignore_permissions=True)

        # 3. Create Sales Invoice
        debit_to = frappe.db.get_value("Account", {"account_type": "Receivable", "company": company}, "name") or f"Debtors - {frappe.db.get_value('Company', company, 'abbr')}"
        
        invoice = frappe.get_doc({
            "doctype": "Sales Invoice",
            "company": company,
            "customer": customer,
            "posting_date": frappe.utils.today(),
            "due_date": frappe.utils.today(),
            "currency": "INR",
            "debit_to": debit_to,
            "taxes_and_charges": "Output GST In-state - QTPL" if frappe.db.exists("Sales Taxes and Charges Template", "Output GST In-state - QTPL") else None,
            "items": [{
                "item_code": item_code,
                "qty": 1,
                "rate": float(amount),
                "income_account": "Sales - QTPL" if frappe.db.exists("Account", "Sales - QTPL") else None
            }]
        })
        invoice.set_missing_values()
        invoice.calculate_taxes_and_totals()
        invoice.insert(ignore_permissions=True)
        invoice.submit()

        # 4. Create Payment Entry
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
        pe = get_payment_entry("Sales Invoice", invoice.name)
        pe.reference_no = payment_ref
        pe.reference_date = frappe.utils.today()
        
        paid_to = "Razorpay Clearing - QTPL" if frappe.db.exists("Account", "Razorpay Clearing - QTPL") else None
        if not paid_to:
            paid_to = frappe.db.get_value("Account", {"account_name": ["like", "%Razorpay%"], "company": company}, "name")
        if not paid_to:
            paid_to = frappe.db.get_value("Company", company, "default_bank_account")
        if not paid_to:
            paid_to = frappe.db.get_value("Account", {"account_type": "Bank", "company": company}, "name")
            
        pe.paid_to = paid_to
        pe.insert(ignore_permissions=True)
        pe.submit()

        return {
            "sales_invoice": invoice.name,
            "payment_entry": pe.name
        }
        
    finally:
        frappe.set_user(original_user)


@frappe.whitelist()
def uat_create_purchase_invoice_for_payout(mentor_email, gross_amount, commission_amount, refund_amount, total_penalties, sheet_name, company=None):
    """
    Creates a Purchase Invoice for a mentor payout on UAT.
    """
    original_user = frappe.session.user
    try:
        frappe.set_user("Administrator")
        
        company = company or frappe.defaults.get_global_default("company") or "Quantbit Technologies Pvt Ltd"
        
        credit_to = frappe.db.get_value("Account", {"account_type": "Payable", "company": company}, "name")
        if not credit_to:
            credit_to = frappe.db.get_value("Account", {"company": company, "is_group": 0, "root_type": "Liability"}, "name")
            
        # 1. Get or create Supplier
        supplier = frappe.db.get_value("Supplier", {"email_id": mentor_email}, "name") or frappe.db.get_value("Supplier", {"supplier_name": mentor_email}, "name")
        if not supplier:
            twc = "Professional Fees - Individual" if frappe.db.exists("Tax Withholding Category", "Professional Fees - Individual") else None
            s_dict = {
                "doctype": "Supplier",
                "supplier_name": mentor_email,
                "supplier_group": "All Supplier Groups",
                "email_id": mentor_email
            }
            if twc:
                s_dict["tax_withholding_category"] = twc
            s = frappe.get_doc(s_dict)
            s.insert(ignore_permissions=True)
            supplier = s.name
            
        # 2. Get or create item
        mentor_services_item = "Mentor Services"
        if not frappe.db.exists("Item", mentor_services_item):
            item_group = "Services" if frappe.db.exists("Item Group", "Services") else "All Item Groups"
            uom = "Nos" if frappe.db.exists("UOM", "Nos") else "Unit"
            hsn_code = frappe.db.get_value("GST HSN Code", {"hsn_code": ["like", "99%"]}, "name")
            item_dict = {
                "doctype": "Item",
                "item_code": mentor_services_item,
                "item_name": mentor_services_item,
                "item_group": item_group,
                "stock_uom": uom,
                "is_stock_item": 0
            }
            if hsn_code:
                item_dict["gst_hsn_code"] = hsn_code
            item = frappe.get_doc(item_dict)
            if frappe.db.exists("Item Tax Template", "GST 18% - QTPL"):
                item.append("taxes", {"item_tax_template": "GST 18% - QTPL"})
            item.insert(ignore_permissions=True)

        items = []
        
        # Helper to get/create accounts
        def get_acc(name, acc_type, root):
            abbr = frappe.get_cached_value("Company", company, "abbr")
            full_name = f"{name} - {abbr}"
            acc = frappe.db.get_value("Account", {"name": full_name}, "name")
            if not acc:
                acc = frappe.db.get_value("Account", {"account_name": name, "company": company}, "name")
            if not acc:
                parent = frappe.db.get_value("Account", {"company": company, "is_group": 1, "root_type": root}, "name")
                acc_doc = frappe.get_doc({
                    "doctype": "Account",
                    "account_name": name,
                    "account_type": acc_type,
                    "parent_account": parent,
                    "company": company,
                    "is_group": 0
                })
                acc_doc.insert(ignore_permissions=True)
                acc = acc_doc.name
            return acc

        mentor_expense_account = get_acc("Mentor Expense", "Expense Account", "Expense")
        commission_account = get_acc("Mentor Commission", "Income Account", "Income")
        penalty_account = get_acc("Penalty Payable", "Liability", "Liability")

        # Add items
        items.append({
            "item_code": mentor_services_item,
            "item_name": f"Mentorship Gross completed - Sheet: {sheet_name}",
            "qty": 1,
            "rate": float(gross_amount),
            "price_list_rate": float(gross_amount),
            "amount": float(gross_amount),
            "expense_account": mentor_expense_account
        })
        
        if float(commission_amount) > 0:
            items.append({
                "item_code": mentor_services_item,
                "item_name": f"Commission Deduction - Sheet: {sheet_name}",
                "qty": 1,
                "rate": -float(commission_amount),
                "price_list_rate": -float(commission_amount),
                "amount": -float(commission_amount),
                "expense_account": commission_account
            })
            
        if float(refund_amount) > 0:
            items.append({
                "item_code": mentor_services_item,
                "item_name": f"Student Refund Deduction - Sheet: {sheet_name}",
                "qty": 1,
                "rate": -float(refund_amount),
                "price_list_rate": -float(refund_amount),
                "amount": -float(refund_amount),
                "expense_account": penalty_account
            })
            
        if float(total_penalties) > 0:
            items.append({
                "item_code": mentor_services_item,
                "item_name": f"Absenteeism Penalty Deduction - Sheet: {sheet_name}",
                "qty": 1,
                "rate": -float(total_penalties),
                "price_list_rate": -float(total_penalties),
                "amount": -float(total_penalties),
                "expense_account": penalty_account
            })

        pi = frappe.get_doc({
            "doctype": "Purchase Invoice",
            "supplier": supplier,
            "company": company,
            "posting_date": frappe.utils.today(),
            "credit_to": credit_to,
            "ignore_pricing_rule": 1,
            "apply_tds": 1,
            "items": items
        })
        pi.insert(ignore_permissions=True)
        pi.submit()

        return {"purchase_invoice": pi.name}
        
    finally:
        frappe.set_user(original_user)


@frappe.whitelist(allow_guest=True)
def uat_create_razorpay_order(amount, receipt):
    from payments.utils import get_payment_gateway_controller
    controller = get_payment_gateway_controller("Razorpay")
    controller.init_client()
    amount_in_paise = int(float(amount) * 100)
    order_payload = {
        "amount": amount_in_paise,
        "currency": "INR",
        "receipt": receipt
    }
    order = controller.client.order.create(data=order_payload)
    return {
        "order_id": order.get("id"),
        "api_key": controller.api_key
    }

@frappe.whitelist(allow_guest=True)
def uat_verify_razorpay_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
    from payments.utils import get_payment_gateway_controller
    controller = get_payment_gateway_controller("Razorpay")
    controller.init_client()
    try:
        controller.client.utility.verify_payment_signature({
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature
        })
        return {"status": "success"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}
