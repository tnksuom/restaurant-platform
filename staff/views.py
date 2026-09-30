from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash, views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core import audit
from core.models import AuditEvent
from core.permissions import is_manager, manager_required
from timeclock.models import Shift

from . import services
from .forms import EmployeeCreateForm, EmployeeDetailsForm, LoginForm, RoleForm
from .models import Employee, Role


def _add_errors(form, exc):
    if hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, exc.messages)


class LoginView(auth_views.LoginView):
    authentication_form = LoginForm
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if settings.DEMO_MODE:
            context["demo_password"] = settings.DEMO_PASSWORD
            context["demo_accounts"] = [
                ("owner", "Olli Owner", _("Owner")),
                ("mia", "Mia Virtanen", _("Manager")),
                ("linh", "Linh Nguyen", _("Employee")),
                ("eero", "Eero Korhonen", _("Employee · on a break")),
            ]
        return context


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, _("You have signed out."))
    return redirect("login")


@login_not_required
def set_password(request, uidb64, token):
    try:
        pk = force_str(urlsafe_base64_decode(uidb64))
        employee = Employee.objects.get(pk=pk, is_active=True)
    except (ValueError, TypeError, OverflowError, Employee.DoesNotExist):
        employee = None
    if employee is None or not default_token_generator.check_token(employee, token):
        return render(request, "registration/set_password_invalid.html", status=400)
    form = SetPasswordForm(employee, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.record(employee, "employee.password_set", employee)
        messages.success(request, _("Your password is set. You can now sign in."))
        return redirect("login")
    return render(request, "registration/set_password.html", {"form": form, "employee": employee})


def _get_target(request, pk):
    return get_object_or_404(Employee, pk=pk, restaurant=request.user.restaurant)


@manager_required
def staff_list(request):
    qs = Employee.objects.filter(restaurant=request.user.restaurant).annotate(
        on_shift=Exists(Shift.objects.filter(employee=OuterRef("pk"), status=Shift.Status.OPEN))
    )
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(display_name__icontains=q) | Q(login_identifier__icontains=q))
    role = request.GET.get("role", "")
    if role in Role.values:
        qs = qs.filter(role=role)
    status = request.GET.get("status", "active")
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "staff/list.html",
        {"page": page, "q": q, "role": role, "status": status, "roles": Role.choices},
    )


@manager_required
def staff_create(request):
    roles = services.assignable_roles(request.user)
    form = EmployeeCreateForm(request.POST or None, roles=roles)
    if request.method == "POST" and form.is_valid():
        try:
            employee = services.create_employee(request.user, **form.cleaned_data)
        except ValidationError as exc:
            _add_errors(form, exc)
        else:
            link = services.issue_setup_link(request.user, employee, request)
            messages.success(request, _("%(name)s was added.") % {"name": employee.display_name})
            return render(request, "staff/setup_link.html", {"employee": employee, "link": link, "new": True})
    return render(request, "staff/form.html", {"form": form, "title": _("Add staff member")})


def staff_detail(request, pk):
    target = _get_target(request, pk)
    if not is_manager(request.user) and target.pk != request.user.pk:
        raise Http404
    open_shift = Shift.objects.filter(employee=target, status=Shift.Status.OPEN).first()
    history = None
    if is_manager(request.user):
        history = AuditEvent.objects.filter(entity_type="staff.employee", entity_id=str(target.pk)).select_related(
            "actor"
        )[:20]
    return render(
        request,
        "staff/detail.html",
        {
            "target": target,
            "can_manage": services.can_manage(request.user, target) and target.pk != request.user.pk,
            "can_edit_details": services.can_manage(request.user, target),
            "open_shift": open_shift,
            "history": history,
            "is_self": target.pk == request.user.pk,
        },
    )


def my_profile(request):
    return redirect("staff:detail", pk=request.user.pk)


@manager_required
def staff_edit(request, pk):
    target = _get_target(request, pk)
    if not services.can_manage(request.user, target):
        raise Http404
    form = EmployeeDetailsForm(
        request.POST or None, initial={"display_name": target.display_name, "phone": target.phone}
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.update_details(request.user, target, **form.cleaned_data)
        except ValidationError as exc:
            _add_errors(form, exc)
        else:
            messages.success(request, _("Details saved."))
            return redirect("staff:detail", pk=target.pk)
    return render(request, "staff/form.html", {"form": form, "title": _("Edit %(name)s") % {"name": target}})


@manager_required
def staff_role(request, pk):
    target = _get_target(request, pk)
    if not services.can_manage(request.user, target):
        raise Http404
    form = RoleForm(request.POST or None, roles=services.assignable_roles(request.user), initial={"role": target.role})
    if request.method == "POST" and form.is_valid():
        try:
            services.change_role(request.user, target, form.cleaned_data["role"])
        except ValidationError as exc:
            _add_errors(form, exc)
        else:
            messages.success(request, _("Role updated."))
            return redirect("staff:detail", pk=target.pk)
    return render(
        request, "staff/form.html", {"form": form, "title": _("Change role for %(name)s") % {"name": target}}
    )


@manager_required
def staff_deactivate(request, pk):
    target = _get_target(request, pk)
    if not services.can_manage(request.user, target):
        raise Http404
    error = None
    if request.method == "POST":
        try:
            services.deactivate(request.user, target)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, _("%(name)s can no longer sign in.") % {"name": target.display_name})
            return redirect("staff:detail", pk=target.pk)
    open_shift = Shift.objects.filter(employee=target, status=Shift.Status.OPEN).first()
    return render(request, "staff/deactivate.html", {"target": target, "error": error, "open_shift": open_shift})


@manager_required
@require_POST
def staff_reactivate(request, pk):
    target = _get_target(request, pk)
    if not services.can_manage(request.user, target):
        raise Http404
    services.reactivate(request.user, target)
    messages.success(request, _("%(name)s is active again.") % {"name": target.display_name})
    return redirect("staff:detail", pk=target.pk)


@manager_required
@require_POST
def staff_setup_link(request, pk):
    target = _get_target(request, pk)
    if not services.can_manage(request.user, target) or not target.is_active:
        raise Http404
    link = services.issue_setup_link(request.user, target, request)
    return render(request, "staff/setup_link.html", {"employee": target, "link": link, "new": False})


def change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        audit.record(user, "employee.password_changed", user)
        messages.success(request, _("Your password was changed."))
        return redirect("staff:me")
    return render(request, "staff/form.html", {"form": form, "title": _("Change my password")})
