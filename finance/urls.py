from django.urls import path

from . import views

app_name = "finance"

urlpatterns = [
    path("accounts/", views.account_list_view, name="account_list"),

    path("journals/", views.journal_list_view, name="journal_list"),
    path("journals/create/", views.journal_create_view, name="journal_create"),
    path("journals/<int:pk>/", views.journal_detail_view, name="journal_detail"),
    path("journals/<int:pk>/post/", views.post_journal_view, name="journal_post"),

    path("receipts/", views.receipt_list_view, name="receipt_list"),
    path("receipts/create/", views.receipt_create_view, name="receipt_create"),
    path("receipts/<int:pk>/", views.receipt_detail_view, name="receipt_detail"),
    path("receipts/<int:pk>/post/", views.receipt_post_view, name="receipt_post"),

    path("payments/", views.payment_list_view, name="payment_list"),
    path("payments/create/", views.payment_create_view, name="payment_create"),
    path("payments/<int:pk>/", views.payment_detail_view, name="payment_detail"),
    path("payments/<int:pk>/post/", views.payment_post_view, name="payment_post"),

    path(
        "reports/general-ledger/",
        views.general_ledger_view,
        name="general_ledger",
    ),
    path(
        "reports/trial-balance/",
        views.trial_balance_view,
        name="trial_balance",
    ),
    path(
        "journals/<int:pk>/reverse/",
        views.reverse_journal_view,
        name="journal_reverse",
    ),
    path(
        "reports/income-statement/",
        views.income_statement_view,
        name="income_statement",
    ),
    path(
        "reports/balance-sheet/",
        views.balance_sheet_view,
        name="balance_sheet",
    ),
    path(
        "reports/cash-flow/",
        views.cash_flow_statement_view,
        name="cash_flow_statement",
    ),
    path(
        "periods/",
        views.fiscal_period_list_view,
        name="fiscal_period_list",
    ),
    path(
        "periods/create-year/",
        views.fiscal_year_create_view,
        name="fiscal_year_create",
    ),
    path(
        "periods/<int:pk>/status/",
        views.fiscal_period_status_view,
        name="fiscal_period_status",
    ),
    path(
        "fiscal-years/<int:pk>/status/",
        views.fiscal_year_status_view,
        name="fiscal_year_status",
    ),
    path(
        "fiscal-years/<int:pk>/history/",
        views.fiscal_year_history_view,
        name="fiscal_year_history",
    ),
    path(
        "periods/<int:pk>/notes/",
        views.fiscal_period_notes_view,
        name="fiscal_period_notes",
    ),
    path(
        "fiscal-years/<int:pk>/notes/",
        views.fiscal_year_notes_view,
        name="fiscal_year_notes",
    ),
    path(
        "reports/customer-statement/",
        views.customer_statement_view,
        name="customer_statement",
    ),
    path(
        "reports/supplier-statement/",
        views.supplier_statement_view,
        name="supplier_statement",
    ),
    path(
        "reports/receivables-aging/",
        views.receivables_aging_view,
        name="receivables_aging",
    ),
    path(
        "reports/payables-aging/",
        views.payables_aging_view,
        name="payables_aging",
    ),
]
