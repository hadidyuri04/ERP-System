from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Supplier

User = get_user_model()


class SupplierModelTests(TestCase):
    def test_code_must_be_unique(self):
        Supplier.objects.create(code="S1", name="Jordan Dairy")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Supplier.objects.create(code="S1", name="Another Supplier")

    def test_str_shows_code_and_name(self):
        supplier = Supplier.objects.create(code="S1", name="Jordan Dairy")

        self.assertIn("S1", str(supplier))
        self.assertIn("Jordan Dairy", str(supplier))

    def test_a_new_supplier_is_active_by_default(self):
        supplier = Supplier.objects.create(code="S1", name="Jordan Dairy")

        self.assertTrue(supplier.is_active)

    def test_field_labels_are_translatable(self):
        """These were plain field names until verbose_name was added."""
        supplier = Supplier()

        self.assertEqual(str(supplier._meta.get_field("code").verbose_name), "Code")
        self.assertEqual(str(supplier._meta.get_field("name").verbose_name), "Name")

    def test_opening_balance_defaults_to_zero(self):
        supplier = Supplier.objects.create(code="S2", name="Amman Trading")

        self.assertEqual(supplier.opening_balance, Decimal("0"))


class SupplierViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="staff", email="s@example.com", password="x"
        )
        self.client.force_login(self.user)

    def test_list_page_renders(self):
        self.assertEqual(self.client.get("/suppliers/").status_code, 200)

    def test_create_page_renders(self):
        self.assertEqual(self.client.get("/suppliers/create/").status_code, 200)

    def test_a_supplier_can_be_created_through_the_form(self):
        response = self.client.post("/suppliers/create/", {
            "code": "S9",
            "name": "New Supplier",
            "phone": "0790000000",
            "email": "",
            "address": "",
            "tax_number": "",
            "opening_balance": "0",
            "notes": "",
            "is_active": "on",
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Supplier.objects.filter(code="S9").exists())

    def test_edit_page_renders(self):
        supplier = Supplier.objects.create(code="S1", name="Jordan Dairy")

        self.assertEqual(
            self.client.get(f"/suppliers/{supplier.pk}/edit/").status_code, 200
        )

    def test_login_is_required(self):
        self.client.logout()
        response = self.client.get("/suppliers/")

        self.assertEqual(response.status_code, 302)
