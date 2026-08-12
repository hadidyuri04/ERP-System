from django.db.models.signals import post_migrate
from django.dispatch import receiver
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

@receiver(post_migrate)
def setup_default_role_permissions(sender, **kwargs):
    # Ensure this only runs once per migration cycle, specifically tied to the 'accounts' app
    if sender.name != 'accounts':
        return

    # 1. Cashier Group Permissions
    cashier_group, _ = Group.objects.get_or_create(name='Cashiers')
    cashier_codenames = [
        'add_possale', 'view_possale', 'add_possaleitem', 'view_possaleitem',
        'view_customer', 'add_customer',
        'add_quotation', 'view_quotation', 'change_quotation'
    ]
    cashier_perms = Permission.objects.filter(codename__in=cashier_codenames)
    cashier_group.permissions.set(cashier_perms)

    # 2. Accountant Group Permissions
    accountant_group, _ = Group.objects.get_or_create(name='Accountants')
    accountant_codenames = [
        'view_account', 'add_account',
        'add_journalentry', 'view_journalentry', 'change_journalentry',
        'add_receiptvoucher', 'view_receiptvoucher', 'change_receiptvoucher',
        'add_paymentvoucher', 'view_paymentvoucher', 'change_paymentvoucher',
        'view_purchaseinvoice', 'add_purchaseinvoice',
        'view_supplier', 'add_supplier', 'change_supplier',
        'view_customer', 'add_customer', 'change_customer',
        'view_stockbalance', 'view_stockmovement'
    ]
    accountant_perms = Permission.objects.filter(codename__in=accountant_codenames)
    accountant_group.permissions.set(accountant_perms)

    # 3. Administrator Group Permissions (Full Access)
    admin_group, _ = Group.objects.get_or_create(name='Administrators')
    admin_group.permissions.set(Permission.objects.all())