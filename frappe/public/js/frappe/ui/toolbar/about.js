frappe.provide("frappe.ui.misc");
frappe.ui.misc.about = function () {
	if (frappe.ui.misc.about_dialog) {
		frappe.ui.misc.about_dialog.show();
		return;
	}

	const dialog = new frappe.ui.Dialog({ title: __("Exe ERP") });

	$(dialog.body).html(
		`<div>
				<p>${__("Enterprise Resource Planning by AskExe")}</p>

				<p>
					<i class='fa fa-globe fa-fw'></i>
					${__("Website")}:
					<a href='https://askexe.com' target='_blank'>https://askexe.com</a>
				</p>

				<p>
					<i class='fa fa-github fa-fw'></i>
					${__("Source Code")}:
					<a href='https://github.com/AskExe' target='_blank'>https://github.com/AskExe</a>
				</p>

				<hr>

				<div class="d-flex align-items-center justify-content-between">
					<h4>${__("Installed Apps")}</h4>
					<button class="btn action-btn hidden" id="copy-apps-info"
					title="${__("Copy Apps Version")}"
					style="margin-bottom: var(--margin-md);">
						${frappe.utils.icon("clipboard")}
					</button>
				</div>

				<div id='about-app-versions'>${__("Loading versions...")}</div>
				<p>
					<b>
						<a href="/attribution" target="_blank" class="text-muted">
							${__("Dependencies & Licenses")}
						</a>
					</b>
				</p>

				<hr>

				<p class='text-muted'>${__("&copy; AskExe and contributors. Built on Frappe Framework (AGPL).")} </p>
			</div>`
	);

	frappe.ui.misc.about_dialog = dialog;

	frappe.ui.misc.about_dialog.on_page_show = function () {
		if (!frappe.versions) {
			frappe.call({
				method: "frappe.utils.change_log.get_versions",
				callback: function (r) {
					show_versions(r.message);
				},
			});
		} else {
			show_versions(frappe.versions);
		}
	};

	const show_versions = function (versions) {
		const $wrap = $("#about-app-versions").empty();
		let app = {};

		function get_version_text(app) {
			if (app.branch) {
				return `v${app.branch_version || app.version} (${app.branch})`;
			} else {
				return `v${app.version}`;
			}
		}

		for (const app_name in versions) {
			app = versions[app_name];
			const title = `${app_name}: ${app.branch_version || app.version}`;
			const text = `<p class='app-version' role='button' title='${title}'>
							<b>${app.title}:</b> ${get_version_text(app)}
						</p>`;
			$(text).appendTo($wrap);
		}

		frappe.versions = versions;

		if (frappe.versions) {
			$(dialog.body).find("#copy-apps-info").removeClass("hidden");
		}
	};

	const code_block = (snippet, lang = "") => "```" + lang + "\n" + snippet + "\n```";

	// Listener for copying installed apps info
	$(dialog.body).on("click", "#copy-apps-info", function () {
		if (!frappe.versions) return;

		const versions = Object.entries(frappe.versions).reduce((acc, [key, app]) => {
			acc[key] = app.branch_version || app.version;
			return acc;
		}, {});

		frappe.utils.copy_to_clipboard(code_block(JSON.stringify(versions, null, "\t"), "json"));
	});

	// Listener for copy app version
	$(dialog.body).on("click", ".app-version", function () {
		const title = $(this).attr("title");
		if (title) {
			frappe.utils.copy_to_clipboard(title);
		}
	});

	frappe.ui.misc.about_dialog.show();
};
