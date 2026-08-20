from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Customer

User = get_user_model()


class CustomerModelTests(TestCase):
    def test_code_must_be_unique(self):
        Customer.objects.create(code="C1", name="Abu Ahmad")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Customer.objects.create(code="C1", name="Someone Else")

    def test_str_shows_code_and_name(self):
        customer = Customer.objects.create(code="C1", name="Abu Ahmad")

        self.assertIn("C1", str(customer))
        self.assertIn("Abu Ahmad", str(customer))

    def test_a_new_customer_is_active_by_default(self):
        customer = Customer.objects.create(code="C1", name="Abu Ahmad")

        self.assertTrue(customer.is_active)

    def test_optional_fields_may_be_blank(self):
        """Only code and name are required; the rest are optional."""
        customer = Customer.objects.create(code="C2", name="Walk-in")

        self.assertEqual(customer.phone, "")
        self.assertEqual(customer.credit_limit, Decimal("0"))


class CustomerViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="staff", email="s@example.com", password="x"
        )
        self.client.force_login(self.user)

    def test_list_page_renders(self):
        self.assertEqual(self.client.get("/customers/").status_code, 200)

    def test_create_page_renders(self):
        self.assertEqual(self.client.get("/customers/create/").status_code, 200)

    def test_a_customer_can_be_created_through_the_form(self):
        response = self.client.post("/customers/create/", {
            "code": "C9",
            "name": "New Customer",
            "phone": "0790000000",
            "email": "",
            "address": "",
            "tax_number": "",
            "credit_limit": "0",
            "opening_balance": "0",
            "notes": "",
            "is_active": "on",
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Customer.objects.filter(code="C9").exists())

    def test_edit_page_renders(self):
        customer = Customer.objects.create(code="C1", name="Abu Ahmad")

        self.assertEqual(
            self.client.get(f"/customers/{customer.pk}/edit/").status_code, 200
        )

    def test_login_is_required(self):
        self.client.logout()
        response = self.client.get("/customers/")

        self.assertEqual(response.status_code, 302)
