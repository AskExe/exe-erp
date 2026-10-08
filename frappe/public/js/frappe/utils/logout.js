// Hosted editor logout uses the validated fixed origin supplied by native boot.
frappe.company_editor_logout = function () {
	if (frappe.company_editor_logout_pending) return frappe.company_editor_logout_pending;
	const config = frappe.boot?.company_editor;
	if (config?.version !== 2 || !/^https:\/\/auth\.[a-z0-9.-]+$/.test(config.auth_origin)) {
		return Promise.reject(new Error("invalid_hosted_logout_origin"));
	}
	frappe.company_editor_terminal = true;
	if (frappe.app) frappe.app.logged_out = true;
	document.body.inert = true;
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), 9000);
	frappe.company_editor_logout_pending = (async () => {
		try {
			await fetch("/api/method/logout", {
				method: "POST", credentials: "same-origin", signal: controller.signal,
				headers: {"Content-Type": "application/json", "X-Frappe-CSRF-Token": frappe.csrf_token},
				body: "{}",
			});
		} catch (_error) {
			// Parent logout remains necessary even if local cleanup is unconfirmed.
		} finally {
			clearTimeout(timer);
			window.location.assign(config.auth_origin + "/logout");
		}
	})();
	return frappe.company_editor_logout_pending;
};

frappe.logout = function () {
	if (frappe.boot?.company_editor?.version === 2) return frappe.company_editor_logout();
	const switcher = document.querySelector("exe-service-switcher");
	if (switcher) return switcher._logout();
	frappe.call({
		method: "logout",
		callback: function (r) {
			if (r.exc) {
				return;
			}
			window.location.href = "/login";
		},
	});
};
