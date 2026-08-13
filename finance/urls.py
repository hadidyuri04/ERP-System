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
]
