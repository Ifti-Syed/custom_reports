// /home/ifti/frappe-bench/apps/custom_reports/custom_reports/custom_reports/report/custom_ar_exposure_summary/custom_ar_exposure_summary.js

console.log("🔥 Custom AR Exposure Summary JS LOADED 🔥 FINAL FIXED");

frappe.query_reports["Custom AR Exposure Summary"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1
		},
		{
			fieldname: "report_date",
			label: __("Report Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1
		},
		{
			fieldname: "range",
			label: __("Ageing Range"),
			fieldtype: "Data",
			default: "30, 60, 90, 120",
			description: __("Comma separated ageing ranges in days (e.g., 30, 60, 90, 120)")
		},
		{
			fieldname: "customer_group",
			label: __("Customer Group"),
			fieldtype: "Link",
			options: "Customer Group"
		},
		{
			fieldname: "sales_person",
			label: __("Sales Person"),
			fieldtype: "Link",
			options: "Sales Person"
		}
	],

	// Freeze columns AFTER "Payment Terms"
	// Customer | Sales Person | Payment Terms
	freeze_columns: 3,

	get_datatable_options(options) {
		return Object.assign(options, {
			layout: "fixed",        // required for freeze
			checkboxColumn: true,   // show row checkbox
			showTotalRow: false,
			cellHeight: 34
			// ❌ headerDropdown REMOVED
		});
	},

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		// Bold required columns
		const bold_fields = [
			"outstanding",
			"cheques_required",
			"total_exposure",
			"total_exposure_after_hold"
		];

		if (bold_fields.includes(column.fieldname)) {
			value = `<b>${value}</b>`;
		}

		// Red for negative currency values
		if (column.fieldtype === "Currency") {
			const n = parseFloat(data[column.fieldname]);
			if (!isNaN(n) && n < 0) {
				value = `<span style="color:red;">${value}</span>`;
			}
		}

		return value;
	},

	onload(report) {
		report.page.add_inner_button(__("Show Summary"), () => {
			show_exposure_summary(report);
		});
	}
};
