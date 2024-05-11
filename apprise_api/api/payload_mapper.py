# -*- coding: utf-8 -*-
#
# Copyright (C) 2024 Chris Caron <lead2gold@gmail.com>
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
from api.forms import NotifyForm

# import the logging library
import logging

# Get an instance of a logger
logger = logging.getLogger('django')


def remap_fields(rules, payload, form=None):
    """
    Remaps fields in the payload provided based on the rules provided

    The key value of the dictionary identifies the payload key type you
    wish to alter.  If there is no value defined, then the entry is removed

    If there is a value provided, then it's key is swapped into the new key
    provided.

    The purpose of this function is to allow people to re-map the fields
    that are being posted to the Apprise API before hand.  Mapping them
    can allow 3rd party programs that post 'subject' and 'content' to
    be remapped to say 'title' and 'body' respectively

    """

    # Prepare our Form (identifies our expected keys)
    form = NotifyForm() if form is None else form

    # First generate our expected keys; only these can be mapped
    expected_keys = set(form.fields.keys())
    for _key, value in rules.items():

        key = _key.lower()
        if key in payload and not value:
            # Remove element
            del payload[key]
            continue

        vkey = value.lower()
        if vkey in expected_keys and key in payload:
            if key not in expected_keys or vkey not in payload:
                # replace
                payload[vkey] = payload[key]
                del payload[key]

            elif vkey in payload:
                # swap
                _tmp = payload[vkey]
                payload[vkey] = payload[key]
                payload[key] = _tmp

        elif key in expected_keys or key in payload:
            # assignment
            payload[key] = value

    return True
