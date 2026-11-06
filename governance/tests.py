from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from organization.models import Organization

from .models import AuditEvent, PermissionCode, Role, RolePermission, UserRole


class OrganizationManagementTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Nexa Labs", slug="nexa-labs")
        self.other_organization = Organization.objects.create(name="Other Org", slug="other-org")
        self.admin = User.objects.create_user(
            employee_id="NW-ADMIN",
            password="SecurePassword!123",
            organization=self.organization,
            role=User.Role.EMPLOYER,
            is_staff=True,
        )
        self.employee = User.objects.create_user(
            employee_id="NW-EMP",
            password="SecurePassword!123",
            organization=self.organization,
        )
        self.other_employee = User.objects.create_user(
            employee_id="OTHER-EMP",
            password="SecurePassword!123",
            organization=self.other_organization,
        )

    def test_admin_dashboard_and_employee_search_are_tenant_scoped(self):
        AuditEvent.objects.create(
            organization=self.organization,
            actor=None,
            action="system.event",
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("manage_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["employee_count"], 2)
        self.assertContains(response, "System")

        response = self.client.get(reverse("manage_employee_list"), {"q": "EMP"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.employee.employee_id)
        self.assertNotContains(response, self.other_employee.employee_id)

    def test_audit_log_displays_events_without_an_actor(self):
        AuditEvent.objects.create(
            organization=self.organization,
            actor=None,
            action="system.event",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("manage_audit_log"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System")

    def test_standard_employee_cannot_open_management_dashboard(self):
        self.client.force_login(self.employee)
        response = self.client.get(reverse("manage_employee_list"))
        self.assertEqual(response.status_code, 403)

    def test_custom_rbac_role_grants_only_its_configured_permission(self):
        view_permission = PermissionCode.objects.create(
            code="USER_VIEW", label="View employees", module="employees"
        )
        report_permission = PermissionCode.objects.create(
            code="REPORT_VIEW", label="View reports", module="reports"
        )
        role = Role.objects.create(organization=self.organization, name="Directory viewer")
        RolePermission.objects.create(role=role, permission=view_permission)
        UserRole.objects.create(user=self.employee, role=role)
        self.assertTrue(self.employee.has_perm("governance.USER_VIEW"))

        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(reverse("manage_employee_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("manage_audit_log")).status_code, 403)
        self.assertFalse(role.permissions.filter(pk=report_permission.pk).exists())

    def test_branch_creation_is_tenant_scoped_and_audited(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("manage_branch_list"), {"name": "Lagos", "city": "Lagos"})
        self.assertRedirects(response, reverse("manage_branch_list"))
        branch = self.organization.branches.get(name="Lagos")
        self.assertEqual(branch.organization_id, self.organization.pk)
        self.assertTrue(AuditEvent.objects.filter(action="branch.created", resource_id=str(branch.pk)).exists())

    def test_suspended_organization_blocks_existing_authenticated_sessions(self):
        self.organization.is_active = False
        self.organization.save(update_fields=["is_active"])
        self.client.force_login(self.admin)
        response = self.client.get(reverse("manage_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_platform_admin_creates_tenant_with_initial_employer(self):
        platform_admin = User.objects.create_superuser(
            employee_id="NW-ROOT", password="AnotherSecurePassword!123"
        )
        self.client.force_login(platform_admin)
        response = self.client.post(
            reverse("platform_organization_create"),
            {
                "name": "Nexa Engineering Ltd",
                "slug": "nexa-engineering",
                "admin_employee_id": "NX-OWNER",
                "admin_first_name": "Ari",
                "admin_last_name": "Owner",
                "admin_password": "DistinctSecretPhrase!984",
            },
        )
        self.assertRedirects(response, reverse("platform_dashboard"))
        organization = Organization.objects.get(slug="nexa-engineering")
        employer = User.objects.get(employee_id="NX-OWNER")
        self.assertEqual(employer.organization_id, organization.pk)
        self.assertTrue(employer.is_staff)
        self.assertTrue(AuditEvent.objects.filter(action="organization.created", organization=organization).exists())
