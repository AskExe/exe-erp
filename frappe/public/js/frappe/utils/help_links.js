frappe.provide("frappe.help.help_links");

// Docs base — configurable via site_config "docs_url" (System Settings → Exe),
// defaults to Exe's hosted docs. White-label customers override this.
const _docsBase = (frappe.boot && frappe.boot.docs_url) || "https://docs.askexe.com";

frappe.help.help_links["data-import-tool"] = [
	{
		label: "Importing and Exporting Data",
		url: _docsBase + "/erp/docs/user/manual/en/data",
	},
];

frappe.help.help_links["modules/Setup"] = [
	{
		label: "Users and Permissions",
		url: "https://frappeframework.com/docs/user/en/basics/users-and-permissions",
	},
	{
		label: "System Settings",
		url: _docsBase + "/erp/docs/user/manual/en/system-settings",
	},
	{
		label: "Data Management",
		url: _docsBase + "/erp/docs/user/manual/en/data",
	},
	{ label: "Email", url: _docsBase + "/erp/docs/user/manual/en/email" },
	{ label: "Printing and Branding", url: _docsBase + "/erp/docs/user/manual/en/print" },
];

frappe.help.help_links["List/User"] = [
	{
		label: "Adding Users",
		url: _docsBase + "/erp/docs/user/manual/en/adding-users",
	},
	{
		label: "Rename User",
		url: _docsBase + "/erp/docs/user/manual/en/renaming-documents",
	},
];

frappe.help.help_links["permission-manager"] = [
	{
		label: "Role Permissions Manager",
		url: "https://frappeframework.com/docs/user/en/basics/users-and-permissions#role-permissions-manager",
	},
];

frappe.help.help_links["user-permissions"] = [
	{
		label: "User Permissions",
		url: "https://frappeframework.com/docs/user/en/basics/users-and-permissions#user-permissions",
	},
];

frappe.help.help_links["Form/System Settings"] = [
	{
		label: "System Settings",
		url: _docsBase + "/erp/docs/user/manual/en/system-settings",
	},
];

frappe.help.help_links["List/Email Account"] = [
	{
		label: "Email Account",
		url: _docsBase + "/erp/docs/user/manual/en/email-account",
	},
];

frappe.help.help_links["List/Notification"] = [
	{
		label: "Notification",
		url: _docsBase + "/erp/docs/user/manual/en/notifications",
	},
];

frappe.help.help_links["Form/Print Settings"] = [
	{
		label: "Print Settings",
		url: _docsBase + "/erp/docs/user/manual/en/print-settings",
	},
];

frappe.help.help_links["print-format-builder"] = [
	{
		label: "Print Format Builder",
		url: _docsBase + "/erp/docs/user/manual/en/print-format-builder",
	},
];
