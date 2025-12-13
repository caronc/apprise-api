#
# Copyright (C) 2025 Chris Caron <lead2gold@gmail.com>
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

from django import forms
from django.utils.translation import gettext_lazy as _

import apprise

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

# Define our potential input text categories
INPUT_FORMATS = (
    (apprise.NotifyFormat.TEXT.value, _("TEXT")),
    (apprise.NotifyFormat.MARKDOWN.value, _("MARKDOWN")),
    (apprise.NotifyFormat.HTML.value, _("HTML")),
    # As-is - do not interpret it
    (None, _("IGNORE")),
)

URLS_MAX_LEN = 1024
URLS_PLACEHOLDER = "mailto://user:pass@domain.com, slack://tokena/tokenb/tokenc, ..."


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
        max_length=4096,
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
        """
        We just ensure there is a format always set
        """
        data = self.cleaned_data["format"]
        if not data:
            # Always set a type
            data = apprise.NotifyFormat.TEXT.value
        return data


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
