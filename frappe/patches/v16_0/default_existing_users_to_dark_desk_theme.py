"""Flip existing users from a stored ``desk_theme`` of "Light" to "Dark".

Why this exists
---------------
The desk now defaults to Dark (``frappe.sessions.get_default_desk_theme``), but
that default is only consulted when ``User.desk_theme`` is empty. On a site that
has been running, it is not empty: the column carries a schema-level default of
``'Light'`` from the era when the ``desk_theme`` field shipped a default, so every
row inserted since then stores "Light" without the user ever having chosen it.
Without this patch the new default fires for nobody who already exists and the
release is a visual no-op on real accounts.

Deliberate choices
------------------
A deliberate "Light" and a default-era "Light" are **not distinguishable**. The
only writer of a user's own choice is ``frappe.core.doctype.user.user.switch_theme``,
which uses ``frappe.db.set_value`` — that writes no Version row, so the database
keeps no record of who chose what. The stored string is the entire evidence, and
it is identical in both cases. This patch therefore flips every stored "Light",
and does not pretend to tell them apart. Stored "Dark" and "Automatic" are left
alone: those can only have come from an explicit choice. The shipped theme
switcher still lets any user pick Light or Automatic again afterwards.

Reversal
--------
The names flipped are written to a JSON record under the site's private files and
pointed at by the ``exe_desk_theme_light_to_dark`` global. To undo:

    bench --site <site> execute \
        frappe.patches.v16_0.default_existing_users_to_dark_desk_theme.revert

``revert()`` restores "Light" only for users it flipped who are *still* on "Dark",
so a choice made after the migration is never clobbered.
"""

import json
import os

import click

import frappe
from frappe.utils import now

REVERSAL_GLOBAL_KEY = "exe_desk_theme_light_to_dark"
RECORD_FILENAME = "desk_theme_light_to_dark.json"

# Guest never sees the desk; flipping it would be noise in the reversal record.
EXCLUDED_USERS = ("Guest",)


def execute():
	if frappe.conf.get("default_desk_theme") == "Light":
		# The site has deliberately pinned the desk to Light. Migrating stored
		# preferences to Dark there would fight the site's own configuration.
		click.secho(
			"desk_theme migration skipped: site_config pins default_desk_theme to Light",
			fg="yellow",
		)
		return

	names = frappe.get_all(
		"User",
		filters={"desk_theme": "Light", "name": ("not in", EXCLUDED_USERS)},
		pluck="name",
		order_by="name",
	)
	if not names:
		return

	# One statement, so the row count does not matter. `modified` is left alone
	# on purpose: a migration flipping a preference should not dirty user records.
	user = frappe.qb.DocType("User")
	(
		frappe.qb.update(user)
		.set(user.desk_theme, "Dark")
		.where((user.desk_theme == "Light") & (user.name.notin(EXCLUDED_USERS)))
	).run()

	record_path = _write_record(names)
	frappe.db.set_global(REVERSAL_GLOBAL_KEY, RECORD_FILENAME)
	frappe.clear_cache()

	click.secho(
		f"desk_theme: {len(names)} user(s) moved from Light to Dark (reversal record: {record_path})",
		fg="green",
	)


def revert():
	"""Restore "Light" for the users this patch flipped and that are still on Dark."""
	record_path = _record_path()
	if not os.path.exists(record_path):
		click.secho(f"no reversal record at {record_path}; nothing to revert", fg="yellow")
		return

	with open(record_path, encoding="utf-8") as f:
		record = json.load(f)

	names = record.get("users") or []
	if not names:
		click.secho("reversal record lists no users; nothing to revert", fg="yellow")
		return

	reverted = frappe.get_all(
		"User",
		filters={"name": ("in", names), "desk_theme": "Dark"},
		pluck="name",
		order_by="name",
	)
	if not reverted:
		click.secho("every flipped user has since chosen a theme; nothing to revert", fg="yellow")
		return

	user = frappe.qb.DocType("User")
	(
		frappe.qb.update(user)
		.set(user.desk_theme, "Light")
		.where((user.desk_theme == "Dark") & (user.name.isin(reverted)))
	).run()
	frappe.db.set_global(REVERSAL_GLOBAL_KEY, None)
	frappe.clear_cache()
	frappe.db.commit()  # nosemgrep - explicit, this is a manually invoked bench command

	click.secho(f"desk_theme: {len(reverted)} user(s) restored to Light", fg="green")


def _record_path() -> str:
	return frappe.get_site_path("private", "files", RECORD_FILENAME)


def _write_record(names: list[str]) -> str:
	path = _record_path()
	os.makedirs(os.path.dirname(path), exist_ok=True)
	payload = {
		"migrated_on": now(),
		"from": "Light",
		"to": "Dark",
		"count": len(names),
		"users": names,
	}
	with open(path, "w", encoding="utf-8") as f:
		json.dump(payload, f, indent=1)
	return path
