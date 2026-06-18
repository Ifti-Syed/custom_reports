// /home/ifti/frappe-bench/apps/custom_reports/custom_reports/custom_reports/report/custom_ar_exposure_summary/custom_ar_exposure_summary.js

console.log("🔥 Custom AR Exposure Summary JS LOADED 🔥 0 ");

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
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer"
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

	get_datatable_options(options) {
		options.checkboxColumn = true;
		return options;
	},

	after_datatable_render(datatable) {
		if (!datatable || datatable._freeze_applied) return;
		datatable._freeze_applied = true;

		const wrapper = datatable.wrapper;
		const bodyScrollable = datatable.bodyScrollable || wrapper.querySelector('.dt-scrollable');
		const header = datatable.header || wrapper.querySelector('.dt-header');
		if (!bodyScrollable || !header) return;

		// serialNoColumn=true (default) → col 0
		// checkboxColumn=true → col 1
		// User columns: Customer=2, CustomerGroup=3, SalesPerson=4
		// Freeze cols 0-4 (serial + checkbox + 3 data cols)
		const frozenIndices = [0, 1, 2, 3, 4];

		// Calculate cumulative left offsets from actual header cell widths
		const offsets = {};
		let cumLeft = 0;
		frozenIndices.forEach(i => {
			offsets[i] = cumLeft;
			const cell = header.querySelector(`.dt-cell--col-${i}`);
			cumLeft += cell ? cell.offsetWidth : 0;
		});

		// Inject CSS to make body cells sticky within the scrollable container
		const styleId = 'dt-ar-freeze-style';
		const existing = document.getElementById(styleId);
		if (existing) existing.remove();
		const style = document.createElement('style');
		style.id = styleId;
		style.textContent = frozenIndices.map(i =>
			`.dt-cell--col-${i} { position: sticky !important; left: ${offsets[i]}px !important; z-index: 2 !important; background: #fff !important; }`
		).join('\n');
		document.head.appendChild(style);

		// Counter-scroll frozen header cells: the header moves via translateX(-scrollLeft),
		// so we apply translateX(+scrollLeft) on frozen header cells to keep them in place.
		bodyScrollable.addEventListener('scroll', function () {
			const scrollLeft = this.scrollLeft;
			frozenIndices.forEach(i => {
				header.querySelectorAll(`.dt-cell--col-${i}`).forEach(cell => {
					cell.style.transform = `translateX(${scrollLeft}px)`;
					cell.style.zIndex = '3';
					cell.style.position = 'relative';
				});
			});
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

		report.page.add_inner_button(__("Export Excel"), () => {
			const filters = report.get_values();
			if (!filters || !filters.company || !filters.report_date) {
				frappe.msgprint(__("Please set Company and Report Date before exporting."));
				return;
			}
			window.location = frappe.urllib.get_full_url(
				"/api/method/custom_reports.custom_reports.report.custom_ar_exposure_summary.custom_ar_exposure_summary.download_excel_report"
				+ "?filters=" + encodeURIComponent(JSON.stringify(filters))
			);
		});
	}
};

function show_exposure_summary(report) {
	const data = (report.data || []).filter(r => r && r.customer);
	if (!data.length) {
		frappe.msgprint(__("No data to summarize"));
		return;
	}

	const sum = (field) => data.reduce((a, r) => a + (parseFloat(r[field]) || 0), 0);

	const currency = data[0].currency || "";
	const fmt = (val) => format_currency(val, currency, 2);

	frappe.msgprint({
		title: __("AR Exposure Summary"),
		wide: true,
		message: `
			<table class="table table-bordered">
				<tr><td><b>${__("Total Customers")}</b></td><td class="text-right">${data.length}</td></tr>
				<tr><td><b>${__("Total Outstanding")}</b></td><td class="text-right">${fmt(sum("outstanding"))}</td></tr>
				<tr><td><b>${__("Future Payment")}</b></td><td class="text-right">${fmt(sum("future_payment"))}</td></tr>
				<tr><td><b>${__("Unbilled Sales")}</b></td><td class="text-right">${fmt(sum("unbilled_sales"))}</td></tr>
				<tr><td><b>${__("Cheques Required")}</b></td><td class="text-right">${fmt(sum("cheques_required"))}</td></tr>
				<tr><td><b>${__("OPRs Under Production")}</b></td><td class="text-right">${fmt(sum("oprs_under_production"))}</td></tr>
				<tr><td><b>${__("OPRs On Hold")}</b></td><td class="text-right">${fmt(sum("oprs_on_hold"))}</td></tr>
				<tr class="bg-light font-weight-bold">
					<td>${__("Total Exposure")}</td>
					<td class="text-right">${fmt(sum("total_exposure"))}</td>
				</tr>
				<tr class="bg-info font-weight-bold">
					<td>${__("Total Exposure After Hold")}</td>
					<td class="text-right">${fmt(sum("total_exposure_after_hold"))}</td>
				</tr>
			</table>
		`
	});
}
