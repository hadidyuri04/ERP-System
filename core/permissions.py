from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import user_passes_test
from functools import wraps

def role_required(*allowed_roles):
    """
    Decorator to restrict access based on User.role field[cite: 1].
    Example: @role_required('ADMIN', 'ACCOUNTANT')
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return user_passes_test(lambda u: u.is_authenticated)(view_func)(request, *args, **kwargs)
            
            if request.user.role in allowed_roles or request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            
            raise PermissionDenied
        return _wrapped_view
    return decorator

admin_required = role_required("admin")
accountant_required = role_required("admin", "accountant")
cashier_required = role_required("admin", "accountant", "cashier")