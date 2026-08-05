// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Trip", {
	refresh(frm) {
		frm.set_query("vehicle", () => ({
			query: "transport.transport.doctype.trip.trip.get_available_vehicles",
			filters: {
				trip_date: frm.doc.trip_date,
				scheduled_time: frm.doc.scheduled_time,
				vehicle_category: frm.doc.route ? frappe.db.get_value("Route", frm.doc.route, "vehicle_category") : null,
			},
		}));

		frm.set_query("driver", () => ({
			query: "transport.transport.doctype.trip.trip.get_available_drivers",
			filters: {
				trip_date: frm.doc.trip_date,
				scheduled_time: frm.doc.scheduled_time,
			},
		}));
	},

	route(frm) {
		if (!frm.doc.route) {
			return;
		}
		frappe.db.get_doc("Route", frm.doc.route).then((route) => {
			if (frm.doc.direction === "Drop") {
				frm.set_value("from_place", route.to_place);
				frm.set_value("to_place", route.from_place);
				frm.set_value("scheduled_time", frm.doc.scheduled_time || route.drop_time);
			} else {
				frm.set_value("from_place", route.from_place);
				frm.set_value("to_place", route.to_place);
				frm.set_value("scheduled_time", frm.doc.scheduled_time || route.pickup_time);
			}
			if (!frm.doc.project) {
				frm.set_value("project", route.project);
			}
		});
	},
});
