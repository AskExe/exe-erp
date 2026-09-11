// Background widgets handle failures inline, while normal session-expiry
// handling remains in frappe.request. Preserve the full error response.
export function request_widget_data(method, args) {
	return new Promise((resolve, reject) => {
		frappe.call({
			method,
			args,
			silent: true,
			callback: (response) => resolve(response.message),
			error: reject,
		});
	});
}

export function widget_error_state(error) {
	const response = error?.responseJSON || error || {};
	if (error?.status === 403 || response.exc_type === "PermissionError") {
		return {
			message: __(
				"You do not have access to this data. Ask your workspace administrator for access."
			),
			retry: false,
		};
	}
	if (
		[400, 422].includes(error?.status) ||
		["ValidationError", "MandatoryError"].includes(response.exc_type)
	) {
		return {
			message: __("This widget needs configuration. Check its filters and settings."),
			retry: false,
		};
	}
	return { message: __("Unable to load this data. Please try again."), retry: true };
}
