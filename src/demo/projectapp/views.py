from django.core.urlresolvers import reverse
from django.contrib import messages
from django.views.generic import CreateView, DeleteView
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django import forms
from django.dispatch import receiver

from verification.signals import key_claimed
from verification.models import KeyGroup, Key
from verification.views import AbstractClaimOnPostFormView

activate_account, _ = KeyGroup.objects.get_or_create(
    name='activate_account',
    generator='md5-hex',
    ttl=60)

def send_verification_email(recipient, content):
    "send_func to be used by verification.Key"
    subject = 'Activate demo-account for django-verification'
    recipient = ''.join(recipient.strip().split())
    send_mail(subject, content, 'noreply@example.com', [recipient])


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['email']

class PasswordForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput)
    repeat_password = forms.CharField(widget=forms.PasswordInput)

    def clean(self):
        cleaned_data = super(PasswordForm, self).clean()
        if cleaned_data['password'] == cleaned_data['repeat_password']:
            return cleaned_data
        raise forms.ValidationError('The passwords didn\'t match')

class DeleteUser(DeleteView):
    model = User
    success_url = '/'

    def get_object(self, queryset=None):
        if self.request.user.is_authenticated():
            return self.request.user
        return None

    def delete(self, request, *args, **kwargs):
        next = super(DeleteUser, self).delete(request, *args, **kwargs)
        messages.info(self.request, "You have successfully deleted <b>%s</b>" % self.object.email)
        return next

delete_user = DeleteUser.as_view()

class AbstractCreateUser(CreateView):
    model = User
    form_class = UserForm
    success_url = '/'
    create_url = 'login'
    activate_url = None
    message = ''
    disclaimer = '''You have received this email because you, or someone who
knows your email-address, attempted to create a user
at {create_url} on the
{creation_timestamp}.

If this was not you you can ignore this email.'''
    email = None

    def set_message(self, **kwargs):
        """Use messages.info(self.request, self.message % kwargs)"""
        raise NotImplemented

    def generate_activate_url(self, **kwargs):
        if not kwargs:
            kwargs={}
        path = reverse(self.activate_url, kwargs=kwargs)
        return self.request.build_absolute_uri(path)

    def generate_create_url(self, **kwargs):
        if not kwargs:
            kwargs={}
        path = reverse(self.create_url, kwargs=kwargs)
        return self.request.build_absolute_uri(path)

    def send_key(self):
        assert self.create_url and self.activate_url, (self.create_url, self.activate_url)
        mail_body = self.disclaimer + '\n\n' + self.email
        key = self.key
        create_url = self.generate_create_url()
        activate_url = self.generate_activate_url(key=key.key, group=key.group.name)
        key.send_key(self.object.email, mail_body.format(
                create_url=create_url,
                creation_timestamp=self.object.date_joined,
                activate_url=activate_url,
                key=self.key.key,
            )
        )

    def form_valid(self, form):
        email = form.cleaned_data['email']
        form.cleaned_data['username'] = email
        next = super(AbstractCreateUser, self).form_valid(form)
        self.object.is_active = False
        self.object.username = email
        self.object.save()
        key = Key.generate(activate_account)
        key.send_func = send_verification_email
        key.claimed_by = self.object
        key.save()
        self.key = key
        self.send_key()
        self.set_message(email=email)
        return next

class CreateUserOnGet(AbstractCreateUser):
    message = '''You will shortly receive an email from
    <b>noreply@example.com</b> to the address you gave, which was <b>%s</b>. It
    will contain a link. Click on the link to activate the account. You can
    then <a href="">log in with your email-address as username</a>. The
    password is "demo".'''
    email = '''You can complete the process by clicking on {activate_url}. The link times out in one hour'''
    activate_url = 'verification-claim-get'

    def set_message(self, **kwargs):
        assert 'email' in kwargs
        email = kwargs['email']
        messages.info(self.request, self.message % email)

    def form_valid(self, form):
        next = super(CreateUserOnGet, self).form_valid(form)
        password = 'demo'
        self.object.set_password(password)
        self.object.save()
        return next
create_user_get = CreateUserOnGet.as_view()

class CreateUserOnPost(AbstractCreateUser):
    message = '''You will shortly receive an email from
    <b>noreply@example.com</b> to the address you gave, which was <b>%s</b>. It
    will contain a link. Click on the link, then click on the
    <b>Activate</b>-button to activate the account. You can then <a href="">log
    in with your email-address as username</a>. The password is "demo".'''
    activate_url = 'verification-claim-post-url'
    email = '''You can complete the process by clicking on {activate_url}. The link times out in one hour'''

    def set_message(self, **kwargs):
        assert 'email' in kwargs
        email = kwargs['email']
        messages.info(self.request, self.message % email)

    def form_valid(self, form):
        next = super(CreateUserOnPost, self).form_valid(form)
        password = 'demo'
        self.object.set_password(password)
        self.object.save()
        return next
create_user_post = CreateUserOnPost.as_view()

class CreateUserOnPostWPassword(AbstractCreateUser):
    message = '''You will shortly receive an email from
    <b>noreply@example.com</b> to the address you gave, which was <b>%s</b>. It
    will contain a link. Click on the link, fill in the password and click on
    the <b>Activate</b>-button to activate the account. You can then <a
    href="">log in with your email-address as username</a>. The password is
    "demo".'''
    activate_url = 'demo-claim-postform'
    email = '''You can complete the process by clicking on {activate_url}. The link times out in one hour'''

    def set_message(self, **kwargs):
        assert 'email' in kwargs
        email = kwargs['email']
        messages.info(self.request, self.message % email)
create_user_post_password = CreateUserOnPostWPassword.as_view()

class ClaimOnPostFormView(AbstractClaimOnPostFormView):
    form_class = PasswordForm

    def form_valid(self, form):
        next = super(ClaimOnPostFormView, self).form_valid(form)
        password = form.cleaned_data['password']
        user = self.key.claimed_by
        user.set_password(password)
        # The signal makes the user active so no need to do it here
        user.save()
        return next
claim_post_form = ClaimOnPostFormView.as_view()

#-- signals

@receiver(key_claimed)
def user_created_key_claimed(sender, **kwargs):
    claimant = kwargs['claimant']
    group = kwargs['group']
    if group.name == 'activate_account':
        claimant.is_active = True
        claimant.save()
