from django.contrib import admin

from .models import Branch, Department, JobRole, Organization, Region, Team

admin.site.register([Organization, Region, Branch, Department, Team, JobRole])
