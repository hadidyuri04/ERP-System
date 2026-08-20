from .models import CompanySettings


def company(request):
    """
    Expose company settings to every template.

    Without this, templates hardcode values that are supposed to be
    configurable. The POS session screen printed "$" while the configured
    currency was JOD.
    """
    try:
        settings_row = CompanySettings.load()
    except Exception:
        # Never let a settings lookup break page rendering, for example
        # before the first migration has run.
        return {"company": None, "currency": "JOD"}

    return {
        "company": settings_row,
        "currency": settings_row.currency,
    }
