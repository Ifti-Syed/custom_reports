# /home/ifti/frappe-bench/apps/custom_reports/custom_reports/custom_reports/report/custom_ar_exposure_summary/custom_ar_exposure_summary.py

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport


def execute(filters=None):
	"""
	Custom AR Exposure Summary
	Required columns:
	Customer, Sales Person, Payment Terms, Ageing, Total Outstanding, Future Payment,
	Unbilled Sales, Cheques Required, OPRs Under Production, OPRs On Hold,
	Total Exposure, Total Exposure After Hold

	- Uses ERPNext ReceivablePayableReport for PLE + ageing accuracy
	- Adds filters: Customer Group, Sales Person
	- No chart, no report summary
	"""
	args = {
		"account_type": "Receivable",
		"naming_by": ["Selling Settings", "cust_master_name"],
	}
	columns, data, message, chart, report_summary, skip_total_row = CustomARExposureSummary(filters).run(args)
	return columns, data, message, None, None, skip_total_row


class CustomARExposureSummary(ReceivablePayableReport):
	"""
	Extension of ERPNext ReceivablePayableReport:
	- Keep ERPNext ageing logic
	- Collapse to customer-level summary
	- Add exposure columns
	"""

	def set_defaults(self):
		super().set_defaults()
		# Force ageing calculation as of report date (standard for AR snapshot)
		self.age_as_on = getdate(self.filters.report_date or nowdate())

	def run(self, args):
		# Run standard report pipeline first
		columns, data, message, chart, report_summary, skip_total_row = super().run(args)

		# Build customer-level summary from standard invoice-level rows
		summary_rows = self.build_customer_summary(data)

		# Build columns strictly in required order
		columns = self.build_required_columns()

		# Add exposure + other fields
		self.enrich_customer_rows(summary_rows)

		# Sort by Total Exposure After Hold desc
		summary_rows.sort(key=lambda r: flt(r.get("total_exposure_after_hold", 0), 2), reverse=True)

		return columns, summary_rows, message, None, None, 0

	# ---------------------------------------------------------------------
	# Filters (hook into base query)
	# ---------------------------------------------------------------------

	def prepare_conditions(self):
		# Let ERPNext build all standard conditions first
		super().prepare_conditions()

		# Customer Group filter (tree-aware)
		if self.filters.get("customer_group"):
			groups = get_customer_group_with_children(self.filters.customer_group)
			customers = frappe.get_all(
				"Customer",
				filters={"customer_group": ["in", groups]},
				pluck="name",
			)
			# If none, return empty result early by adding impossible condition
			if customers:
				self.qb_selection_filter.append(self.ple.party.isin(customers))
			else:
				self.qb_selection_filter.append(self.ple.party == "__NO_CUSTOMERS__")

		# Sales Person filter (tree-aware sales person)
		if self.filters.get("sales_person"):
			lft, rgt = frappe.db.get_value("Sales Person", self.filters.sales_person, ["lft", "rgt"])
			assigned_customers = frappe.db.sql_list(
				"""
				SELECT DISTINCT st.parent
				FROM `tabSales Team` st
				INNER JOIN `tabSales Person` sp ON sp.name = st.sales_person
				WHERE st.parenttype = 'Customer'
				  AND sp.lft >= %s AND sp.rgt <= %s
				""",
				(lft, rgt),
			)
			if assigned_customers:
				self.qb_selection_filter.append(self.ple.party.isin(assigned_customers))
			else:
				self.qb_selection_filter.append(self.ple.party == "__NO_CUSTOMERS__")

	# ---------------------------------------------------------------------
	# Column layout
	# ---------------------------------------------------------------------

	def build_required_columns(self):
		cols = [
			{
				"label": _("Customer"),
				"fieldname": "customer",
				"fieldtype": "Link",
				"options": "Customer",
				"width": 200,
			},
			{
				"label": _("Sales Person"),
				"fieldname": "sales_person",
				"fieldtype": "Data",
				"width": 170,
			},
			{
				"label": _("Payment Terms"),
				"fieldname": "payment_terms",
				"fieldtype": "Link",
				"options": "Payment Terms Template",
				"width": 190,
			},
		]

		# Ageing columns (dynamic labels based on filter range)
		age_labels = self._get_ageing_labels()
		for i, label in enumerate(age_labels, start=1):
			cols.append(
				{
					"label": _(label),
					"fieldname": f"range{i}",
					"fieldtype": "Currency",
					"options": "currency",
					"width": 140,
				}
			)

		# Required remaining columns (order locked)
		cols.extend(
			[
				{
					"label": _("Total Outstanding"),
					"fieldname": "outstanding",
					"fieldtype": "Currency",
					"options": "currency",
					"width": 170,
				},
				{
					"label": _("Future Payment"),
					"fieldname": "future_payment",
					"fieldtype": "Currency",
					"options": "currency",
					"width": 170,
				},
				{
					"label": _("Unbilled Sales"),
					"fieldname": "unbilled_sales",
					"fieldtype": "Currency",
					"options": "currency",
					"width": 170,
				},
				{
					"label": _("Cheques Required"),
					"fieldname": "cheques_required",
					"fieldtype": "Currency",
					"options": "currency",
					"width": 180,
				},
				{
					"label": _("OPRs Under Production"),
					"fieldname": "oprs_under_production",
					"fieldtype": "Currency",
					"options": "currency",
					"width": 200,
				},
				{
					"label": _("OPRs On Hold"),
					"fieldname": "oprs_on_hold",
					"fieldtype": "Currency",
					"options": "currency",
					"width": 170,
				},
				{
					"label": _("Total Exposure"),
					"fieldname": "total_exposure",
					"fieldtype": "Currency",
					"options": "currency",
					"width": 180,
				},
				{
					"label": _("Total Exposure After Hold"),
					"fieldname": "total_exposure_after_hold",
					"fieldtype": "Currency",
					"options": "currency",
					"width": 230,
				},
				{
					"label": _("Currency"),
					"fieldname": "currency",
					"fieldtype": "Link",
					"options": "Currency",
					"width": 90,
					"hidden": 1,
				},
			]
		)

		return cols

	def _get_ageing_labels(self):
		# ranges from base report
		ranges = [num.strip() for num in (self.filters.range or "30, 60, 90, 120").split(",") if num.strip().isdigit()]
		ranges = [int(x) for x in ranges]
		labels = []
		prev = 0
		for r in ranges:
			labels.append(f"{prev}-{r}")
			prev = r + 1
		labels.append(f"{prev}+")
		return labels

	# ---------------------------------------------------------------------
	# Data shaping
	# ---------------------------------------------------------------------

	def build_customer_summary(self, data):
		"""
		Base report returns rows per voucher/payment-term.
		We aggregate to customer-level:
		- Sum range buckets
		- Sum outstanding
		- Keep currency
		"""
		if not data:
			return []

		age_bucket_count = len(self._get_ageing_labels())
		out = {}

		for row in data:
			# Skip subtotal/blank rows from base report
			if not isinstance(row, dict) or not row.get("party"):
				continue
			customer = row.get("party")
			currency = row.get("currency") or self.company_currency

			d = out.setdefault(
				customer,
				{
					"customer": customer,
					"sales_person": "",
					"payment_terms": "",
					"outstanding": 0.0,
					"future_payment": 0.0,
					"unbilled_sales": 0.0,
					"cheques_required": 0.0,
					"oprs_under_production": 0.0,
					"oprs_on_hold": 0.0,
					"total_exposure": 0.0,
					"total_exposure_after_hold": 0.0,
					"currency": currency,
				},
			)

			# Sum outstanding
			d["outstanding"] += flt(row.get("outstanding", 0), 2)

			# Sum ageing buckets from base report (already allocated per row)
			for i in range(1, age_bucket_count + 1):
				key = f"range{i}"
				d.setdefault(key, 0.0)
				d[key] += flt(row.get(key, 0), 2)

		# Round to 2 decimals
		for customer, d in out.items():
			d["outstanding"] = flt(d["outstanding"], 2)
			for i in range(1, age_bucket_count + 1):
				d[f"range{i}"] = flt(d.get(f"range{i}", 0), 2)

		return list(out.values())

	def enrich_customer_rows(self, rows):
		if not rows:
			return

		# Prepare maps
		customers = [r["customer"] for r in rows if r.get("customer")]

		sales_person_map = self.get_sales_person_map(customers)
		payment_terms_map = self.get_payment_terms_map(customers)
		future_payment_map = self.get_future_payment_map(customers)
		unbilled_map = self.get_unbilled_sales_map(customers)
		opr_prod_map, opr_hold_map = self.get_opr_data_map(customers)

		for r in rows:
			customer = r.get("customer")
			if not customer:
				continue

			r["sales_person"] = sales_person_map.get(customer, "")
			r["payment_terms"] = payment_terms_map.get(customer, "")

			r["future_payment"] = flt(future_payment_map.get(customer, 0), 2)
			r["unbilled_sales"] = flt(unbilled_map.get(customer, 0), 2)

			# Cheques required = Outstanding - Future Payment (never negative)
			r["cheques_required"] = flt(max(flt(r.get("outstanding", 0), 2) - flt(r.get("future_payment", 0), 2), 0), 2)

			r["oprs_under_production"] = flt(opr_prod_map.get(customer, 0), 2)
			r["oprs_on_hold"] = flt(opr_hold_map.get(customer, 0), 2)

			# Total Exposure = Outstanding + Unbilled Sales + Cheques Required + OPR Under Production
			# (per your spec; note: cheques_required already derived from outstanding)
			r["total_exposure"] = flt(
				flt(r.get("outstanding", 0), 2)
				+ flt(r.get("unbilled_sales", 0), 2)
				+ flt(r.get("cheques_required", 0), 2)
				+ flt(r.get("oprs_under_production", 0), 2),
				2,
			)

			r["total_exposure_after_hold"] = flt(r["total_exposure"] + flt(r.get("oprs_on_hold", 0), 2), 2)

	# ---------------------------------------------------------------------
	# Maps
	# ---------------------------------------------------------------------

	def get_sales_person_map(self, customers):
		if not customers:
			return {}
		try:
			# Get all sales persons per customer, keep comma-separated
			rows = frappe.db.sql(
				"""
				SELECT st.parent AS customer, st.sales_person
				FROM `tabSales Team` st
				WHERE st.parenttype = 'Customer'
				  AND st.parent IN %(customers)s
				ORDER BY st.parent, st.idx
				""",
				{"customers": tuple(customers)},
				as_dict=True,
			)
			m = {}
			for r in rows:
				m.setdefault(r.customer, []).append(r.sales_person)
			return {k: ", ".join(v) for k, v in m.items()}
		except Exception:
			frappe.log_error(title="AR Exposure: Sales Person Map Error", message=frappe.get_traceback())
			return {}

	def get_payment_terms_map(self, customers):
		if not customers:
			return {}
		try:
			rows = frappe.get_all(
				"Customer",
				filters={"name": ["in", customers]},
				fields=["name", "payment_terms"],
			)
			return {r.name: (r.payment_terms or "") for r in rows}
		except Exception:
			frappe.log_error(title="AR Exposure: Payment Terms Map Error", message=frappe.get_traceback())
			return {}

	def get_future_payment_map(self, customers):
		"""
	Future Payment = sum of Payment Entry Receive posted AFTER report_date (unallocated_amount) per customer.
	"""
		if not customers or not self.filters.get("company"):
			return {}
		try:
			report_date = getdate(self.filters.get("report_date") or nowdate())
			rows = frappe.db.sql(
				"""
				SELECT pe.party AS customer, SUM(pe.unallocated_amount) AS amount
				FROM `tabPayment Entry` pe
				WHERE pe.docstatus = 1
				  AND pe.payment_type = 'Receive'
				  AND pe.party_type = 'Customer'
				  AND pe.party IN %(customers)s
				  AND pe.company = %(company)s
				  AND pe.unallocated_amount > 0
				  AND pe.posting_date > %(report_date)s
				GROUP BY pe.party
				""",
				{"customers": tuple(customers), "company": self.filters.company, "report_date": report_date},
				as_dict=True,
			)
			return {r.customer: r.amount for r in rows}
		except Exception:
			frappe.log_error(title="AR Exposure: Future Payment Map Error", message=frappe.get_traceback())
			return {}

	def get_unbilled_sales_map(self, customers):
		if not customers or not self.filters.get("company"):
			return {}
		try:
			report_date = getdate(self.filters.get("report_date") or nowdate())
			rows = frappe.db.sql(
				"""
				SELECT dn.customer, SUM(dn.grand_total - (dn.per_billed * dn.grand_total / 100)) AS amount
				FROM `tabDelivery Note` dn
				WHERE dn.docstatus = 1
				  AND dn.customer IN %(customers)s
				  AND dn.company = %(company)s
				  AND dn.per_billed < 100
				  AND dn.posting_date <= %(report_date)s
				GROUP BY dn.customer
				""",
				{"customers": tuple(customers), "company": self.filters.company, "report_date": report_date},
				as_dict=True,
			)
			return {r.customer: r.amount for r in rows}
		except Exception:
			frappe.log_error(title="AR Exposure: Unbilled Sales Map Error", message=frappe.get_traceback())
			return {}

	def get_opr_data_map(self, customers):
		if not customers or not frappe.db.table_exists("Order Processing Request"):
			return {}, {}
		try:
			prod = frappe.db.sql(
				"""
				SELECT opr.customer_name AS customer, SUM(opr.remaining_value) AS amount
				FROM `tabOrder Processing Request` opr
				WHERE opr.docstatus != 2
				  AND opr.remaining_value > 0
				  AND opr.customer_name IN %(customers)s
				  AND (opr.workflow_state IS NULL OR opr.workflow_state NOT LIKE %(hold)s)
				GROUP BY opr.customer_name
				""",
				{"customers": tuple(customers), "hold": "%Hold%"},
				as_dict=True,
			)
			hold = frappe.db.sql(
				"""
				SELECT opr.customer_name AS customer, SUM(opr.remaining_value) AS amount
				FROM `tabOrder Processing Request` opr
				WHERE opr.docstatus != 2
				  AND opr.remaining_value > 0
				  AND opr.customer_name IN %(customers)s
				  AND opr.workflow_state LIKE %(hold)s
				GROUP BY opr.customer_name
				""",
				{"customers": tuple(customers), "hold": "%Hold%"},
				as_dict=True,
			)
			return {r.customer: r.amount for r in prod}, {r.customer: r.amount for r in hold}
		except Exception:
			frappe.log_error(title="AR Exposure: OPR Map Error", message=frappe.get_traceback())
			return {}, {}


def get_customer_group_with_children(customer_group):
	"""
	Tree-aware customer group expansion (copied logic style from ERPNext)
	"""
	if not customer_group:
		return []
	if not frappe.db.exists("Customer Group", customer_group):
		frappe.throw(_("Customer Group: {0} does not exist").format(customer_group))

	lft, rgt = frappe.db.get_value("Customer Group", customer_group, ["lft", "rgt"])
	children = frappe.get_all(
		"Customer Group",
		filters={"lft": [">=", lft], "rgt": ["<=", rgt]},
		pluck="name",
	)
	return list(set(children))
