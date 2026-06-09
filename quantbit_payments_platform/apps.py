import frappe


@frappe.whitelist(allow_guest=True)
def get_installed_apps():
    """
    Get list of installed apps on the current site.
    Whitelisted API method for public access.
    """
    try:
        # Get installed app names
        installed_app_names = frappe.get_installed_apps()
        
        if not installed_app_names:
            return {
                "message": "success",
                "data": []
            }
        
        apps = []
        for app_name in installed_app_names:
            try:
                # Get app metadata (title, icon, etc.)
                app_title_hooks = frappe.get_hooks("app_title", app_name=app_name) or []
                app_icon_hooks = frappe.get_hooks("app_icon", app_name=app_name) or []
                
                # Extract first value from list or use default
                title = app_title_hooks[0] if app_title_hooks else app_name.title()
                icon = app_icon_hooks[0] if app_icon_hooks else "⚙️"
                
                apps.append({
                    "name": app_name,
                    "app_name": app_name,
                    "title": title,
                    "icon": icon,
                    "status": "installed"
                })
            except Exception as app_err:
                # If metadata fetch fails, use defaults
                apps.append({
                    "name": app_name,
                    "app_name": app_name,
                    "title": app_name.title(),
                    "icon": "⚙️",
                    "status": "installed"
                })
        
        # Sort apps alphabetically by name
        apps.sort(key=lambda x: x["name"].lower())
        
        return {
            "message": "success",
            "data": apps
        }
    
    except Exception as e:
        frappe.logger().error(f"Error fetching installed apps: {str(e)}")
        return {
            "message": "error",
            "error": str(e)
        }