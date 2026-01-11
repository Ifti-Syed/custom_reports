import frappe
from frappe import _
from frappe.utils import getdate, nowdate


def execute(filters=None):
    filters = filters or {}
    company = filters.get("company")
    report_date = getdate(filters.get("report_date") or nowdate())

    if not company:
        # Keep it strict so you don't get silent empty reports
        return [], []

    # Check if OPR table exists (so report won't crash on sites without it)
    opr_table_exists = frappe.db.table_exists("Order Processing Request")

    columns = [
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 240},
        {"label": _("Sales Person"), "fieldname": "sales_person", "fieldtype": "Data", "width": 160},
        {"label": _("Payment Terms"), "fieldname": "payment_terms", "fieldtype": "Data", "width": 180},

        {"label": _("0-30"), "fieldname": "age_0_30", "fieldtype": "Currency", "width": 140},
        {"label": _("31-60"), "fieldname": "age_31_60", "fieldtype": "Currency", "width": 140},
        {"label": _("61-90"), "fieldname": "age_61_90", "fieldtype": "Currency", "width": 140},
        {"label": _("91-120"), "fieldname": "age_91_120", "fieldtype": "Currency", "width": 140},
        {"label": _("121+"), "fieldname": "age_120_above", "fieldtype": "Currency", "width": 140},

        {"label": _("Total Outstanding"), "fieldname": "total_outstanding", "fieldtype": "Currency", "width": 180},
        {"label": _("Unbilled Sales"), "fieldname": "unbilled_sales", "fieldtype": "Currency", "width": 160},
        {"label": _("Future Payment"), "fieldname": "future_payment", "fieldtype": "Currency", "width": 160},
        {"label": _("Cheques Required"), "fieldname": "cheques_required", "fieldtype": "Currency", "width": 180},

        {"label": _("OPRs Under Production"), "fieldname": "oprs_under_production", "fieldtype": "Currency", "width": 190},
        {"label": _("OPRs On Hold"), "fieldname": "oprs_on_hold", "fieldtype": "Currency", "width": 160},

        {"label": _("Total Exposure"), "fieldname": "total_exposure", "fieldtype": "Currency", "width": 180},
        {"label": _("Total Exposure After Hold"), "fieldname": "total_exposure_after_hold", "fieldtype": "Currency", "width": 220},
    ]

    opr_cte = ""
    opr_join = ""
    if opr_table_exists:
        opr_cte = """
        , opr_prod AS (
            SELECT
                customer_name AS customer,
                SUM(remaining_value) AS oprs_under_production
            FROM `tabOrder Processing Request`
            WHERE docstatus != 2
              AND remaining_value > 0
              AND workflow_state IN (
                  'Approved For Production',
                  'Received By Production',
                  'Processed By Production',
                  'Production Completed',
                  'Delivery Completed'
              )
            GROUP BY customer_name
        ),
        opr_hold AS (
            SELECT
                customer_name AS customer,
                SUM(remaining_value) AS oprs_on_hold
            FROM `tabOrder Processing Request`
            WHERE docstatus != 2
              AND remaining_value > 0
              AND LOWER(workflow_state) LIKE '%%hold%%'
            GROUP BY customer_name
        )
        """
        opr_join = """
        LEFT JOIN opr_prod op ON op.customer = c.name
        LEFT JOIN opr_hold oh ON oh.customer = c.name
        """
    else:
        # If OPR doctype doesn't exist, treat as 0
        opr_join = ""

    query = f"""
    WITH
    ple AS (
        SELECT
            party AS customer,
            IFNULL(due_date, posting_date) AS due_date,
            amount
        FROM `tabPayment Ledger Entry`
        WHERE docstatus = 1
          AND party_type = 'Customer'
          AND company = %(company)s
          AND posting_date <= %(report_date)s
    ),
    aged AS (
        SELECT
            customer,
            SUM(CASE WHEN DATEDIFF(%(report_date)s, due_date) BETWEEN 0 AND 30 THEN amount ELSE 0 END) AS age_0_30,
            SUM(CASE WHEN DATEDIFF(%(report_date)s, due_date) BETWEEN 31 AND 60 THEN amount ELSE 0 END) AS age_31_60,
            SUM(CASE WHEN DATEDIFF(%(report_date)s, due_date) BETWEEN 61 AND 90 THEN amount ELSE 0 END) AS age_61_90,
            SUM(CASE WHEN DATEDIFF(%(report_date)s, due_date) BETWEEN 91 AND 120 THEN amount ELSE 0 END) AS age_91_120,
            SUM(CASE WHEN DATEDIFF(%(report_date)s, due_date) > 120 THEN amount ELSE 0 END) AS age_120_above
        FROM ple
        GROUP BY customer
    ),
    dn AS (
        SELECT
            customer,
            SUM(grand_total) AS unbilled_sales
        FROM `tabDelivery Note`
        WHERE docstatus = 1
          AND status = 'To Bill'
          AND company = %(company)s
          AND posting_date <= %(report_date)s
        GROUP BY customer
    ),
    future_pay AS (
        SELECT
            party AS customer,
            SUM(paid_amount) AS future_payment
        FROM `tabPayment Entry`
        WHERE docstatus = 1
          AND payment_type = 'Receive'
          AND company = %(company)s
          AND posting_date > %(report_date)s
        GROUP BY party
    )
    {opr_cte}
    SELECT
        c.name AS customer,
        c.default_sales_partner AS sales_person,
        c.payment_terms AS payment_terms,

        ROUND(COALESCE(a.age_0_30,0), 2) AS age_0_30,
        ROUND(COALESCE(a.age_31_60,0), 2) AS age_31_60,
        ROUND(COALESCE(a.age_61_90,0), 2) AS age_61_90,
        ROUND(COALESCE(a.age_91_120,0), 2) AS age_91_120,
        ROUND(COALESCE(a.age_120_above,0), 2) AS age_120_above,

        ROUND(
            COALESCE(a.age_0_30,0)
          + COALESCE(a.age_31_60,0)
          + COALESCE(a.age_61_90,0)
          + COALESCE(a.age_91_120,0)
          + COALESCE(a.age_120_above,0)
        , 2) AS total_outstanding,

        ROUND(COALESCE(dn.unbilled_sales,0), 2) AS unbilled_sales,
        ROUND(COALESCE(fp.future_payment,0), 2) AS future_payment,

        -- Cheques Required = Outstanding + Unbilled - Future Payments
        ROUND(
            (
              COALESCE(a.age_0_30,0)
            + COALESCE(a.age_31_60,0)
            + COALESCE(a.age_61_90,0)
            + COALESCE(a.age_91_120,0)
            + COALESCE(a.age_120_above,0)
            + COALESCE(dn.unbilled_sales,0)
            ) - COALESCE(fp.future_payment,0)
        , 2) AS cheques_required,

        ROUND({ "COALESCE(op.oprs_under_production,0)" if opr_table_exists else "0" }, 2) AS oprs_under_production,
        ROUND({ "COALESCE(oh.oprs_on_hold,0)" if opr_table_exists else "0" }, 2) AS oprs_on_hold,

        -- Total Exposure = Outstanding + Unbilled + OPR(Prod) - Future Payments
        ROUND(
            (
              COALESCE(a.age_0_30,0)
            + COALESCE(a.age_31_60,0)
            + COALESCE(a.age_61_90,0)
            + COALESCE(a.age_91_120,0)
            + COALESCE(a.age_120_above,0)
            + COALESCE(dn.unbilled_sales,0)
            + { "COALESCE(op.oprs_under_production,0)" if opr_table_exists else "0" }
            ) - COALESCE(fp.future_payment,0)
        , 2) AS total_exposure,

        -- Total Exposure After Hold = Outstanding + Unbilled + OPR(Prod) + OPR(Hold) - Future Payments
        ROUND(
            (
              COALESCE(a.age_0_30,0)
            + COALESCE(a.age_31_60,0)
            + COALESCE(a.age_61_90,0)
            + COALESCE(a.age_91_120,0)
            + COALESCE(a.age_120_above,0)
            + COALESCE(dn.unbilled_sales,0)
            + { "COALESCE(op.oprs_under_production,0)" if opr_table_exists else "0" }
            + { "COALESCE(oh.oprs_on_hold,0)" if opr_table_exists else "0" }
            ) - COALESCE(fp.future_payment,0)
        , 2) AS total_exposure_after_hold

    FROM `tabCustomer` c
    LEFT JOIN aged a ON a.customer = c.name
    LEFT JOIN dn ON dn.customer = c.name
    LEFT JOIN future_pay fp ON fp.customer = c.name
    {opr_join}
    WHERE
        -- show customers with ANY exposure / movement (not only outstanding)
        (
            COALESCE(a.age_0_30,0)
          + COALESCE(a.age_31_60,0)
          + COALESCE(a.age_61_90,0)
          + COALESCE(a.age_91_120,0)
          + COALESCE(a.age_120_above,0)
          + COALESCE(dn.unbilled_sales,0)
          + COALESCE(fp.future_payment,0)
          {"+ COALESCE(op.oprs_under_production,0) + COALESCE(oh.oprs_on_hold,0)" if opr_table_exists else ""}
        ) != 0
    ORDER BY c.name
    """

    data = frappe.db.sql(
        query,
        {"company": company, "report_date": report_date},
        as_dict=True,
    )

    return columns, data
