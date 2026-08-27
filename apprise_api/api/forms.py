#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files(the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions :
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import apprise
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .auth import Authentication
from .utils import (
    CONFIG_KEY_MAX_LENGTH,
    CONFIG_KEY_PATTERN,
)

# Auto-Detect Keyword
AUTO_DETECT_CONFIG_KEYWORD = "auto"

# Define our potential configuration types
CONFIG_FORMATS = (
    (AUTO_DETECT_CONFIG_KEYWORD, _("Auto-Detect")),
    (apprise.ConfigFormat.TEXT.value, _("TEXT")),
    (apprise.ConfigFormat.YAML.value, _("YAML")),
)

NOTIFICATION_TYPES = (
    (apprise.NotifyType.INFO.value, _("Info")),
    (apprise.NotifyType.SUCCESS.value, _("Success")),
    (apprise.NotifyType.WARNING.value, _("Warning")),
    (apprise.NotifyType.FAILURE.value, _("Failure")),
)

# TEXT comes first so new users get plain-text messages by default.
INPUT_FORMATS = (
    (apprise.NotifyFormat.TEXT.value, _("TEXT")),
    (apprise.NotifyFormat.MARKDOWN.value, _("MARKDOWN")),
    (apprise.NotifyFormat.HTML.value, _("HTML")),
    # As-is - do not interpret it
    (None, _("AS-IS")),
)

URLS_MAX_LEN = 1024
URLS_PLACEHOLDER = "mailto://user:pass@domain.com, slack://tokena/tokenb/tokenc, ..."
AUTH_USERNAME_MAX_LEN = 255
AUTH_PASSWORD_MAX_LEN = 255

CONFIG_ACCESS_MODES = (
    (Authentication.ACCESS_USER, _("User-Managed")),
    (Authentication.ACCESS_LOCK, _("Configuration Locked")),
    (Authentication.ACCESS_PUBLIC, _("Public Notifications")),
)


class BrowserLoginForm(forms.Form):
    """Collect credentials for the web interface's signed login cookie."""

    username = forms.CharField(
        label=_("Username"),
        required=False,
        max_length=AUTH_USERNAME_MAX_LEN,
        widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True}),
    )
    password = forms.CharField(
        label=_("Password"),
        max_length=AUTH_PASSWORD_MAX_LEN,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    next = forms.CharField(required=False, max_length=2048, widget=forms.HiddenInput())
    key = forms.RegexField(
        regex=CONFIG_KEY_PATTERN,
        label=_("Config ID"),
        required=False,
        max_length=CONFIG_KEY_MAX_LENGTH,
        widget=forms.PasswordInput(
            render_value=True,
            attrs={"autocomplete": "off"},
        ),
    )

    def clean_username(self):
        """Keep form credentials consistent with Basic Auth parsing."""
        username = self.cleaned_data["username"]
        if ":" in username:
            raise ValidationError(_("Username cannot contain ':'"))
        return username


class ConfigKeyForm(forms.Form):
    """Validate a Config ID selected through the browser interface."""

    key = forms.RegexField(
        regex=CONFIG_KEY_PATTERN,
        max_length=CONFIG_KEY_MAX_LENGTH,
    )


class AuthForm(forms.Form):
    """Validate credentials for one protected configuration."""

    access = forms.ChoiceField(
        label=_("Access"),
        choices=CONFIG_ACCESS_MODES,
        initial=Authentication.ACCESS_USER,
        required=False,
    )

    username = forms.CharField(
        label=_("Username"),
        required=False,
        max_length=AUTH_USERNAME_MAX_LEN,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "autocapitalize": "none",
                "spellcheck": "false",
                "data-1p-ignore": "true",
                "data-bwignore": "true",
                "data-lpignore": "true",
            }
        ),
    )
    password = forms.CharField(
        label=_("Password"),
        required=False,
        max_length=AUTH_PASSWORD_MAX_LEN,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "data-1p-ignore": "true",
                "data-bwignore": "true",
                "data-lpignore": "true",
            }
        ),
    )
    current_password = forms.CharField(
        label=_("Current Password"),
        required=False,
        max_length=AUTH_PASSWORD_MAX_LEN,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "off",
                "data-1p-ignore": "true",
                "data-bwignore": "true",
                "data-lpignore": "true",
            }
        ),
    )
    password_confirm = forms.CharField(
        label=_("Confirm Password"),
        required=False,
        max_length=AUTH_PASSWORD_MAX_LEN,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "data-1p-ignore": "true",
                "data-bwignore": "true",
                "data-lpignore": "true",
            }
        ),
    )

    def __init__(
        self,
        *args,
        shared=False,
        current_username="",
        current_access=Authentication.ACCESS_USER,
        has_credentials=False,
        require_current=False,
        **kwargs,
    ):
        """Configure the extra fields used when a key user changes access."""
        super().__init__(*args, **kwargs)
        self.shared = shared
        self.current_username = current_username or ""
        self.current_access = current_access
        self.has_credentials = has_credentials
        if shared:
            # Configuration users may rotate credentials but only an admin may
            # change what those credentials are allowed to do.
            self.fields["access"].widget = forms.HiddenInput()
            self.fields["username"].widget.attrs["readonly"] = True
            self.fields["password"].label = _("New Password")
            self.fields["password_confirm"].label = _("Confirm New Password")
            self.fields["password_confirm"].required = True
            self.fields["current_password"].required = require_current

    def clean_username(self):
        """Reject Basic Auth separators and changes by key users."""
        username = self.cleaned_data["username"]
        if ":" in username:
            raise ValidationError(_("Username cannot contain ':'"))
        if self.shared and username != self.current_username:
            raise ValidationError(_("The username cannot be changed by a configuration user"))
        return username

    def clean_access(self):
        """Keep the saved mode when older clients omit the access field."""
        return self.cleaned_data["access"] or self.current_access

    def clean(self):
        """Validate access changes and optional credential preservation."""
        cleaned = super().clean()
        access = cleaned.get("access")
        password = cleaned.get("password")
        username = cleaned.get("username", "")

        if self.shared and access != self.current_access:
            self.add_error("access", _("Only an administrator may change configuration access"))

        if self.shared and not password:
            self.add_error("password", _("A new password is required"))

        if self.shared and cleaned.get("password") != cleaned.get("password_confirm"):
            self.add_error("password_confirm", _("The passwords do not match"))

        if not self.shared and not password:
            if not self.has_credentials and access != Authentication.ACCESS_PUBLIC:
                self.add_error("password", _("A password is required for this access mode"))
            elif username != self.current_username:
                self.add_error("username", _("Set a password when changing the username"))
        return cleaned


class AddByUrlForm(forms.Form):
    """
    Form field for adding entries simply by passing in a string
    of one or more URLs that have been deliminted by either a
    comma and/or a space.

    This content can just be directly fed straight into Apprise
    """

    urls = forms.CharField(
        label=_("URLs"),
        widget=forms.TextInput(attrs={"placeholder": URLS_PLACEHOLDER}),
        max_length=URLS_MAX_LEN,
    )


class AddByConfigForm(forms.Form):
    """
    This is the reading in of a configuration file which contains
    potential asset information (if yaml file) and tag details.
    """

    format = forms.ChoiceField(
        label=_("Format"),
        choices=CONFIG_FORMATS,
        initial=CONFIG_FORMATS[0][0],
        required=False,
    )

    config = forms.CharField(
        label=_("Configuration"),
        widget=forms.Textarea(),
        max_length=settings.APPRISE_CONFIG_MAX_LENGTH,
        required=False,
    )

    def clean_format(self):
        """
        We just ensure there is a format always set and it defaults to auto
        """
        data = self.cleaned_data["format"]
        if not data:
            # Set to auto
            data = CONFIG_FORMATS[0][0]
        return data


class MoveConfigForm(forms.Form):
    """Validate the source and destination IDs used by a configuration move."""

    source = forms.RegexField(
        regex=CONFIG_KEY_PATTERN,
        label=_("From"),
        # Config IDs are secrets, so both fields begin concealed.
        widget=forms.PasswordInput(
            render_value=True,
            attrs={"placeholder": _("Current Configuration ID"), "autocomplete": "off"},
        ),
        max_length=CONFIG_KEY_MAX_LENGTH,
        required=True,
    )

    to = forms.RegexField(
        regex=CONFIG_KEY_PATTERN,
        label=_("To"),
        widget=forms.PasswordInput(
            render_value=True,
            attrs={"placeholder": _("New Configuration ID"), "autocomplete": "off"},
        ),
        max_length=CONFIG_KEY_MAX_LENGTH,
        required=True,
    )

    def __init__(self, *args, restricted=False, current_from="", **kwargs):
        """Configure whether the source Config ID can be edited."""
        super().__init__(*args, **kwargs)
        self.restricted = restricted
        self.current_from = current_from or ""
        # Python reserves ``from``, so expose that form name after setup.
        from_field = self.fields.pop("source")
        from_field.widget.attrs["readonly"] = restricted
        self.fields = {"from": from_field, **self.fields}

    def clean(self):
        """Reject a move that doesn't actually go anywhere."""
        cleaned_data = super().clean()
        from_config_id = cleaned_data.get("from")
        to_config_id = cleaned_data.get("to")
        if self.restricted and from_config_id != self.current_from:
            self.add_error("from", _("The configuration ID cannot be changed by a restricted user"))
        if from_config_id and to_config_id and from_config_id == to_config_id:
            raise ValidationError(_("The destination configuration ID must differ from the source"))
        return cleaned_data


class NotifyForm(forms.Form):
    """
    This is the reading in of a configuration file which contains
    potential asset information (if yaml file) and tag details.
    """

    format = forms.ChoiceField(
        label=_("Process As"),
        initial=INPUT_FORMATS[0][0],
        choices=INPUT_FORMATS,
        required=False,
    )

    type = forms.ChoiceField(
        label=_("Type"),
        choices=NOTIFICATION_TYPES,
        initial=NOTIFICATION_TYPES[0][0],
        required=False,
    )

    title = forms.CharField(
        label=_("Title"),
        widget=forms.TextInput(attrs={"placeholder": _("Optional Title")}),
        max_length=apprise.NotifyBase.title_maxlen,
        required=False,
    )

    body = forms.CharField(
        label=_("Body"),
        widget=forms.Textarea(attrs={"placeholder": _("Define your message body here...")}),
        max_length=apprise.NotifyBase.body_maxlen,
        required=False,
    )

    # Attachment Support
    attachment = forms.FileField(
        label=_("Attachment"),
        required=False,
    )

    tag = forms.CharField(
        label=_("Tags"),
        widget=forms.TextInput(attrs={"placeholder": _("Optional_Tag1, Optional_Tag2, ...")}),
        required=False,
    )

    # Allow support for tags keyword in addition to tag; the 'tag' field will
    # always take priority over this however adding `tags` gives the user more
    # flexibilty to use either/or keyword
    tags = forms.CharField(
        label=_("Tags"),
        widget=forms.HiddenInput(),
        required=False,
    )

    def clean_type(self):
        """
        We just ensure there is a type always set
        """
        data = self.cleaned_data["type"]
        if not data:
            # Always set a type
            data = apprise.NotifyType.INFO.value
        return data

    def clean_format(self):
        """Return the selected format, or ``None`` for unchanged content.

        The form always submits a choice, so the server-wide default used for
        omitted API fields does not apply here.
        """
        return self.cleaned_data["format"] or None


class NotifyByUrlForm(NotifyForm):
    """
    Same as the NotifyForm but additionally processes a string of URLs to
    notify directly.
    """

    urls = forms.CharField(
        label=_("URLs"),
        widget=forms.TextInput(attrs={"placeholder": URLS_PLACEHOLDER}),
        max_length=URLS_MAX_LEN,
        required=False,
    )
