from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.db.utils import DatabaseError
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from communications.models import (
    Announcement,
    Channel,
    ChannelMembership,
    Meeting,
    MeetingEvent,
    MeetingParticipant,
    Message,
    Notification,
)
from communications.notifications import notification_service
from organization.models import Branch, Department, Organization

from .forms import (
    AnnouncementForm,
    BranchForm,
    ChannelForm,
    DepartmentForm,
    EmployeeCreateForm,
    EmployeeEditForm,
    MeetingForm,
    RoleForm,
)
from .models import AuditEvent, PermissionCode, Role, RolePermission, UserRole
from .services import PERMISSIONS, organization_for, permission_required, record_audit

User = get_user_model()


def _render_form(request, template, title, form, **context):
    return render(request, template, {"title": title, "form": form, **context})


@login_required
def manage_dashboard(request):
    organization = organization_for(request.user)
    if not request.user.can_manage_workplace:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    active_meetings = Meeting.objects.filter(
        organization=organization, status=Meeting.Status.ACTIVE
    ).count()
    context = {
        "employee_count": User.objects.filter(organization=organization, is_active=True).count(),
        "suspended_count": User.objects.filter(organization=organization, is_suspended=True).count(),
        "branch_count": organization.branches.filter(is_active=True).count(),
        "department_count": organization.departments.filter(is_active=True).count(),
        "active_meetings": active_meetings,
        "scheduled_meetings": Meeting.objects.filter(
            organization=organization, status=Meeting.Status.SCHEDULED
        ).count(),
        "message_count": Message.objects.filter(channel__organization=organization).count(),
        "recent_events": AuditEvent.objects.filter(organization=organization).select_related("actor")[:8],
    }
    return render(request, "governance/dashboard.html", context)


@login_required
@permission_required("USER_VIEW")
def employee_list(request):
    organization = organization_for(request.user)
    employees = User.objects.filter(organization=organization).select_related("branch", "department")
    query = request.GET.get("q", "").strip()
    role = request.GET.get("role", "").strip()
    if query:
        employees = employees.filter(
            Q(employee_id__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
            | Q(job_title__icontains=query)
        )
    if role in User.Role.values:
        employees = employees.filter(role=role)
    return render(
        request,
        "governance/employee_list.html",
        {"employees": employees[:200], "query": query, "roles": User.Role.choices, "selected_role": role},
    )


@login_required
@permission_required("USER_CREATE")
def employee_create(request):
    organization = organization_for(request.user)
    form = EmployeeCreateForm(request.POST or None, organization=organization)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            employee = form.save()
            record_audit(request, "employee.created", employee)
            if employee.role_assignments.exists() is False:
                default_role = Role.objects.filter(organization=organization, name="Employee").first()
                if default_role:
                    UserRole.objects.create(user=employee, role=default_role, assigned_by=request.user)
        messages.success(request, f"{employee.get_full_name() or employee.employee_id} was added.")
        return redirect("manage_employee_list")
    return _render_form(request, "governance/form_page.html", "Add employee", form)


@login_required
@permission_required("USER_UPDATE")
def employee_edit(request, user_id):
    organization = organization_for(request.user)
    employee = get_object_or_404(User, pk=user_id, organization=organization)
    form = EmployeeEditForm(request.POST or None, instance=employee, organization=organization)
    if request.method == "POST" and form.is_valid():
        form.save()
        record_audit(request, "employee.updated", employee)
        messages.success(request, "Employee details were updated.")
        return redirect("manage_employee_list")
    return _render_form(request, "governance/form_page.html", "Edit employee", form)


@login_required
@permission_required("USER_DELETE")
@require_POST
def employee_toggle_status(request, user_id):
    organization = organization_for(request.user)
    employee = get_object_or_404(User, pk=user_id, organization=organization)
    if employee.pk == request.user.pk:
        messages.error(request, "You cannot suspend or deactivate your own account.")
    elif request.POST.get("action") == "suspend":
        employee.is_suspended = not employee.is_suspended
        employee.save(update_fields=["is_suspended"])
        record_audit(
            request,
            "employee.suspended" if employee.is_suspended else "employee.reinstated",
            employee,
        )
        messages.success(request, "Employee suspension status updated.")
    else:
        employee.is_active = not employee.is_active
        employee.save(update_fields=["is_active"])
        record_audit(
            request,
            "employee.deactivated" if not employee.is_active else "employee.activated",
            employee,
        )
        messages.success(request, "Employee account status updated.")
    return redirect("manage_employee_list")


@login_required
@permission_required("BRANCH_VIEW")
def branch_list(request):
    organization = organization_for(request.user)
    form = BranchForm(request.POST or None, organization=organization)
    if request.method == "POST":
        from .services import require_permission
        require_permission(request.user, "BRANCH_CREATE")
        if form.is_valid():
            branch = form.save(commit=False)
            branch.organization = organization
            branch.save()
            record_audit(request, "branch.created", branch)
            messages.success(request, "Branch created.")
            return redirect("manage_branch_list")
    branches = organization.branches.annotate(employee_count=Count("employees")).all()
    return render(request, "governance/branch_list.html", {"branches": branches, "form": form})


@login_required
@permission_required("BRANCH_DELETE")
@require_POST
def branch_toggle(request, branch_id):
    organization = organization_for(request.user)
    branch = get_object_or_404(Branch, pk=branch_id, organization=organization)
    branch.is_active = not branch.is_active
    branch.save(update_fields=["is_active"])
    record_audit(request, "branch.status_changed", branch, metadata={"is_active": branch.is_active})
    return redirect("manage_branch_list")


@login_required
@permission_required("DEPARTMENT_VIEW")
def department_list(request):
    organization = organization_for(request.user)
    form = DepartmentForm(request.POST or None)
    if request.method == "POST":
        from .services import require_permission
        require_permission(request.user, "DEPARTMENT_CREATE")
        if form.is_valid():
            department = form.save(commit=False)
            department.organization = organization
            department.save()
            record_audit(request, "department.created", department)
            messages.success(request, "Department created.")
            return redirect("manage_department_list")
    departments = organization.departments.annotate(employee_count=Count("employees")).all()
    return render(request, "governance/department_list.html", {"departments": departments, "form": form})


@login_required
@permission_required("DEPARTMENT_DELETE")
@require_POST
def department_toggle(request, department_id):
    organization = organization_for(request.user)
    department = get_object_or_404(Department, pk=department_id, organization=organization)
    department.is_active = not department.is_active
    department.save(update_fields=["is_active"])
    record_audit(request, "department.status_changed", department, metadata={"is_active": department.is_active})
    return redirect("manage_department_list")


@login_required
@permission_required("CHANNEL_CREATE")
def channel_manage(request):
    organization = organization_for(request.user)
    form = ChannelForm(request.POST or None, organization=organization)
    if request.method == "POST" and form.is_valid():
        channel = form.save(commit=False)
        channel.organization = organization
        channel.created_by = request.user
        channel.save()
        form.save_m2m()
        if channel.kind == Channel.Kind.PUBLIC:
            channel.members.add(*User.objects.filter(organization=organization, is_active=True))
        record_audit(request, "channel.created", channel)
        messages.success(request, "Channel created.")
        return redirect("manage_channel_list")
    channels = Channel.objects.filter(organization=organization).annotate(member_count=Count("members"))
    return render(request, "governance/channel_manage.html", {"form": form, "channels": channels})


@login_required
@permission_required("CHANNEL_DELETE")
@require_POST
def channel_delete(request, channel_id):
    organization = organization_for(request.user)
    channel = get_object_or_404(Channel, pk=channel_id, organization=organization)
    record_audit(request, "channel.deleted", channel, metadata={"name": channel.name})
    channel.delete()
    messages.success(request, "Channel deleted.")
    return redirect("manage_channel_list")


@login_required
@permission_required("ANNOUNCEMENT_CREATE")
def announcement_create(request):
    organization = organization_for(request.user)
    form = AnnouncementForm(request.POST or None, organization=organization)
    if request.method == "POST" and form.is_valid():
        announcement = form.save(commit=False)
        announcement.organization = organization
        announcement.created_by = request.user
        announcement.save()
        recipients = User.objects.filter(organization=organization, is_active=True)
        if announcement.branch_id:
            recipients = recipients.filter(branch_id=announcement.branch_id)
        if announcement.region_id:
            recipients = recipients.filter(branch__region_id=announcement.region_id)
        if announcement.department_id:
            recipients = recipients.filter(department_id=announcement.department_id)
        if announcement.team_id:
            recipients = recipients.filter(team_id=announcement.team_id)
        if not announcement.publish_at or announcement.publish_at <= timezone.now():
            notification_service.deliver_announcement(
                announcement, recipients.iterator()
            )
        record_audit(request, "announcement.published", announcement)
        messages.success(request, "Announcement published.")
        return redirect("announcements")
    return _render_form(request, "governance/form_page.html", "Publish announcement", form)


@login_required
@permission_required("MEETING_CREATE")
def meeting_create(request):
    organization = organization_for(request.user)
    form = MeetingForm(request.POST or None, organization=organization)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            meeting = form.save(commit=False)
            meeting.organization = organization
            meeting.host = request.user
            meeting.save()
            MeetingParticipant.objects.update_or_create(
                meeting=meeting,
                user=request.user,
                defaults={"role": MeetingParticipant.Role.HOST},
            )
            invitees = form.cleaned_data["invitees"]
            MeetingParticipant.objects.bulk_create(
                [MeetingParticipant(meeting=meeting, user=user) for user in invitees if user.pk != request.user.pk],
                ignore_conflicts=True,
            )
            notification_service.create_many(
                invitees,
                f"Meeting invitation: {meeting.title}",
                f"You're invited for {timezone.localtime(meeting.starts_at):%b %d at %I:%M %p}.",
                f"/meetings/{meeting.pk}/",
            )
            MeetingEvent.objects.create(meeting=meeting, actor=request.user, event_type="scheduled")
            record_audit(request, "meeting.created", meeting)
        messages.success(request, "Meeting scheduled and invitations sent.")
        return redirect("meeting_detail", meeting_id=meeting.pk)
    return _render_form(request, "governance/form_page.html", "Schedule a meeting", form)


@login_required
@permission_required("AUDIT_LOG_VIEW")
def audit_log(request):
    organization = organization_for(request.user)
    events = AuditEvent.objects.filter(organization=organization).select_related("actor")
    action = request.GET.get("action", "").strip()
    if action:
        events = events.filter(action__icontains=action)
    return render(request, "governance/audit_log.html", {"events": events[:250], "action": action})


@login_required
@permission_required("ROLE_MANAGE")
def role_list(request):
    organization = organization_for(request.user)
    form = RoleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        role = form.save(commit=False)
        role.organization = organization
        role.save()
        permission_ids = request.POST.getlist("permissions")
        valid_ids = PermissionCode.objects.filter(pk__in=permission_ids).values_list("pk", flat=True)
        RolePermission.objects.bulk_create(
            [RolePermission(role=role, permission_id=permission_id) for permission_id in valid_ids],
            ignore_conflicts=True,
        )
        record_audit(request, "role.created", role)
        messages.success(request, "Role created.")
        return redirect("manage_role_list")
    roles = Role.objects.filter(organization=organization).prefetch_related("permissions", "users")
    return render(
        request,
        "governance/role_list.html",
        {"roles": roles, "form": form, "permission_groups": _permission_groups()},
    )


def _permission_groups():
    groups = {}
    for permission in PermissionCode.objects.exclude(code="PLATFORM_ADMIN"):
        groups.setdefault(permission.module, []).append(permission)
    return groups


@login_required
@permission_required("ROLE_MANAGE")
@require_POST
def role_assign(request, role_id, user_id):
    organization = organization_for(request.user)
    role = get_object_or_404(Role, pk=role_id, organization=organization)
    employee = get_object_or_404(User, pk=user_id, organization=organization)
    assignment, created = UserRole.objects.get_or_create(
        user=employee, role=role, defaults={"assigned_by": request.user}
    )
    if not created:
        assignment.delete()
    record_audit(
        request,
        "role.assigned" if created else "role.unassigned",
        employee,
        metadata={"role": role.name},
    )
    return redirect("manage_employee_list")


@login_required
def global_search(request):
    organization = organization_for(request.user)
    query = request.GET.get("q", "").strip()[:100]
    people = User.objects.none()
    channels = Channel.objects.none()
    message_results = Message.objects.none()
    meetings = Meeting.objects.none()
    branches = Branch.objects.none()
    departments = Department.objects.none()
    announcements = Announcement.objects.none()
    files = None
    if query:
        people = User.objects.filter(organization=organization, is_active=True).exclude(pk=request.user.pk).filter(
            Q(employee_id__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(job_title__icontains=query)
        )[:20]
        channels = Channel.objects.filter(organization=organization).filter(
            Q(kind=Channel.Kind.PUBLIC) | Q(members=request.user)
        ).filter(Q(name__icontains=query) | Q(description__icontains=query)).distinct()[:20]
        allowed_channels = Channel.objects.filter(organization=organization).filter(
            Q(kind=Channel.Kind.PUBLIC) | Q(members=request.user)
        )
        message_results = Message.objects.filter(
            channel__in=allowed_channels,
            body__icontains=query,
            deleted_at__isnull=True,
        ).select_related("author", "channel").order_by("-created_at")[:20]
        meetings = Meeting.objects.filter(
            organization=organization,
            title__icontains=query,
        )
        if not has_permission(request.user, "MEETING_CREATE"):
            meetings = meetings.filter(Q(host=request.user) | Q(participant_records__user=request.user))
        meetings = meetings.distinct()[:20]
        branches = Branch.objects.filter(organization=organization, name__icontains=query)[:20]
        departments = Department.objects.filter(organization=organization, name__icontains=query)[:20]
        announcements = _visible_announcements(request.user, organization).filter(
            title__icontains=query
        )[:20]
        from documents.views import _visible_files
        files = _visible_files(request.user).filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        )[:20]
    return render(
        request,
        "governance/search.html",
        {
            "query": query,
            "people": people,
            "channels": channels,
            "messages": message_results,
            "meetings": meetings,
            "branches": branches,
            "departments": departments,
            "announcements": announcements,
            "files": files,
        },
    )


@login_required
@user_passes_test(lambda user: user.is_superuser)
def platform_dashboard(request):
    return render(
        request,
        "governance/platform_dashboard.html",
        {
            "organization_count": Organization.objects.count(),
            "active_organizations": Organization.objects.filter(is_active=True).count(),
            "user_count": User.objects.count(),
            "audit_count": AuditEvent.objects.count(),
            "organizations": Organization.objects.annotate(employee_count=Count("employees")),
            "recent_events": AuditEvent.objects.select_related("organization", "actor")[:12],
        },
    )


@login_required
@user_passes_test(lambda user: user.is_superuser)
@require_POST
def platform_organization_create(request):
    name = request.POST.get("name", "").strip()[:180]
    slug = request.POST.get("slug", "").strip()[:200]
    employee_id = request.POST.get("admin_employee_id", "").strip().upper()[:32]
    password = request.POST.get("admin_password", "")
    first_name = request.POST.get("admin_first_name", "").strip()[:150]
    last_name = request.POST.get("admin_last_name", "").strip()[:150]
    if not name or not slug or not employee_id or not password:
        messages.error(request, "Organization details and the first employer's ID and password are required.")
        return redirect("platform_dashboard")
    try:
        validate_password(password)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("platform_dashboard")
    if User.objects.filter(employee_id=employee_id).exists():
        messages.error(request, "That employee ID is already in use.")
        return redirect("platform_dashboard")
    try:
        with transaction.atomic():
            organization = Organization.objects.create(name=name, slug=slug)
            admin_user = User.objects.create_user(
                employee_id=employee_id,
                password=password,
                organization=organization,
                first_name=first_name,
                last_name=last_name,
                role=User.Role.EMPLOYER,
                is_staff=True,
            )
            AuditEvent.objects.create(
                actor=request.user,
                organization=organization,
                action="organization.created",
                resource_type="Organization",
                resource_id=str(organization.pk),
            )
            AuditEvent.objects.create(
                actor=request.user,
                organization=organization,
                action="organization_admin.created",
                resource_type="User",
                resource_id=str(admin_user.pk),
            )
    except IntegrityError:
        messages.error(request, "That organization name or slug is already in use.")
        return redirect("platform_dashboard")
    messages.success(request, f"{organization.name} and its first employer account were created.")
    return redirect("platform_dashboard")


@login_required
@user_passes_test(lambda user: user.is_superuser)
@require_POST
def platform_organization_toggle(request, organization_id):
    organization = get_object_or_404(Organization, pk=organization_id)
    organization.is_active = not organization.is_active
    organization.save(update_fields=["is_active"])
    AuditEvent.objects.create(
        actor=request.user,
        organization=organization,
        action="organization.suspended" if not organization.is_active else "organization.reactivated",
        resource_type="Organization",
        resource_id=str(organization.pk),
    )
    return redirect("platform_dashboard")


def health(request):
    return JsonResponse({"status": "ok", "service": "NexaWork"})


def readiness(request):
    from django.db import connection
    try:
        connection.ensure_connection()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready", "database": "connected"})
