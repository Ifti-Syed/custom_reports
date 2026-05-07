# /home/ifti/frappe-bench/apps/custom_reports/custom_reports/custom_reports/report/custom_ar_exposure_summary/custom_ar_exposure_summary.py

import json
from io import BytesIO

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport


def execute(filters=None):
    """
    Custom AR Exposure Summary
    """
    args = {
        "account_type": "Receivable",
        "naming_by": ["Selling Settings", "cust_master_name"],
    }
    columns, data, message, chart, report_summary, skip_total_row = CustomARExposureSummary(filters).run(args)
    return columns, data, message, None, None, skip_total_row


class CustomARExposureSummary(ReceivablePayableReport):

    def set_defaults(self):
        super().set_defaults()
        self.age_as_on = getdate(self.filters.report_date or nowdate())

    def run(self, args):
        columns, data, message, chart, report_summary, skip_total_row = super().run(args)

        summary_rows = self.build_customer_summary(data)
        columns = self.build_required_columns()

        # Add customers who have active OPRs but no submitted Sales Invoices
        existing_customers = {r["customer"] for r in summary_rows if r.get("customer")}
        opr_only = self.get_opr_only_customers(existing_customers)
        if opr_only:
            default_currency = frappe.db.get_value("Company", self.filters.company, "default_currency")
            bucket_count = len(self._get_ageing_labels())
            for customer in opr_only:
                summary_rows.append({
                    "customer": customer,
                    "currency": default_currency,
                    "outstanding": 0.0,
                    **{f"range{i}": 0.0 for i in range(1, bucket_count + 1)},
                })

        self.enrich_customer_rows(summary_rows)

        summary_rows.sort(
            key=lambda r: flt(r.get("total_exposure_after_hold", 0), 2),
            reverse=True,
        )

        return columns, summary_rows, message, None, None, 0

    def prepare_conditions(self):
        super().prepare_conditions()

        if self.filters.get("customer_group"):
            groups = get_customer_group_with_children(self.filters.customer_group)
            customers = frappe.get_all(
                "Customer",
                filters={"customer_group": ["in", groups]},
                pluck="name",
            )
            if customers:
                self.qb_selection_filter.append(self.ple.party.isin(customers))
            else:
                self.qb_selection_filter.append(self.ple.party == "__NO_CUSTOMERS__")

        if self.filters.get("sales_person"):
            lft, rgt = frappe.db.get_value(
                "Sales Person",
                self.filters.sales_person,
                ["lft", "rgt"],
            )
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

    def build_required_columns(self):
        cols = [
            {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 200},
            {"label": _("Sales Person"), "fieldname": "sales_person", "fieldtype": "Data", "width": 170},
            {"label": _("Payment Terms"), "fieldname": "payment_terms", "fieldtype": "Link", "options": "Payment Terms Template", "width": 190},
        ]

        for i, label in enumerate(self._get_ageing_labels(), start=1):
            cols.append(
                {
                    "label": _(label),
                    "fieldname": f"range{i}",
                    "fieldtype": "Currency",
                    "options": "currency",
                    "width": 140,
                }
            )

        cols.extend(
            [
                {"label": _("Total Outstanding"), "fieldname": "outstanding", "fieldtype": "Currency", "options": "currency", "width": 170},
                {"label": _("Future Payment"), "fieldname": "future_payment", "fieldtype": "Currency", "options": "currency", "width": 170},
                {"label": _("Unbilled Sales"), "fieldname": "unbilled_sales", "fieldtype": "Currency", "options": "currency", "width": 170},
                {"label": _("Cheques Required"), "fieldname": "cheques_required", "fieldtype": "Currency", "options": "currency", "width": 180},
                {"label": _("OPRs Under Production"), "fieldname": "oprs_under_production", "fieldtype": "Currency", "options": "currency", "width": 200},
                {"label": _("OPRs On Hold"), "fieldname": "oprs_on_hold", "fieldtype": "Currency", "options": "currency", "width": 170},
                {"label": _("Total Exposure"), "fieldname": "total_exposure", "fieldtype": "Currency", "options": "currency", "width": 180},
                {"label": _("Total Exposure After Hold"), "fieldname": "total_exposure_after_hold", "fieldtype": "Currency", "options": "currency", "width": 230},
                {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1},
            ]
        )

        return cols

    def _get_ageing_labels(self):
        ranges = [int(x.strip()) for x in (self.filters.range or "30, 60, 90, 120").split(",") if x.strip().isdigit()]
        labels, prev = [], 0
        for r in ranges:
            labels.append(f"{prev}-{r}")
            prev = r + 1
        labels.append(f"{prev}+")
        return labels

    def build_customer_summary(self, data):
        if not data:
            return []

        out = {}
        bucket_count = len(self._get_ageing_labels())

        for row in data:
            if not isinstance(row, dict) or not row.get("party"):
                continue

            customer = row["party"]
            d = out.setdefault(customer, {"customer": customer, "currency": row.get("currency"), "outstanding": 0.0})

            d["outstanding"] += flt(row.get("outstanding", 0), 2)

            for i in range(1, bucket_count + 1):
                key = f"range{i}"
                d[key] = flt(d.get(key, 0) + flt(row.get(key, 0), 2), 2)

        return list(out.values())

    def enrich_customer_rows(self, rows):
        if not rows:
            return

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

            outstanding = flt(r.get("outstanding", 0), 2)
            future_payment = flt(future_payment_map.get(customer, 0), 2)
            unbilled_sales = flt(unbilled_map.get(customer, 0), 2)
            opr_under_prod = flt(opr_prod_map.get(customer, 0), 2)
            opr_on_hold = flt(opr_hold_map.get(customer, 0), 2)

            r["future_payment"] = future_payment
            r["unbilled_sales"] = unbilled_sales
            r["oprs_under_production"] = opr_under_prod
            r["oprs_on_hold"] = opr_on_hold

            cheques_required = max(outstanding - future_payment, 0)
            r["cheques_required"] = flt(cheques_required, 2)

            total_exposure = (
                cheques_required
                + unbilled_sales
                + opr_under_prod
            )
            r["total_exposure"] = flt(total_exposure, 2)

            r["total_exposure_after_hold"] = flt(
                total_exposure + opr_on_hold, 2
            )

    def get_sales_person_map(self, customers):
        if not customers:
            return {}

        result = frappe.db.sql(
            """
            SELECT parent, GROUP_CONCAT(sales_person SEPARATOR ', ') as sales_person
            FROM `tabSales Team`
            WHERE parenttype = 'Customer'
              AND parent IN %(customers)s
            GROUP BY parent
            """,
            {"customers": customers},
            as_dict=True,
        )
        return {row.parent: row.sales_person for row in result}

    def get_payment_terms_map(self, customers):
        if not customers:
            return {}

        result = frappe.db.sql(
            """
            SELECT name, payment_terms
            FROM `tabCustomer`
            WHERE name IN %(customers)s
            """,
            {"customers": customers},
            as_dict=True,
        )
        return {row.name: row.payment_terms for row in result}

    def get_future_payment_map(self, customers):
        if not customers:
            return {}

        params = {
            "customers": customers,
            "report_date": self.filters.report_date,
            "company": self.filters.company,
        }

        # Submitted payment entries (existing logic unchanged)
        submitted = frappe.db.sql(
            """
            SELECT party, SUM(paid_amount) as future_amount
            FROM `tabPayment Entry`
            WHERE docstatus = 1
              AND payment_type = 'Receive'
              AND party_type = 'Customer'
              AND party IN %(customers)s
              AND posting_date > %(report_date)s
              AND company = %(company)s
            GROUP BY party
            """,
            params,
            as_dict=True,
        )

        # Draft CDC/PDC entries — only if custom_type column exists on Payment Entry
        draft_cdc_pdc = []
        if frappe.db.has_column("Payment Entry", "custom_type"):
            draft_cdc_pdc = frappe.db.sql(
                """
                SELECT party, SUM(paid_amount) as future_amount
                FROM `tabPayment Entry`
                WHERE docstatus = 0
                  AND payment_type = 'Receive'
                  AND party_type = 'Customer'
                  AND party IN %(customers)s
                  AND posting_date > %(report_date)s
                  AND company = %(company)s
                  AND custom_type IN ('CDC', 'PDC')
                GROUP BY party
                """,
                params,
                as_dict=True,
            )

        future_map = {row.party: flt(row.future_amount, 2) for row in submitted}
        for row in draft_cdc_pdc:
            future_map[row.party] = flt(future_map.get(row.party, 0) + flt(row.future_amount, 2), 2)

        return future_map

    def get_unbilled_sales_map(self, customers):
        if not customers:
            return {}

        result = frappe.db.sql(
            """
            SELECT customer, SUM(grand_total) as unbilled_amount
            FROM `tabDelivery Note`
            WHERE docstatus = 1
              AND status = 'To Bill'
              AND customer IN %(customers)s
              AND company = %(company)s
              AND posting_date <= %(report_date)s
            GROUP BY customer
            """,
            {"customers": customers, "company": self.filters.company, "report_date": self.filters.report_date},
            as_dict=True,
        )
        return {row.customer: row.unbilled_amount for row in result}

    def get_company_vat(self):
        """Returns VAT % from Company.custom_vat_ field, defaulting to 0 if unavailable."""
        try:
            vat = frappe.db.get_value("Company", self.filters.company, "custom_vat_")
            return flt(vat or 0, 4)
        except Exception:
            return 0.0

    def get_opr_data_map(self, customers):
        if not customers:
            return {}, {}

        if not frappe.db.table_exists("Order Processing Request"):
            return {}, {}

        vat_rate = self.get_company_vat()
        vat_multiplier = 1 + vat_rate / 100

        # OPRs in Production, Unbilled, and other active non-Hold stages (VAT-inclusive)
        prod_result = frappe.db.sql(
            """
            SELECT customer_name, SUM(remaining_value) as production_value
            FROM `tabOrder Processing Request`
            WHERE docstatus != 2
              AND remaining_value > 0
              AND customer_name IN %(customers)s
              AND (workflow_state IS NULL OR workflow_state NOT LIKE '%%Hold%%')
            GROUP BY customer_name
            """,
            {"customers": customers},
            as_dict=True,
        )

        # OPRs in Hold stage (VAT-inclusive)
        hold_result = frappe.db.sql(
            """
            SELECT customer_name, SUM(remaining_value) as hold_value
            FROM `tabOrder Processing Request`
            WHERE docstatus != 2
              AND remaining_value > 0
              AND customer_name IN %(customers)s
              AND workflow_state LIKE '%%Hold%%'
            GROUP BY customer_name
            """,
            {"customers": customers},
            as_dict=True,
        )

        production_map = {
            row.customer_name: flt((row.production_value or 0) * vat_multiplier, 2)
            for row in prod_result
        }
        hold_map = {
            row.customer_name: flt((row.hold_value or 0) * vat_multiplier, 2)
            for row in hold_result
        }

        return production_map, hold_map

    def get_opr_only_customers(self, existing_customers):
        """Returns customers with active OPRs who have no outstanding invoices."""
        if not frappe.db.table_exists("Order Processing Request"):
            return []

        conditions = ""
        params = {}

        if self.filters.get("customer_group"):
            groups = get_customer_group_with_children(self.filters.customer_group)
            group_customers = frappe.get_all(
                "Customer",
                filters={"customer_group": ["in", groups]},
                pluck="name",
            )
            if not group_customers:
                return []
            conditions += " AND customer_name IN %(group_customers)s"
            params["group_customers"] = group_customers

        if self.filters.get("sales_person"):
            lft, rgt = frappe.db.get_value(
                "Sales Person",
                self.filters.sales_person,
                ["lft", "rgt"],
            )
            sp_customers = frappe.db.sql_list(
                """
                SELECT DISTINCT st.parent
                FROM `tabSales Team` st
                INNER JOIN `tabSales Person` sp ON sp.name = st.sales_person
                WHERE st.parenttype = 'Customer'
                  AND sp.lft >= %s AND sp.rgt <= %s
                """,
                (lft, rgt),
            )
            if not sp_customers:
                return []
            conditions += " AND customer_name IN %(sp_customers)s"
            params["sp_customers"] = sp_customers

        result = frappe.db.sql(
            f"""
            SELECT DISTINCT customer_name
            FROM `tabOrder Processing Request`
            WHERE docstatus != 2
              AND remaining_value > 0
              {conditions}
            """,
            params,
            as_dict=True,
        )

        return [
            row.customer_name
            for row in result
            if row.customer_name and row.customer_name not in existing_customers
        ]


def get_customer_group_with_children(customer_group):
    if not customer_group:
        return []

    lft, rgt = frappe.db.get_value("Customer Group", customer_group, ["lft", "rgt"])
    return frappe.get_all(
        "Customer Group",
        filters={"lft": [">=", lft], "rgt": ["<=", rgt]},
        pluck="name",
    )


@frappe.whitelist()
def download_excel_report(filters):
    """Generate a formatted Excel export of the AR Exposure Summary report."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    if isinstance(filters, str):
        filters = frappe._dict(json.loads(filters))
    elif isinstance(filters, dict):
        filters = frappe._dict(filters)

    args = {
        "account_type": "Receivable",
        "naming_by": ["Selling Settings", "cust_master_name"],
    }
    report = CustomARExposureSummary(filters)
    columns, data, *_ = report.run(args)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "AR Exposure Summary"

    fieldnames = [col["fieldname"] for col in columns]
    headers = [col["label"] for col in columns]
    currency_fields = {col["fieldname"] for col in columns if col.get("fieldtype") == "Currency"}
    hidden_fields = {col["fieldname"] for col in columns if col.get("hidden")}

    # Filter out hidden columns for export
    visible_indices = [i for i, col in enumerate(columns) if col["fieldname"] not in hidden_fields]
    visible_headers = [headers[i] for i in visible_indices]
    visible_fieldnames = [fieldnames[i] for i in visible_indices]

    header_font = Font(bold=True, size=10)
    header_fill = PatternFill(start_color="BDD7EE", end_color="BDD7EE", fill_type="solid")
    header_align = Alignment(wrap_text=True, horizontal="center", vertical="center")
    right_align = Alignment(horizontal="right", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")

    # Write and style header row
    ws.append(visible_headers)
    ws.row_dimensions[1].height = 40
    for col_idx, cell in enumerate(ws[1], 1):
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align

    # Write data rows
    for row_data in data:
        row_values = []
        for fn in visible_fieldnames:
            val = row_data.get(fn)
            if fn in currency_fields and val is not None:
                row_values.append(flt(val, 2))
            else:
                row_values.append(val if val is not None else "")
        ws.append(row_values)

    # Align data cells and track max column widths
    col_widths = [len(str(h)) + 2 for h in visible_headers]
    for row in ws.iter_rows(min_row=2):
        for col_idx, cell in enumerate(row):
            fn = visible_fieldnames[col_idx] if col_idx < len(visible_fieldnames) else ""
            cell.alignment = right_align if fn in currency_fields else left_align
            if cell.value is not None:
                col_widths[col_idx] = max(col_widths[col_idx], len(str(cell.value)) + 2)

    # Apply auto-adjusted column widths (capped at 40)
    for col_idx, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = min(width, 40)

    # Freeze panes: lock header row and first 2 columns (Customer, Sales Person)
    ws.freeze_panes = "C2"

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    frappe.local.response.filecontent = output.read()
    frappe.local.response.type = "download"
    frappe.local.response.filename = "AR_Exposure_Summary.xlsx"
