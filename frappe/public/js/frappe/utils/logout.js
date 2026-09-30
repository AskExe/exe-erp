frappe.logout = function () {
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
