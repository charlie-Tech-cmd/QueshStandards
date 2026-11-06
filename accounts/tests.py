from django.test import TestCase
from django.urls import reverse

from organization.models import Organization

from .models import User


class EmployeeAuthenticationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Nexa Labs", slug="nexa-labs")
        self.employee = User.objects.create_user(
            employee_id="nw-1042",
            password="SecurePassword!123",
            organization=self.organization,
            first_name="Ari",
        )
        self.employer = User.objects.create_user(
            employee_id="nw-admin",
            password="SecurePassword!456",
            organization=self.organization,
            role=User.Role.EMPLOYER,
            is_staff=True,
        )

    def test_employee_signs_in_with_employee_id(self):
        response = self.client.post(
            reverse("employee_login"),
            {"employee_id": "NW-1042", "password": "SecurePassword!123"},
        )
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.employee.pk)

    def test_employee_cannot_use_employer_sign_in(self):
        response = self.client.post(
            reverse("employer_login"),
            {"employee_id": "NW-1042", "password": "SecurePassword!123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot use this sign-in")

    def test_employer_signs_in_separately_and_can_open_dashboard(self):
        response = self.client.post(
            reverse("employer_login"),
            {"employee_id": "NW-ADMIN", "password": "SecurePassword!456"},
        )
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.employer.pk)

    def test_dashboard_redirects_signed_out_users_to_employee_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, f"{reverse('employee_login')}?next={reverse('dashboard')}")

    def test_dashboard_renders_for_an_employee(self):
        self.client.force_login(self.employee)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Good to see you, Ari")

    def test_employer_without_organization_gets_setup_instructions(self):
        new_employer = User.objects.create_superuser(
            employee_id="nw-root", password="SecurePassword!789"
        )
        self.client.force_login(new_employer)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create your organization")

    def test_employee_admin_add_form_uses_employee_id(self):
        admin = User.objects.create_superuser(employee_id="nw-root", password="SecurePassword!789")
        self.client.force_login(admin)
        response = self.client.get("/admin/accounts/user/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="employee_id"')

    def test_admin_can_create_an_employer_account(self):
        admin = User.objects.create_superuser(employee_id="nw-root", password="SecurePassword!789")
        self.client.force_login(admin)
        response = self.client.post(
            "/admin/accounts/user/add/",
            {
                "employee_id": "nw-employer",
                "password1": "StrongPassword!789",
                "password2": "StrongPassword!789",
                "first_name": "Workplace",
                "last_name": "Owner",
                "email": "",
                "organization": self.organization.pk,
                "role": User.Role.EMPLOYER,
                "is_staff": "on",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        employer = User.objects.get(employee_id="NW-EMPLOYER")
        self.assertTrue(employer.is_staff)
        self.assertEqual(employer.role, User.Role.EMPLOYER)
