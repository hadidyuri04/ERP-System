from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from finance.models import FiscalYear
from inventory.models import Warehouse
from pos.models import POSSession


class ConnectedViewsSmokeTests(TestCase):
    """Keep the main navigation and create pages renderable as they evolve."""

    route_names = (
        "core:admin_dashboard",
        "core:accountant_dashboard",
        "core:home_dashboard",
        "core:settings",
        "customers:list",
        "customers:create",
        "suppliers:list",
        "suppliers:create",
        "inventory:product_list",
        "inventory:product_create",
        "inventory:warehouse_list",
        "inventory:waste_list",
        "inventory:waste_create",
        "purchasing:list",
        "purchasing:create",
        "finance:account_list",
        "finance:journal_list",
        "finance:receipt_list",
        "finance:receipt_create",
        "finance:payment_list",
        "finance:payment_create",
        "finance:general_ledger",
        "finance:trial_balance",
        "finance:income_statement",
        "finance:balance_sheet",
        "finance:cash_flow_statement",
        "finance:receivables_aging",
        "finance:payables_aging",
        "finance:fiscal_period_list",
        "pos:terminal",
        "pos:sale_list",
        "quotations:list",
        "quotations:create",
    )

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="view-admin",
            password="test-password",
            email="admin@example.com",
        )
        self.client.force_login(self.user)
        warehouse = Warehouse.objects.create(
            code="SMOKE-WH",
            name="Smoke test warehouse",
        )
        POSSession.objects.create(
            session_number="SMOKE-SESSION",
            cashier=self.user,
            warehouse=warehouse,
        )
        self.fiscal_year = FiscalYear.objects.create(year=2099)

    def test_main_pages_render(self):
        for route_name in self.route_names:
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "{#")
                self.assertNotContains(response, "#}")

    def test_anonymous_user_is_sent_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("finance:journal_list"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('finance:journal_list')}",
        )

    def test_home_redirects_to_the_role_dashboard(self):
        response = self.client.get(reverse("core:home"))
        self.assertRedirects(response, reverse("core:admin_dashboard"))

    def test_cashier_navigation_pages_render(self):
        self.user.is_superuser = False
        self.user.is_staff = False
        self.user.role = "cashier"
        self.user.save(update_fields=["is_superuser", "is_staff", "role"])
        for route_name in ("pos:terminal", "pos:sale_list", "quotations:list"):
            with self.subTest(route_name=route_name):
                self.assertEqual(self.client.get(reverse(route_name)).status_code, 200)

    def test_arabic_pages_render_translated_rtl_interface(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"

        for route_name in self.route_names:
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'lang="ar"')
                self.assertContains(response, 'dir="rtl"')

        response = self.client.get(reverse("finance:journal_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "\u0642\u064a\u0648\u062f \u0627\u0644\u064a\u0648\u0645\u064a\u0629")
        self.assertContains(response, "\u0642\u064a\u062f \u062c\u062f\u064a\u062f")

        translated_finance_pages = {
            "finance:cash_flow_statement": "قائمة التدفقات النقدية",
            "finance:receivables_aging": "تقرير أعمار الذمم المدينة",
            "finance:payables_aging": "تقرير أعمار الذمم الدائنة",
            "finance:fiscal_period_list": "الفترات المالية",
        }
        for route_name, arabic_title in translated_finance_pages.items():
            with self.subTest(arabic_route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, arabic_title)

        fiscal_periods_response = self.client.get(reverse("finance:fiscal_period_list"))
        self.assertContains(fiscal_periods_response, "سجل الفتح والإغلاق")

        history_response = self.client.get(
            reverse("finance:fiscal_year_history", args=[self.fiscal_year.pk])
        )
        self.assertEqual(history_response.status_code, 200)
        self.assertContains(history_response, "سجل الفتح والإغلاق")
