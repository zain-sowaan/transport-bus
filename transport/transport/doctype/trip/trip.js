// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Trip", {
	refresh(frm) {
		/*frm.set_query("vehicle", () => ({
			query: "transport.transport.doctype.trip.trip.get_available_vehicles",
			filters: {
				trip_date: frm.doc.trip_date,
				scheduled_time: frm.doc.scheduled_time,
				vehicle_category: frm.doc.route ? frappe.db.get_value("Route", frm.doc.route, "vehicle_category") : null,
			},
		})); */

		frm.set_query("driver", () => ({
			query: "transport.transport.doctype.trip.trip.get_available_drivers",
			filters: {
				trip_date: frm.doc.trip_date,
				scheduled_time: frm.doc.scheduled_time,
			},
		}));

		const is_office = ["Transport In-Charge", "Transport Operations", "System Manager"].some(
			(role) => frappe.user.has_role(role)
		);

		if (!frm.is_new() && frm.doc.status === "Completed" && is_office) {
			frm.add_custom_button(__("Approve"), () => {
				frappe.call({
					method: "transport.transport.operations.approve_trip",
					args: { trip_name: frm.doc.name },
					callback: () => frm.reload_doc(),
				});
			});
		}

		// Starting from the desk is for a driver who is not using the portal -
		// one who phones in, or who has no portal account. The driver's own
		// start asks for a vehicle photo and checks their position against the
		// pickup point; neither exists here, so the odometer is all this can
		// ask for and it stays optional.
		// Ending from the desk, same trade as starting: the driver's own end asks
		// for a photo of the drop location, which nobody at a desk has. Unlike
		// the Server Script this replaces, the app method also recalculates
		// duration and Duty/OT from the actual times - without that the trip
		// stays costed on its planned hours.
		if (!frm.is_new() && frm.doc.status === "Started" && is_office) {
			frm.add_custom_button(__("End Trip"), () => {
				frappe.prompt(
					[
						{
							fieldname: "end_odometer",
							label: __("End odometer (km)"),
							fieldtype: "Float",
							description: frm.doc.start_odometer
								? __("Started at {0} km", [frm.doc.start_odometer])
								: "",
						},
						{
							fieldname: "driver_remarks",
							label: __("Remarks"),
							fieldtype: "Small Text",
						},
					],
					(values) => {
						frappe.call({
							method: "transport.transport.operations.end_trip",
							args: {
								trip_name: frm.doc.name,
								end_odometer: values.end_odometer || null,
								driver_remarks: values.driver_remarks || null,
							},
							freeze: true,
							callback: () => frm.reload_doc(),
						});
					},
					__("End this trip"),
					__("End")
				);
			});
		}

		if (!frm.is_new() && ["Assigned", "Accepted"].includes(frm.doc.status) && is_office) {
			frm.add_custom_button(__("Start Trip"), () => {
				frappe.prompt(
					{
						fieldname: "start_odometer",
						label: __("Start odometer (km)"),
						fieldtype: "Float",
						reqd: 0,
					},
					(values) => {
						frappe.call({
							method: "transport.transport.operations.start_trip",
							args: {
								trip_name: frm.doc.name,
								start_odometer: values.start_odometer || null,
							},
							freeze: true,
							callback: () => frm.reload_doc(),
						});
					},
					__("Start this trip"),
					__("Start")
				);
			});
		}
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
