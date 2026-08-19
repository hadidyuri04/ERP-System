from django.urls import path
from . import views

app_name = "pos"

urlpatterns = [
    path("", views.pos_terminal, name="terminal"),
    path("api/search-product/", views.search_product, name="search_product"),
    path("api/validate-discount/", views.validate_discount, name="validate_discount"),
    path("sales/", views.sale_list, name="sale_list"),
    path("sales/<int:pk>/", views.sale_detail, name="sale_detail"),
    path("complete/", views.complete_sale_view, name="complete_sale"),
    
    # Session Management Routes
    path("sessions/", views.session_list, name="session_list"),
    path("sessions/open/", views.open_session, name="open_session"),
    path("sessions/<int:pk>/close/", views.close_session, name="close_session"),
    path("sessions/<int:pk>/cash-transaction/", views.cash_transaction, name="cash_transaction"),

    path("hold/", views.hold_sale_view, name="hold_sale"),
    path("held-sales/", views.list_held_sales_view, name="list_held_sales"),
    path("held-sales/<int:pk>/recall/", views.recall_held_sale_view, name="recall_held_sale"),
    path("held-sales/<int:pk>/cancel/", views.cancel_held_sale_view, name="cancel_held_sale"),

    # Discount Code Management Routes
    path("discounts/", views.discount_code_list, name="discount_code_list"),
    path("discounts/<int:pk>/toggle/", views.toggle_discount_code_status, name="toggle_discount_code"),
]