from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class ConnectedViewsSmokeTests(TestCase):
    """Keep the main navigation and create pages renderable as they evolve."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="view-admin",
            password="test-password",
            email="admin@example.com",
        )
        self.client.force_login(self.user)

    def test_main_pages_render(self):
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
            "pos:terminal",
            "pos:sale_list",
            "quotations:list",
            "quotations:create",
        )

        for route_name in route_names:
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
