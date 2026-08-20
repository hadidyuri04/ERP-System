from django.urls import path

from . import views

app_name = "sales"

urlpatterns = [
    path("", views.invoice_list, name="invoice_list"),
    path("create/", views.invoice_create, name="invoice_create"),
    path("<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("<int:pk>/edit/", views.invoice_update, name="invoice_update"),
    path("<int:pk>/confirm/", views.invoice_confirm, name="invoice_confirm"),
    path("<int:pk>/payment/", views.invoice_payment, name="invoice_payment"),
    path("<int:pk>/cancel/", views.invoice_cancel, name="invoice_cancel"),
    path("<int:pk>/credit-note/", views.credit_note_create, name="credit_note_create"),
]
