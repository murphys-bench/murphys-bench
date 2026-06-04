from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import render, redirect
from django.views.generic import FormView
from django.contrib import messages


class LoginView(FormView):
    """Login page"""
    template_name = 'accounts/login.html'
    form_class = AuthenticationForm

    def dispatch(self, request, *args, **kwargs):
        # Already logged in? Send to dashboard
        if request.user.is_authenticated:
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        login(self.request, form.get_user())
        next_url = self.request.GET.get('next', 'core:dashboard')
        return redirect(next_url)

    def form_invalid(self, form):
        messages.error(self.request, 'Invalid username or password.')
        return super().form_invalid(form)


def logout_view(request):
    """Log out and redirect to login"""
    logout(request)
    return redirect('accounts:login')
